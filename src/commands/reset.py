from src import log
from src.responses import message_history, user_model_preferences, voice_message_history

logger = log.setup_logger(__name__)


async def handle_reset(interaction):
    user_id = interaction.user.id
    message_history.clear_history(user_id)
    voice_message_history.clear_history('voice_shared')
    user_model_preferences.pop(user_id, None)
    await interaction.followup.send("> **Info: I have forgotten everything.**")
    logger.warning("Bot has been successfully reset for user %s", user_id)
