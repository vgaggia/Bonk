# src/responses.py

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
            system="You are Claude, an AI assistant.",
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

async def enhance_prompt(prompt: str) -> str:
    try:
        logger.info(f"Enhancing prompt: {prompt}")
        response = anthropic_client.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=100,
            temperature=0,
            messages=[
                {
                    "role": "user",
                    "content": f"You are an AI assistant tasked with enhancing text-to-image prompts. Your goal is to take a simple initial prompt and expand it into a more detailed and vivid description that can be used to generate more interesting and specific images.\n\nWhen enhancing the prompt, consider the following guidelines:\n1. Add specific details about the setting or environment\n2. Include information about lighting, time of day, or weather\n3. Suggest a particular art style or medium\n4. Incorporate additional elements that complement the main subject\n5. Describe emotions, actions, or interactions if applicable\n6. The enhanced prompt must be shorter than 60 tokens or 200 characters in total\n7. Try to adhere to the original subject and enhance it as you see fit whilst following all the rules\n\nHere is the initial prompt to enhance:\n<initial_prompt>\n{prompt}\n</initial_prompt>\n\nYour output should simply just be the enhanced prompt"
                }
            ]
        )
        enhanced_prompt = response.content[0].text.strip()
        logger.info(f"Enhanced prompt: {enhanced_prompt}")
        return enhanced_prompt
    except Exception as e:
        logger.exception(f"Error enhancing prompt: {str(e)}")
        return prompt  # Return original prompt if enhancement fails