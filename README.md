# Bonk

- Bonk is a bot that can be used to generate images and or interact with GPT-4 using StabilityAI and OpenAI's APIs respectively.

## Features

- Utilizes Dall-e 3 & Stable Diffusion 3 for image generation
- Integrates with StabilityAI and OpenAI's APIs

## Installation

1. Clone the repository
2. Install the dependencies from requirements.txt with "pip install -r requirements"
3. Configure the API keys for StabilityAI and OpenAI in a `.env` file.
4. Run the bot: with 'py start.py'

## Usage

Once the bot is running, you can interact with it in any discord server you add it to with the /draw, /chat and /help command

## License

This project is licensed under the [GPL-3.0 License](LICENSE).


## env recommended layout:

# Discord Bot token
DISCORD_BOT_TOKEN="YOUR-OPENAI-API-KEY"

# OpenAI Authentication
OPENAI_API_KEY="YOUR-OPENAI-API-KEY"

#StabilityAI Authentication
STABILITY_API_KEY = "YOUR-STABILITY-API-KEY"

# Optional settings
CHAT_MODEL="OFFICIAL"
GPT_ENGINE="gpt-4"
LOGGING="True"
REPLYING_ALL="False"
REPLYING_ALL_DISCORD_CHANNEL_ID=""

