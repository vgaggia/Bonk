import os
import anthropic
from dotenv import load_dotenv
import logging

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")

if not anthropic_api_key:
    raise ValueError("ANTHROPIC_API_KEY is not set in the environment variables")

logger.debug(f"Anthropic API Key: {anthropic_api_key[:5]}{'*' * (len(anthropic_api_key) - 5) if anthropic_api_key else 'Not set'}")

try:
    anthropic_client = anthropic.Anthropic(api_key=anthropic_api_key)
    logger.debug("Anthropic client initialized successfully")
except Exception as e:
    logger.error(f"Error initializing Anthropic client: {str(e)}")
    raise

async def handle_response(message) -> str:
    logger.info(f"Handling response for message: {message[:50]}...")  # Log first 50 chars of message
    try:
        logger.info("Sending request to Anthropic API")
        response = anthropic_client.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=1000,
            temperature=0.7,
            system="You are Claude, an AI assistant. Respond only to the current message without considering any previous context.",
            messages=[
                {
                    "role": "user",
                    "content": message
                }
            ]
        )
        logger.info("Received response from Anthropic API")
        return response.content[0].text
    except Exception as e:
        logger.exception(f"Error in handle_response: {e}")
        return f"An error occurred: {str(e)}"