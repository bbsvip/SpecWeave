"""Entry point for durable review worker: python source/review_worker.py."""
import asyncio
from review_queue import worker_main
from chat_background import chat_worker_main
from project_trash import project_trash_worker

if __name__ == "__main__":
    async def main():
        await asyncio.gather(worker_main(), chat_worker_main(), project_trash_worker())
    asyncio.run(main())
