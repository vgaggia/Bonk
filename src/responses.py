import asyncio
import logging
import os

import anthropic
import requests
from dotenv import load_dotenv

from .error_handler import APIError, handle_error
from .message_history import MessageHistory

# Set up logging
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Constants (configurable via .env)
CLAUDE_MODEL = os.getenv("GPT_ENGINE", "claude-sonnet-4-20250514")
MAX_TOKENS = 1000
PROMPT_MAX_TOKENS = 200  # Increased for better prompt enhancement
MAX_MESSAGE_LENGTH = 4000  # Discord limit consideration
LOCAL_API_BASE = os.getenv("LOCAL_API_BASE", "http://127.0.0.1:5000/v1")
LOCAL_SYSTEM_PROMPT = os.getenv(
    "LOCAL_SYSTEM_PROMPT",
    "Act lively, and do your best to emulate being vgaggia, don't say sentences too short. And also your in a playful mood. The following is an emulated conversation with vgaggia:",
)

# Default chat model selection from .env (CHAT_MODEL)
_chat_model_env = os.getenv("CHAT_MODEL", "").strip().lower()
if _chat_model_env in ("anthropic", "claude"):
    DEFAULT_CHAT_MODEL = "anthropic"
elif _chat_model_env in ("gpt-4o", "openai", "gpt"):
    DEFAULT_CHAT_MODEL = "gpt-4o"
elif _chat_model_env in ("local", "local-model"):
    DEFAULT_CHAT_MODEL = "local-model"
else:
    DEFAULT_CHAT_MODEL = "anthropic"

# Initialize message history and user preferences
message_history = MessageHistory(max_messages=10)
voice_message_history = MessageHistory(max_messages=40)  # Quadrupled size for voice chat
user_model_preferences = {}  # Store user's last selected model


def initialize_anthropic_client():
    """Initialize the Anthropic client with proper error handling"""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise APIError("ANTHROPIC_API_KEY is not set in the environment variables")

    try:
        return anthropic.Anthropic(api_key=api_key)
    except Exception as e:
        logger.error("Failed to initialize Anthropic client")
        raise APIError(f"Failed to initialize Anthropic client: {str(e)}")


# Initialize the client
try:
    anthropic_client = initialize_anthropic_client()
except APIError as e:
    logger.error(str(e))
    raise


class ModelAPIs:
    @staticmethod
    async def anthropic_api(
        client, message, model, user_id=None, voice_mode=False, extra_context=None
    ):
        """Handle Anthropic API calls with message history"""
        messages = []

        # Add message history
        if voice_mode:
            # Use shared voice history for voice mode
            # We use a fixed ID for shared voice history, e.g., 0 or the guild ID passed as context
            # For now, we'll use a dedicated "shared" ID like 'voice_shared'
            messages.extend(voice_message_history.format_history_for_api('voice_shared'))
        elif user_id is not None:
            messages.extend(message_history.format_history_for_api(user_id))

        # Add the current message
        content = message
        if extra_context:
            content = f"Context from other users:\n{extra_context}\n\nUser message:\n{message}"

        messages.append({"role": "user", "content": content})

        # Different system prompts for voice vs text
        if voice_mode:
            system_content = (
                "You are Bonk in a Discord voice chat. "
                "Keep your responses VERY short and conversational - like you're actually talking. "
                "Use 1-2 short sentences maximum. Think casual speech, not essays. "
                "Be natural and concise like real conversation."
            )
        else:
            system_content = (
                "You are Bonk, an AI assistant for the Bonk Discord bot. "
                "You have access to previous messages in the conversation. "
                "Keep responses relatively concise and easy to follow, "
                "especially when they will be spoken aloud via TTS."
            )

        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            lambda: client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS,
                temperature=0.7,
                system=system_content,
                messages=messages,
            ),
        )

        # Store the interaction in history
        if voice_mode:
            voice_message_history.add_message('voice_shared', "user", message)
            voice_message_history.add_message('voice_shared', "assistant", response.content[0].text)
        elif user_id is not None:
            message_history.add_message(user_id, "user", message)
            message_history.add_message(user_id, "assistant", response.content[0].text)

        return response.content[0].text

    @staticmethod
    async def openai_like_api(
        base_url, api_key, message, model, user_id=None, voice_mode=False, extra_context=None
    ):
        """Handle OpenAI-like API calls with message history"""
        headers = {"Content-Type": "application/json"}

        # Only add Authorization header if api_key is provided
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        messages = []

        # Add system message
        if voice_mode:
            voice_system = (
                "You are Bonk in a Discord voice chat. "
                "Keep responses VERY short and conversational - like you're actually talking. "
                "Use 1-2 short sentences maximum. Think casual speech, not essays. "
                "Be natural and concise like real conversation."
            )
            messages.append({"role": "system", "content": voice_system})
        elif model == "local-model":
            messages.append({"role": "system", "content": LOCAL_SYSTEM_PROMPT})

        # Add message history
        if voice_mode:
            messages.extend(voice_message_history.format_history_for_api('voice_shared'))
        elif user_id is not None:
            messages.extend(message_history.format_history_for_api(user_id))

        # Add the current message
        content = message
        if extra_context:
            content = f"Context from other users:\n{extra_context}\n\nUser message:\n{message}"

        messages.append({"role": "user", "content": content})

        # Base payload
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": MAX_TOKENS,
            "temperature": 0.7,
        }

        # Add chat mode, character, and preset for local model
        if model == "local-model":
            payload = {
                **payload,
                "mode": "chat",
                "character": "vgaggia",
                "preset": "My Preset",
                "system_prompt": LOCAL_SYSTEM_PROMPT,  # Add system prompt directly in payload
            }

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.post(
                    f"{base_url}/chat/completions", headers=headers, json=payload, timeout=30
                ),
            )
            response.raise_for_status()
            response_text = response.json()['choices'][0]['message']['content']

            # Store the interaction in history
            if voice_mode:
                voice_message_history.add_message('voice_shared', "user", message)
                voice_message_history.add_message('voice_shared', "assistant", response_text)
            elif user_id is not None:
                message_history.add_message(user_id, "user", message)
                message_history.add_message(user_id, "assistant", response_text)

            return response_text
        except requests.exceptions.RequestException as e:
            logger.error(f"OpenAI-like API error: {str(e)}")
            if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
                try:
                    error_detail = e.response.json()
                    raise APIError(f"API request failed: {str(e)}. Details: {error_detail}")
                except ValueError:
                    pass
            raise APIError(f"API request failed: {str(e)}")


