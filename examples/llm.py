#!/usr/bin/env python3
"""
Test script for DeepSeek model via ARK API
Usage: 
export ARK_API_KEY=e12bfb67-c025-4587-be5e-235661673886 && export ARK_MODEL_ID=deepseek-v3-1-250821 && export ARK_EXTRACT_MODEL_ID=deepseek-v3-1-250821 && export ARK_USE_VISION=false && python examples/llm.py
"""

import os
from langchain_openai import ChatOpenAI

def test_ark_api():
    # Get environment variables
    api_key = os.getenv("ARK_API_KEY")
    model_id = os.getenv("ARK_MODEL_ID") 
    base_url = os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
    
    print(f"API Key: {api_key[:10]}..." if api_key else "No API Key")
    print(f"Model ID: {model_id}")
    print(f"Base URL: {base_url}")
    print("-" * 50)
    
    if not api_key or not model_id:
        print("❌ Missing required environment variables")
        print("Please set ARK_API_KEY and ARK_MODEL_ID")
        return
    
    try:
        # Create ChatOpenAI instance for ARK API
        llm = ChatOpenAI(
            base_url=base_url,
            model=model_id,
            api_key=api_key,
            default_headers={
                "X-Client-Request-Id": "test-deepseek-20250825"
            }
        )
        
        # Test simple completion
        print("🧪 Testing simple completion...")
        response = llm.invoke("Hello! Please respond with 'API connection successful'")
        print(f"✅ Response: {response.content}")
        
        # Test more complex query
        print("\n🧪 Testing complex query...")
        complex_query = "What is 2+2? Please explain your calculation step by step."
        response = llm.invoke(complex_query)
        print(f"✅ Response: {response.content}")
        
        print("\n🎉 All tests passed! ARK API is working correctly.")
        
    except Exception as e:
        print(f"❌ Error testing ARK API: {str(e)}")
        print(f"Error type: {type(e).__name__}")

if __name__ == "__main__":
    test_ark_api()