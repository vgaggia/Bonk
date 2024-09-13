from src import log
from src.ui.draw_buttons import DrawButtons

logger = log.setup_logger(__name__)

async def handle_draw(interaction, prompt):
    username = str(interaction.user)
    channel = str(interaction.channel)
    logger.info(f"\x1b[31m{username}\x1b[0m : /draw [{prompt}] in ({channel})")

    try:
        await interaction.response.defer(thinking=True)
        view = DrawButtons(prompt, interaction)
        await view.start()
    except Exception as e:
        logger.exception(f"Error in draw command: {str(e)}")
        await interaction.followup.send("An error occurred while preparing image generation options.")