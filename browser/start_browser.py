#!/usr/bin/env python3
import asyncio
import aiohttp
import uvicorn
import uuid
import websockets
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from playwright.async_api import async_playwright
from contextlib import asynccontextmanager
import logging
from typing import Optional
from websockets.exceptions import ConnectionClosed, WebSocketException
import json

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global variables
http_session = None
browser_manager = {}
current_port = 9222
PUBLIC_IP = "43.134.*.*"  # Replace with your public IP

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    global http_session
    http_session = aiohttp.ClientSession()
    yield
    # Shutdown
    if http_session:
        await http_session.close()
    
    # Clean up browsers on shutdown
    for browser_id, browser_info in browser_manager.items():
        try:
            if browser_info.get('browser'):
                await browser_info['browser'].close()
            if browser_info.get('playwright'):
                await browser_info['playwright'].stop()
        except Exception as e:
            logger.error(f"Error cleaning up browser {browser_id}: {e}")

app = FastAPI(lifespan=lifespan)

@app.post("/browsers")
async def create_browser():
    """Create a new browser instance."""
    global current_port
    current_port += 1
    port = current_port
    browser_id = str(uuid.uuid4())
    
    try:
        playwright = await async_playwright().start()
        
        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                f'--remote-debugging-port={port}',
                '--remote-allow-origins=*',
                '--remote-debugging-address=0.0.0.0',
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-web-security',
                '--disable-features=VizDisplayCompositor'
            ],
        )
        
        # Wait a bit for Chrome to start up
        await asyncio.sleep(1)
        
        # Get CDP URL
        if not http_session:
            raise HTTPException(status_code=500, detail="HTTP session not initialized")
        
        # Retry connection to Chrome CDP
        max_retries = 5
        for attempt in range(max_retries):
            try:
                async with http_session.get(f"http://127.0.0.1:{port}/json/version") as response:
                    if response.status == 200:
                        version_info = await response.json()
                        cdp_url = version_info.get('webSocketDebuggerUrl', '')
                        if cdp_url:
                            # Extract browser WebSocket ID
                            browser_ws_id = cdp_url.split('/')[-1]
                            public_cdp_url = f"ws://{PUBLIC_IP}:8000/browsers/{browser_id}/devtools/browser/{browser_ws_id}"
                            
                            # Store browser info with ID as key
                            browser_manager[browser_id] = {
                                'id': browser_id,
                                'browser': browser,
                                'playwright': playwright,
                                'cdp_url': public_cdp_url,
                                'local_cdp_url': cdp_url,
                                'port': port,
                                'status': 'ready',
                                'ws_id': browser_ws_id
                            }
                            
                            return {
                                "id": browser_id,
                                "port": port,
                                "cdp_url": public_cdp_url,
                                "status": "ready"
                            }
                        else:
                            raise Exception("No webSocketDebuggerUrl in response")
                    else:
                        raise Exception(f"HTTP {response.status}")
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)
                else:
                    raise
        
        # If we get here, all retries failed
        await browser.close()
        await playwright.stop()
        raise HTTPException(status_code=500, detail="Failed to connect to Chrome CDP after retries")
                
    except Exception as e:
        logger.error(f"Error creating browser: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/browsers/{browser_id}")
async def get_browser(browser_id: str):
    """Get information about a specific browser."""
    if browser_id not in browser_manager:
        raise HTTPException(status_code=404, detail="Browser not found")
    
    browser_info = browser_manager[browser_id]
    
    return {
        "id": browser_info['id'],
        "port": browser_info['port'],
        "cdp_url": browser_info['cdp_url'],
        "status": browser_info['status']
    }

