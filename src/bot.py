import os
import certifi
import discord
from discord import app_commands
from src import responses, log
from src.health_check import health_check
from src.commands import chat, draw, imagine, model_3d, reset, help, music, tts, clear, video
from src.ui import draw_buttons, aspect_ratio_view, generate_video_view
from src.error_handler import handle_error
from src.queue_manager import enqueue
from dotenv import load_dotenv
import anthropic
from openai import OpenAI
import logging

# Setup
load_dotenv()
os.environ['SSL_CERT_FILE'] = certifi.where()

logger = log.setup_logger(__name__)

# Initialize API clients
try:
    anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    logger.info("API clients initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize API clients: {str(e)}")
    raise

# Discord setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client_instance = discord.Client(intents=intents)
tree = app_commands.CommandTree(client_instance)

@client_instance.event
async def on_ready():
    """Handle bot startup"""
    try:
        # Run health checks first
        await health_check()
        
        # Sync commands
        await tree.sync()
        logger.info(f'{client_instance.user} is now running!')
        logger.info("Synced application commands")
        logger.info("✅ Bot startup completed successfully")
    except Exception as e:
        logger.error(f"Error during startup: {str(e)}")
        raise

@tree.error
async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Global error handler for command tree"""
    try:
        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(
                f"This command is on cooldown. Try again in {error.retry_after:.2f} seconds.",
                ephemeral=True
            )
        elif isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You don't have the required permissions to use this command.",
                ephemeral=True
            )
        else:
            error_msg = handle_error(error)
            await interaction.response.send_message(error_msg, ephemeral=True)
        
        logger.error(f"Error in command {interaction.command.name if interaction.command else 'unknown'}: {str(error)}")
    except Exception as e:
        logger.error(f"Error in error handler: {str(e)}")

@tree.command(name="chat", description="Chat with various AI models")
@app_commands.describe(
    message="The message to send to the AI",
    tts_enabled="Enable text-to-speech for the response",
    model="Select the AI model to use"
)
@app_commands.choices(tts_enabled=[
    app_commands.Choice(name="Yes", value="yes"),
    app_commands.Choice(name="No", value="no")
])
@app_commands.choices(model=[
    app_commands.Choice(name="Claude (Anthropic)", value="anthropic"),
    app_commands.Choice(name="GPT-4o", value="gpt-4o"),
    app_commands.Choice(name="Local Model", value="local-model")
])
@enqueue
async def chat_command(
    interaction: discord.Interaction, 
    message: str, 
    tts_enabled: app_commands.Choice[str] = None,
    model: app_commands.Choice[str] = None
):
    await chat.handle_chat(
        interaction, 
        message, 
        tts_enabled,
        model.value if model else None  # Pass None to use stored preference
    )

@tree.command(name="draw", description="Generate an image with the Dalle3, Stable Diffusion, or Replicate model")
@app_commands.describe(
    prompt="The prompt for image generation",
    enhance="Enhance the prompt using AI (optional)"
)
@app_commands.choices(enhance=[
    app_commands.Choice(name="Yes", value="yes"),
    app_commands.Choice(name="No", value="no")
])
@enqueue
async def draw_command(interaction: discord.Interaction, prompt: str, enhance: app_commands.Choice[str] = None):
    await draw.handle_draw(interaction, prompt, enhance)

@tree.command(name="imagine", description="Animate user profile pictures or an attached image")
@enqueue
async def imagine_command(interaction: discord.Interaction, user: discord.Member = None, attachment: discord.Attachment = None):
    await imagine.handle_imagine(interaction, user, attachment)

@tree.command(name="3d", description="Generate a 3D model from an image")
@enqueue
async def model_3d_command(interaction: discord.Interaction, user: discord.Member = None, attachment: discord.Attachment = None):
    await model_3d.handle_3d(interaction, user, attachment)

@tree.command(name="reset", description="Reset Claude conversation history")
@enqueue
async def reset_command(interaction: discord.Interaction):
    await reset.handle_reset(interaction)

@tree.command(name="clear", description="Clear your personal message history")
@enqueue
async def clear_command(interaction: discord.Interaction):
    await clear.handle_clear(interaction)

@tree.command(name="help", description="Show help for the bot")
@enqueue
async def help_command(interaction: discord.Interaction):
    await help.handle_help(interaction)

@tree.command(name="play", description="Play a YouTube video in your voice channel")
@enqueue
async def play_command(interaction: discord.Interaction, url: str):
    await music.play(interaction, url)

@tree.command(name="stop", description="Stop playback and clear the queue")
@enqueue
async def stop_command(interaction: discord.Interaction):
    await music.stop(interaction)

@tree.command(name="pause", description="Pause the current playback")
@enqueue
async def pause_command(interaction: discord.Interaction):
    await music.pause(interaction)

@tree.command(name="resume", description="Resume paused playback")
@enqueue
async def resume_command(interaction: discord.Interaction):
    await music.resume(interaction)

@tree.command(name="next", description="Skip to the next song in the queue")
@enqueue
async def next_command(interaction: discord.Interaction):
    await music.next(interaction)

@tree.command(name="video", description="Generate a video using AI")
@app_commands.describe(
    prompt="The prompt for video generation"
)
@enqueue
async def video_command(interaction: discord.Interaction, prompt: str):
    await video.handle_video(interaction, prompt)

@tree.command(name="tts", description="Generate text-to-speech audio")
@app_commands.describe(
    text="The text to convert to speech",
    voice="The voice to use for text-to-speech",
    enhance="Enhance the text prompt using AI (optional)"
)
@app_commands.choices(voice=[
    app_commands.Choice(name=voice, value=voice) for voice in tts.VOICES
])
@app_commands.choices(enhance=[
    app_commands.Choice(name="Yes", value="yes"),
    app_commands.Choice(name="No", value="no")
])
@enqueue
async def tts_command(interaction: discord.Interaction, text: str, voice: app_commands.Choice[str], enhance: app_commands.Choice[str] = None):
    await tts.handle_tts(interaction, text, voice.value, enhance)

def run_discord_bot():
    """Start the Discord bot"""
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    if not TOKEN:
        logger.error("DISCORD_BOT_TOKEN not found in environment variables")
        raise ValueError("DISCORD_BOT_TOKEN not found in environment variables")
    
    try:
        client_instance.run(TOKEN)
    except Exception as e:
        logger.error(f"Failed to start bot: {str(e)}")
        raise
