import asyncio
from collections import deque

class QueueManager:
    def __init__(self):
        self.queue = deque()
        self.lock = asyncio.Lock()

    async def add_to_queue(self, interaction, task):
        async with self.lock:
            self.queue.append((interaction, task))
        await self.process_queue()

    async def process_queue(self):
        async with self.lock:
            if not self.queue:
                return
            interaction, task = self.queue.popleft()

        try:
            await interaction.response.defer(thinking=True)
            await task()
        except Exception as e:
            await interaction.followup.send(f"An error occurred: {str(e)}")
        finally:
            await self.process_queue()

queue_manager = QueueManager()

def enqueue(func):
    async def wrapper(interaction, *args, **kwargs):
        await queue_manager.add_to_queue(interaction, lambda: func(interaction, *args, **kwargs))
    return wrapper