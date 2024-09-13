from src import log

logger = log.setup_logger(__name__)

async def handle_reset(interaction):
    await interaction.followup.send("> **Info: I have forgotten everything.**")
    logger.warning("\x1b[31mClaude bot has been successfully reset\x1b[0m")