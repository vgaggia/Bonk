import uuid

from src import log, responses
from src.commands import tts

logger = log.setup_logger(__name__)

async def handle_chat(interaction, message, tts_enabled, model=None):
    message_id = str(uuid.uuid4())[:8]
    logger.info(f"[{message_id}] Received chat command from {interaction.user} : /chat [{message}] in ({interaction.channel})")
    
    try:
        logger.info(f"[{message_id}] Calling handle_response with model: {model}")
        # Pass the user ID to handle_response with model as None to use stored preference
        response = await responses.handle_response(
            message, 
            model=model,  # Will use stored preference if None
            user_id=interaction.user.id
        )
        logger.info(f"[{message_id}] Received response from handle_response")
        
        logger.info(f"[{message_id}] Sending response to user")
        await interaction.followup.send(response)
        logger.info(f"[{message_id}] Response sent to user")

        if tts_enabled and tts_enabled.value == "yes":
            await tts.handle_tts_for_chat(interaction, response)

    except Exception as e:
        logger.exception(f"[{message_id}] Error in chat command: {str(e)}")
        await interaction.followup.send("An error occurred while processing your request.")
