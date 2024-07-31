import os
import anthropic
from asgiref.sync import sync_to_async
from dotenv import load_dotenv

load_dotenv()

anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")

if not anthropic_api_key:
    raise ValueError("ANTHROPIC_API_KEY is not set in the environment variables")

anthropic_client = anthropic.Anthropic(api_key=anthropic_api_key)

async def handle_response(message) -> str:
    try:
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
        return response.content[0].text
    except Exception as e:
        return f"An error occurred: {str(e)}"