from src import log, responses
from src.ui.draw_buttons import DrawButtons

logger = log.setup_logger(__name__)

async def handle_draw(interaction, prompt, enhance=None):
    username = str(interaction.user)
    channel = str(interaction.channel)
    logger.info(f"\x1b[31m{username}\x1b[0m : /draw [{prompt}] in ({channel})")

    try:
        if enhance and enhance.value == "yes":
            logger.info(f"Enhancing prompt: {prompt}")
            enhanced_prompt = await responses.enhance_prompt(prompt)
            prompt = enhanced_prompt
            logger.info(f"Enhanced prompt: {prompt}")

        view = DrawButtons(prompt, interaction)
        await interaction.followup.send(content="Select the model you want to use:", view=view)
        await view.wait()
    except Exception as e:
        logger.exception(f"Error in draw command: {str(e)}")
        await interaction.followup.send("An error occurred while preparing image generation options.")