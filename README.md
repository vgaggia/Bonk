# Bonk

Bonk is a bot that can be used to generate images and or interact with GPT-4 using StabilityAI and OpenAI's APIs respectively.

## Features

Utilizes Dall-e 3 & Stable Diffusion 3 for image generation
Integrates with StabilityAI and OpenAI's APIs

## Installation

1. Clone the repository
2. Install the dependencies from requirements with "pip install -r requirements"
3. Configure the API keys for StabilityAI and OpenAI in a `.env` file.
4. Run the bot: with 'py start.py'

## Usage

Once the bot is running, you can interact with it in any discord server you add it to with the /draw, /chat and /help command

# .env recommended layout:
```
# Discord Bot token
DISCORD_BOT_TOKEN=""

# Anthropic API key
ANTHROPIC_API_KEY=""

# OpenAI API key
OPENAI_API_KEY=""

# Stability AI API Key

STABILITY_API_KEY = ""

# Optional settings
CHAT_MODEL="ANTHROPIC"
GPT_ENGINE="claude-3-5-sonnet-20240620"
LOGGING="True"
```

## License

This project is licensed under the [GPL-3.0 License](LICENSE).
