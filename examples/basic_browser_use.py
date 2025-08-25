import asyncio
from dotenv import load_dotenv
load_dotenv()
from browser_use import Agent, ChatOpenAI
import os

async def main():
    llm = ChatOpenAI(
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        # model="doubao-1-5-pro-256k-250115" 
        model = "doubao-seed-1-6-250615", # worked with 0.5.7
        # model = "deepseek-v3-1-250821",
        api_key=os.getenv("ARK_API_KEY"),
    )

    agent = Agent(
        task="get the price of BTC",
        llm=llm,
    )
    await agent.run()

asyncio.run(main())