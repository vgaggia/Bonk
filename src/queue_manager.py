import asyncio
import discord
from functools import wraps
import logging
from .error_handler import handle_interaction_error

logger = logging.getLogger(__name__)

class QueueManager:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.is_processing = False
        logger.info("Queue manager initialized")

    async def add_to_queue(self, interaction: discord.Interaction, task):
        """Add a task to the queue and acknowledge the interaction"""
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(thinking=True)
                logger.debug(f"Deferred interaction {interaction.id}")
            
            await self.queue.put((interaction, task))
            logger.debug(f"Added task to queue for interaction {interaction.id}")
            
            if not self.is_processing:
                logger.debug("Starting queue processing")
                asyncio.create_task(self.process_queue())
        except Exception as e:
            logger.error(f"Error adding task to queue: {str(e)}")
            await handle_interaction_error(interaction, e)

    async def process_queue(self):
        """Process tasks in the queue"""
        self.is_processing = True
        logger.info("Started processing queue")
        
        while not self.queue.empty():
            interaction, task = await self.queue.get()
            logger.debug(f"Processing task for interaction {interaction.id}")

            try:
                await task()
                logger.debug(f"Successfully completed task for interaction {interaction.id}")
            except discord.errors.NotFound:
                logger.warning(f"Interaction {interaction.id} not found (expired)")
            except Exception as e:
                logger.error(f"Error processing task: {str(e)}")
                await handle_interaction_error(interaction, e)
            finally:
                self.queue.task_done()
                logger.debug(f"Marked task done for interaction {interaction.id}")

        self.is_processing = False
        logger.info("Finished processing queue")

queue_manager = QueueManager()

def enqueue(func):
    """Decorator to enqueue a command for processing"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        # Find the interaction object
        interaction = next(
            (arg for arg in args if isinstance(arg, discord.Interaction)),
            kwargs.get('interaction')
        )
        
        if not interaction:
            logger.error("Could not find discord.Interaction in arguments")
            raise ValueError("Could not find discord.Interaction in arguments")
        
        logger.debug(f"Enqueueing command {func.__name__} for interaction {interaction.id}")
        task = lambda: func(*args, **kwargs)
        await queue_manager.add_to_queue(interaction, task)
    
    return wrapper
