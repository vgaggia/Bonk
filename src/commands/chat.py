import uuid
from src import responses, log

logger = log.setup_logger(__name__)

async def handle_chat(interaction, message):
    message_id = str(uuid.uuid4())[:8]
    logger.info(f"[{message_id}] Received chat command from {interaction.user} : /chat [{message}] in ({interaction.channel})")
    
    try:
        logger.info(f"[{message_id}] Calling handle_response")
        response = await responses.handle_response(message)
        logger.info(f"[{message_id}] Received response from handle_response")
        
        logger.info(f"[{message_id}] Sending response to user")
        await interaction.response.send_message(response)
        logger.info(f"[{message_id}] Response sent to user")
    except Exception as e:
        logger.exception(f"[{message_id}] Error in chat command: {str(e)}")
        await interaction.response.send_message("An error occurred while processing your request.")