async def handle_response(
    message, model=None, user_id=None, voice_mode=False, extra_context=None
) -> str:
    """Handle user message and get AI response with model selection and message history"""
    if not message or not message.strip():
        raise ValueError("Message cannot be empty")

    # Get the user's preferred model if none specified
    if model is None and user_id and user_id in user_model_preferences:
        model = user_model_preferences.get(user_id)
    elif model is None:
        # Default if no preference exists: respect CHAT_MODEL from .env
        model = DEFAULT_CHAT_MODEL

    # Store the current model selection for the user
    if user_id is not None and model is not None:
        user_model_preferences[user_id] = model
        logger.info(f"Stored model preference for user {user_id}: {model}")

    logger.info(f"Processing message request with model: {model}, voice_mode: {voice_mode}")

    try:
        if model == 'anthropic':
            return await ModelAPIs.anthropic_api(
                anthropic_client, message, CLAUDE_MODEL, user_id, voice_mode, extra_context
            )
        elif model == 'gpt-4o':
            gpt4o_key = os.getenv("OPENAI_API_KEY")
            if not gpt4o_key:
                raise APIError("OpenAI API key not configured")
            return await ModelAPIs.openai_like_api(
                "https://api.openai.com/v1",
                gpt4o_key,
                message,
                "gpt-4o",
                user_id,
                voice_mode,
                extra_context,
            )
        elif model == 'local-model':
            local_key = os.getenv("LOCAL_MODEL_API_KEY")  # Optional for local models
            return await ModelAPIs.openai_like_api(
                LOCAL_API_BASE,
                local_key,
                message,
                "local-model",
                user_id,
                voice_mode,
                extra_context,
            )
        else:
            raise ValueError(f"Unsupported model: {model}")
    except Exception as e:
        error_msg = handle_error(e, "message_processing")
        logger.error(f"Error in handle_response: {error_msg}")
        raise APIError(error_msg)


async def enhance_prompt(prompt: str, context: str = 'image') -> str:
    """Enhance a prompt with more details based on context"""
    if not prompt.strip():
        raise ValueError("Prompt cannot be empty")

    logger.info(f"Enhancing {context} prompt")
    try:
        # Different system prompts based on context
        if context == 'image':
            system_prompt = f"""Enhance this image generation prompt by adding specific details about:
            - Setting/environment
            - Lighting/time of day/weather
            - Art style/medium
            - Complementary elements
            - Emotions/actions (if applicable)
            Keep the enhanced prompt under 200 characters while maintaining the original subject.
            
            Prompt: {prompt}"""
        elif context == 'tts':
            system_prompt = f"""Enhance this text-to-speech prompt by:
            adding stuff like AAAAAAA and IOUUUUUUU to the provided text, for example if you get the original "This is amazing! As soon as I received. It I had to strip down and try it out. Using the app I found a pre-programmed pattern that blew more than my socks off (if you know what I mean). And the gush when paired with my Flexer is an other worldly experience. Can’t wait to collect all of these fun items" it should be more like "This is amazing! AAAAAAAAAAAAAAAAAAA As soon as I received. It I had to strip down and try it out. Using the app I found a pre-programmed pattern that blew more than my socks off (if you know what I mean) IOUUUUUUUUUUUUU. And the gush when paired with my Flexer YAAAAAAAAAAAAAAAAAAAAA is an other worldly experience. Can’t wait to collect all of these fun items."
            Please just give the modified text with your changes and dont add stuff like "here's the modified original text", also add it where you think punctuation should be(like in the example), and you dont HAVE to add exactly the example it should just be LIKE that
            Original Text: {prompt}"""
        else:
            raise ValueError(f"Unsupported enhancement context: {context}")

        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            lambda: anthropic_client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=PROMPT_MAX_TOKENS,
                temperature=0.7,
                messages=[{"role": "user", "content": system_prompt}],
            ),
        )
        enhanced_prompt = response.content[0].text.strip()
        logger.info("Successfully enhanced prompt")
        return enhanced_prompt
    except Exception as e:
        logger.warning(f"Failed to enhance prompt, returning original: {str(e)}")
        return prompt  # Fallback to original prompt on error
