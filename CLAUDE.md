# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Running the Bot
```bash
python main.py
```

### Testing
```bash
pytest
pytest tests/test_specific_file.py  # Run single test file
```

### Code Quality
```bash
ruff check .                # Lint code
ruff format .              # Format code
```

### Dependencies
```bash
pip install -r requirements.txt          # Production dependencies
pip install -r dev-requirements.txt      # Development dependencies
```

## Code Architecture

### Entry Point
- `main.py` - Simple entry point that imports and runs the bot
- `src/bot.py` - Main Discord bot setup with command registration and API client initialization

### Core Systems

**Queue Management (`src/queue_manager.py`)**
- All Discord commands are processed through a centralized queue system
- Commands decorated with `@enqueue` are automatically queued for sequential processing
- Prevents rate limiting and ensures stable command execution

**Error Handling**
- Global error handling in `src/error_handler.py` and `src/art/error_handler.py`
- Interaction-specific error handling for Discord commands
- Comprehensive logging through `src/log.py`

**Message History (`src/message_history.py`)**
- Persistent conversation context per user
- Used by chat commands to maintain conversation state

### AI Integration Modules

**Chat (`src/commands/chat.py`)**
- Multi-AI model support: Claude 4, GPT-4o, local models
- Model selection via environment variables and user preferences

**Image Generation (`src/art/`)**
- `image_generation.py` - DALL-E 3 and Stable Diffusion 3 integration
- `replicate_models.py` - Replicate API model management
- `utils.py` - Common utilities for image processing

**Video Generation (`src/art/`)**
- `video_generation.py` and `video_generation2.py` - Luma Labs integration
- Support for AI-powered video creation

**3D Models (`src/art/model_3d.py`)**
- Image-to-3D model generation via Replicate

### UI Components (`src/ui/`)
- `aspect_ratio_view.py` - Aspect ratio selection for image generation
- `draw_buttons.py` - Interactive buttons for image generation commands
- `generate_video_view.py` - Video generation interface
- `replicate_model_selector.py` - Model selection interface

### Commands Structure (`src/commands/`)
All Discord slash commands are organized in separate modules:
- `chat.py` - AI chat functionality
- `draw.py` - Image generation with multiple models
- `imagine.py` - Profile picture animation
- `model_3d.py` - 3D model generation
- `video.py` - Video generation
- `tts.py` - Text-to-speech
- `music.py` - YouTube integration with queue management
- `help.py`, `reset.py`, `clear.py` - Utility commands

## Configuration

### Required Environment Variables
Copy `.env.example` to `.env` and configure:
- `DISCORD_BOT_TOKEN` - Discord bot token
- `ANTHROPIC_API_KEY` - For Claude 4 integration
- `OPENAI_API_KEY` - For DALL-E 3 and GPT-4o
- `STABILITY_API_KEY` - For Stable Diffusion 3
- `REPLICATE_API_TOKEN` - For various Replicate models
- `LUMALABS_API_KEY` - For video generation

### Key Settings
- `CHAT_MODEL="ANTHROPIC"` - Default AI model for chat
- `GPT_ENGINE="claude-sonnet-4-20250514"` - Claude model version
- `LOGGING="True"` - Enable detailed logging

## Important Patterns

1. **All commands must use `@enqueue` decorator** for proper queue management
2. **Error handling** - Use existing error handlers rather than creating new ones
3. **Logging** - Use the configured logger from `src.log`
4. **API clients** - Anthropic and OpenAI clients are initialized in `src/bot.py`
5. **Discord interactions** - Commands should handle both deferred and immediate responses
6. **Health checks** - System performs startup health checks in `src/health_check.py`

## Code Quality Configuration
- **Ruff** configured in `pyproject.toml` with line length 100, Python 3.10+ target
- **Pytest** configured for `tests/` directory with quiet output
- **Import organization** follows ruff's isort-compatible rules