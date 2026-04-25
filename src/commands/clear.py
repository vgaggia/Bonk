from src import log
from src.responses import message_history

logger = log.setup_logger(__name__)


async def handle_clear(interaction):
    user_id = interaction.user.id
    message_history.clear_history(user_id)
    await interaction.followup.send("> **Info: Your message history has been cleared.**")
    logger.info(f"Message history cleared for user {user_id}")
