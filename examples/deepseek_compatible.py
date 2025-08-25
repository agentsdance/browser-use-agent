import asyncio
import os
from dotenv import load_dotenv
from browser_use import Agent, ChatOpenAI
from browser_use.llm.openai.chat import ChatOpenAI as BrowserUseChatOpenAI
from langchain_core.messages import BaseMessage
from typing import Any, Dict, List, Optional
import json

load_dotenv()

class DeepSeekCompatibleChat(BrowserUseChatOpenAI):
    """
    Custom ChatOpenAI that uses json_object instead of json_schema
    for DeepSeek compatibility via ARK API
    """
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
    
    async def _agenerate_with_structured_output(
        self,
        messages: List[BaseMessage],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        response_format: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> Any:
        """Override to use json_object format instead of json_schema"""
        
        # Convert json_schema format to json_object for DeepSeek compatibility
        if response_format and response_format.get("type") == "json_schema":
            # Use json_object format and include schema instructions in the system message
            schema = response_format.get("json_schema", {})
            schema_name = schema.get("name", "response")
            
            # Add schema instructions to the last message
            if messages and messages[-1].content:
                schema_instruction = f"\n\nPlease respond with a valid JSON object that follows this schema structure: {json.dumps(schema.get('schema', {}), indent=2)}"
                messages[-1].content += schema_instruction
            
            # Use json_object format
            kwargs["response_format"] = {"type": "json_object"}
        
        # Call the parent method with modified parameters
        return await super()._agenerate_with_structured_output(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            response_format=kwargs.get("response_format"),
            **{k: v for k, v in kwargs.items() if k != "response_format"}
        )

async def main():
    llm = DeepSeekCompatibleChat(
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        model="deepseek-v3-1-250821",
        api_key=os.getenv("ARK_API_KEY"),
        timeout=30,
        default_headers={
            "X-Client-Request-Id": "deepseek-browser-use-example"
        }
    )

    agent = Agent(
        task="get the price of BTC",
        llm=llm,
    )
    await agent.run()

if __name__ == "__main__":
    asyncio.run(main())