@app.delete("/browsers/{browser_id}")
async def delete_browser(browser_id: str):
    """Delete a browser instance."""
    if browser_id not in browser_manager:
        raise HTTPException(status_code=404, detail="Browser not found")
    
    browser_info = browser_manager[browser_id]
    
    try:
        if browser_info.get('browser'):
            await browser_info['browser'].close()
        if browser_info.get('playwright'):
            await browser_info['playwright'].stop()
        
        del browser_manager[browser_id]
        return {"message": "Browser deleted successfully"}
    except Exception as e:
        logger.error(f"Error deleting browser: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# WebSocket configuration
MAX_MESSAGE_SIZE = 50 * 1024 * 1024  # 50MB
CHUNK_SIZE = 1024 * 1024  # 1MB chunks
PING_INTERVAL = 30  # seconds
PING_TIMEOUT = 10  # seconds

@app.websocket("/browsers/{browser_id}/devtools/browser/{ws_id}")
async def websocket_proxy(websocket: WebSocket, browser_id: str, ws_id: str):
    """Enhanced WebSocket proxy to forward external connections to local Chrome CDP."""
    logger.info(f"WebSocket connection attempt for browser {browser_id}, ws_id {ws_id}")
    
    if browser_id not in browser_manager:
        logger.error(f"Browser {browser_id} not found")
        await websocket.close(code=4004, reason="Browser not found")
        return
    
    browser_info = browser_manager[browser_id]
    
    # Verify the WebSocket ID matches
    if browser_info.get('ws_id') != ws_id:
        logger.error(f"WebSocket ID mismatch: expected {browser_info.get('ws_id')}, got {ws_id}")
        await websocket.close(code=4004, reason="WebSocket ID mismatch")
        return
    
    local_ws_url = f"ws://127.0.0.1:{browser_info['port']}/devtools/browser/{ws_id}"
    
    try:
        # Accept connection
        await websocket.accept()
        logger.info(f"WebSocket connection accepted for {browser_id}")
        
        chrome_ws = None
        
        # Connect to local Chrome WebSocket with custom settings
        chrome_ws = await websockets.connect(
            local_ws_url,
            max_size=MAX_MESSAGE_SIZE,
            ping_interval=PING_INTERVAL,
            ping_timeout=PING_TIMEOUT,
            close_timeout=10
        )
        
        logger.info(f"Connected to Chrome CDP at {local_ws_url}")
        
        # Connection status tracking
        connection_active = True
        
        async def forward_to_chrome():
            """Forward messages from client to Chrome."""
            nonlocal connection_active
            try:
                while connection_active:
                    try:
                        # Receive with timeout to allow for graceful shutdown
                        data = await asyncio.wait_for(websocket.receive(), timeout=1.0)
                        
                        if "text" in data:
                            message = data["text"]
                            # Check message size
                            if len(message.encode('utf-8')) > MAX_MESSAGE_SIZE:
                                logger.warning(f"Outgoing message too large: {len(message)} bytes")
                                continue
                            await chrome_ws.send(message)
                            logger.debug(f"Forwarded text message to Chrome: {message[:100]}...")
                        elif "bytes" in data:
                            message = data["bytes"]
                            if len(message) > MAX_MESSAGE_SIZE:
                                logger.warning(f"Outgoing binary message too large: {len(message)} bytes")
                                continue
                            await chrome_ws.send(message)
                            logger.debug(f"Forwarded binary message to Chrome: {len(message)} bytes")
                            
                    except asyncio.TimeoutError:
                        # Check if connection is still alive
                        if websocket.client_state != websocket.client_state.CONNECTED:
                            connection_active = False
                            break
                        continue
                        
            except WebSocketDisconnect:
                logger.info("Client disconnected from proxy")
                connection_active = False
            except ConnectionClosed:
                logger.info("Chrome connection closed during forward to chrome")
                connection_active = False
            except Exception as e:
                logger.error(f"Error forwarding to Chrome: {e}")
                connection_active = False
        
        async def forward_to_client():
            """Forward messages from Chrome to client with chunking for large messages."""
            nonlocal connection_active
            try:
                async for message in chrome_ws:
                    if not connection_active:
                        break
                        
                    try:
                        if isinstance(message, str):
                            message_size = len(message.encode('utf-8'))
                            logger.debug(f"Received text message from Chrome: {message_size} bytes")
                            
                            # Handle large messages
                            if message_size > CHUNK_SIZE:
                                await handle_large_message(message, websocket)
                            else:
                                await websocket.send_text(message)
                                
                        elif isinstance(message, bytes):
                            message_size = len(message)
                            logger.debug(f"Received binary message from Chrome: {message_size} bytes")
                            
                            if message_size > CHUNK_SIZE:
                                await handle_large_binary_message(message, websocket)
                            else:
                                await websocket.send_bytes(message)
                                
                    except WebSocketDisconnect:
                        logger.info("Client disconnected during message send")
                        connection_active = False
                        break
                    except Exception as e:
                        logger.error(f"Error sending message to client: {e}")
                        # Don't break, try to continue with other messages
                        continue
                        
            except ConnectionClosed:
                logger.info("Chrome connection closed during forward to client")
                connection_active = False
            except Exception as e:
                logger.error(f"Error forwarding to client: {e}")
                connection_active = False
        
        # Run both forwarding tasks concurrently
        tasks = [
            asyncio.create_task(forward_to_chrome()),
            asyncio.create_task(forward_to_client())
        ]
        
        try:
            # Wait for either task to complete (indicating an error or disconnection)
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            
            # Cancel remaining tasks
            for task in pending:
                task.cancel()
            
            # Wait for cancelled tasks to complete
            await asyncio.gather(*pending, return_exceptions=True)
            
        except Exception as e:
            logger.error(f"Error in forwarding tasks: {e}")
        finally:
            connection_active = False
            
            # Ensure all tasks are cancelled
            for task in tasks:
                if not task.done():
                    task.cancel()
            
            # Wait for tasks to complete cancellation
            await asyncio.gather(*tasks, return_exceptions=True)
    
    except websockets.exceptions.InvalidURI:
        logger.error(f"Invalid WebSocket URI: {local_ws_url}")
        try:
            await websocket.close(code=4000, reason="Invalid Chrome WebSocket URI")
        except:
            pass
    except websockets.exceptions.ConnectionClosed:
        logger.info("Chrome WebSocket connection closed")
    except Exception as e:
        logger.error(f"WebSocket proxy error: {e}")
        try:
            if websocket.client_state == websocket.client_state.CONNECTED:
                await websocket.close(code=4000, reason=str(e))
        except:
            pass
    finally:
        # Ensure Chrome connection is closed
        if chrome_ws:
            try:
                await chrome_ws.close()
                logger.info("Chrome WebSocket connection closed")
            except:
                pass

async def handle_large_message(message: str, websocket: WebSocket):
    """Handle large text messages by attempting to compress or filter them."""
    try:
        original_size = len(message.encode('utf-8'))
        logger.info(f"Handling large message: {original_size} bytes")
        
        # Try to parse as JSON and filter large fields
        try:
            data = json.loads(message)
            if isinstance(data, dict) and 'result' in data:
                result = data['result']
                
                # Common CDP responses that can be large
                if 'outerHTML' in result:
                    # Truncate large HTML content
                    html_content = result['outerHTML']
                    if len(html_content) > 100000:  # 100KB
                        result['outerHTML'] = html_content[:50000] + "...[truncated by proxy]"
                        logger.info(f"Truncated HTML content from {len(html_content)} to 50KB")
                
                elif 'nodes' in result:
                    # DOM snapshot can be huge
                    nodes = result['nodes']
                    if len(nodes) > 1000:
                        result['nodes'] = nodes[:1000]
                        result['_truncated'] = True
                        logger.info(f"Truncated DOM nodes from {len(nodes)} to 1000")
                
                elif 'data' in result and isinstance(result['data'], str):
                    # Large data responses (screenshots, etc.)
                    data_content = result['data']
                    if len(data_content) > 1000000:  # 1MB
                        result['data'] = data_content[:500000] + "...[truncated by proxy]"
                        result['_truncated'] = True
                        logger.info(f"Truncated data content from {len(data_content)} to 500KB")
                
                # Network response bodies can be large
                elif 'body' in result and isinstance(result['body'], str):
                    body_content = result['body']
                    if len(body_content) > 100000:  # 100KB
                        result['body'] = body_content[:50000] + "...[truncated by proxy]"
                        result['_truncated'] = True
                        logger.info(f"Truncated response body from {len(body_content)} to 50KB")
                
                message = json.dumps(data)
                
        except json.JSONDecodeError:
            logger.debug("Message is not valid JSON, treating as plain text")
        
        # If still too large, truncate
        final_size = len(message.encode('utf-8'))
        if final_size > MAX_MESSAGE_SIZE:
            message = message[:MAX_MESSAGE_SIZE//2] + "...[message truncated due to size limit]"
            logger.warning(f"Message truncated due to size limit: {final_size} -> {len(message)} bytes")
        
        await websocket.send_text(message)
        logger.debug(f"Sent large message: {len(message.encode('utf-8'))} bytes")
        
    except Exception as e:
        logger.error(f"Error handling large message: {e}")
        # Send error message instead
        error_msg = json.dumps({
            "error": "Message too large to forward",
            "original_size": len(message),
            "details": str(e)
        })
        try:
            await websocket.send_text(error_msg)
        except:
            pass

async def handle_large_binary_message(message: bytes, websocket: WebSocket):
    """Handle large binary messages by chunking or truncating."""
    try:
        original_size = len(message)
        logger.info(f"Handling large binary message: {original_size} bytes")
        
        if original_size > MAX_MESSAGE_SIZE:
            # Truncate binary message
            truncated = message[:MAX_MESSAGE_SIZE//2]
            logger.warning(f"Binary message truncated from {original_size} to {len(truncated)} bytes")
            await websocket.send_bytes(truncated)
        else:
            await websocket.send_bytes(message)
            
    except Exception as e:
        logger.error(f"Error handling large binary message: {e}")

@app.get("/browsers/{browser_id}/json/version")
async def browser_json_version(browser_id: str):
    """Get JSON version info from a specific browser."""
    if browser_id not in browser_manager:
        raise HTTPException(status_code=404, detail="Browser not found")
    
    browser_info = browser_manager[browser_id]
    port = browser_info['port']
    
    try:
        if not http_session:
            raise HTTPException(status_code=500, detail="HTTP session not initialized")
        async with http_session.get(f"http://127.0.0.1:{port}/json/version") as response:
            if response.status == 200:
                version_info = await response.json()
                # Replace localhost with public IP for external access
                if 'webSocketDebuggerUrl' in version_info:
                    browser_ws_id = version_info['webSocketDebuggerUrl'].split('/')[-1]
                    version_info['webSocketDebuggerUrl'] = f"ws://{PUBLIC_IP}:8000/browsers/{browser_id}/devtools/browser/{browser_ws_id}"
                return version_info
            else:
                raise HTTPException(status_code=response.status, detail="Failed to fetch version")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/browsers")
async def list_browsers():
    """List all active browsers."""
    browsers = []
    for browser_id, browser_info in browser_manager.items():
        browsers.append({
            "id": browser_id,
            "port": browser_info['port'],
            "status": browser_info['status'],
            "cdp_url": browser_info['cdp_url']
        })
    return {"browsers": browsers}

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "active_browsers": len(browser_manager),
        "http_session": http_session is not None
    }

if __name__ == "__main__":
    # Run with increased WebSocket message size limits
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8000,
        ws_max_size=MAX_MESSAGE_SIZE,
        timeout_keep_alive=30
    )