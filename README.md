# Bonk

Bonk is a versatile Discord bot that integrates with multiple AI services for image generation, text-to-speech, video generation, music playback, and chat interactions using the latest AI models.

## Features

- 🤖 **Multi-AI Chat**: Support for Claude 4, GPT-4o, and local models
- 🎨 **Image Generation**: DALL-E 3, Stable Diffusion 3, and Replicate models with full aspect ratio support
- 🎬 **Video Generation**: AI-powered video creation with Luma Labs
- 🎵 **Music Player**: YouTube integration with queue management
- 🗣️ **Text-to-Speech**: Multiple voice options with AI enhancement
- 🎭 **Animations**: Profile picture animations and 3D model generation
- 📝 **Message History**: Persistent conversation context per user
- 🔄 **Image Iteration**: Edit and refine images with GPT Image 1

Utilizes the latest Claude 4 models, DALL-E 3, Stable Diffusion 3, and more for comprehensive AI interactions.

## Installation

1. Clone the repository
```bash
git clone https://github.com/yourusername/Bonk.git
cd Bonk
```

2. Install dependencies
```bash
pip install -r requirements.txt
```

3. **IMPORTANT**: Copy the example environment file and configure your API keys
```bash
cp .env.example .env
```
Then edit `.env` with your actual API keys (see API Keys section below)

4. Run the bot
```bash
python main.py
```

## API Keys Required

You'll need API keys from the following services:

- **Discord Bot Token**: Create a bot at [Discord Developer Portal](https://discord.com/developers/applications)
- **Anthropic API Key**: Get from [Anthropic Console](https://console.anthropic.com/)
- **OpenAI API Key**: Get from [OpenAI Platform](https://platform.openai.com/)
- **Stability AI API Key**: Get from [Stability AI Platform](https://platform.stability.ai/)
- **Replicate API Token**: Get from [Replicate](https://replicate.com/)
- **Luma Labs API Key**: Get from [Luma Labs](https://lumalabs.ai/)

⚠️ **SECURITY WARNING**: Never commit your `.env` file to version control! The `.env.example` file shows the required format without exposing real keys.

## Usage

Once the bot is running, invite it to your Discord server and use these commands:

- `/chat` - Chat with AI models (Claude 4, GPT-4o, or local)
- `/draw` - Generate images with AI
- `/imagine` - Animate profile pictures or images
- `/3d` - Generate 3D models from images
- `/video` - Create AI-generated videos
- `/tts` - Text-to-speech with multiple voices
- `/play` - Play YouTube videos
- `/help` - Show all available commands

## Configuration

Key settings in your `.env` file:

```env
# Default AI model for chat (recommended: ANTHROPIC for Claude 4)
CHAT_MODEL="ANTHROPIC"

# Claude model version (latest: Claude 4)
GPT_ENGINE="claude-sonnet-4-20250514"

# Enable detailed logging
LOGGING="True"
```

## Recent Updates

- ✅ **Updated to Claude 4**: Now using the latest `claude-sonnet-4-20250514` model
- ✅ **DALL-E Aspect Ratios**: DALL-E 3 now supports all aspect ratios (was previously 1:1 only)
- ✅ **Fixed Iterate Button**: GPT Image 1 iterate functionality now works reliably with better timeout handling
- ✅ **Enhanced Security**: Proper API key management and validation
- ✅ **Improved Error Handling**: Better error messages and recovery
- ✅ **Version Management**: Pinned dependency versions for stability

## License

This project is licensed under the [GPL-3.0 License](LICENSE).
