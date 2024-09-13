import asyncio
from collections import deque
import discord
from functools import wraps

class QueueManager:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.is_processing = False

    async def add_to_queue(self, interaction: discord.Interaction, task):
        # Immediately acknowledge the interaction
        if not interaction.response.is_done():
            await interaction.response.defer(thinking=True)
        
        await self.queue.put((interaction, task))
        
        if not self.is_processing:
            asyncio.create_task(self.process_queue())

    async def process_queue(self):
        self.is_processing = True
        while not self.queue.empty():
            interaction, task = await self.queue.get()

            try:
                # Execute the task
                await task()
            except discord.errors.NotFound:
                # Interaction may have expired, log and continue
                print(f"Interaction {interaction.id} not found. It may have expired.")
            except Exception as e:
                # Log the error and attempt to notify the user
                print(f"Error processing task: {str(e)}")
                try:
                    await interaction.followup.send(f"An error occurred: {str(e)}")
                except discord.errors.NotFound:
                    print(f"Couldn't send error message to user for interaction {interaction.id}")
            finally:
                # Mark the task as done
                self.queue.task_done()

        self.is_processing = False

queue_manager = QueueManager()

def enqueue(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        interaction = args[0] if isinstance(args[0], discord.Interaction) else kwargs.get('interaction')
        if not interaction:
            raise ValueError("Could not find discord.Interaction in arguments")
        
        task = lambda: func(*args, **kwargs)
        await queue_manager.add_to_queue(interaction, task)
    
    return wrapper