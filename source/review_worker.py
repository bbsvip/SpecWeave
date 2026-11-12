"""Entry point for durable review worker: python source/review_worker.py."""
import asyncio
from review_queue import worker_main

if __name__ == "__main__":
    asyncio.run(worker_main())
