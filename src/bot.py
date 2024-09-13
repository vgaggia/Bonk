import os
import certifi
import uuid
import discord
from discord import app_commands
from src import responses, log
from src.commands import chat, draw, imagine, model_3d, reset, help
from src.ui import draw_buttons, aspect_ratio_view, generate_video_view
from dotenv import load_dotenv
import anthropic
from openai import OpenAI
import asyncio
import io
import random
from src.queue_manager import enqueue

load_dotenv()
os.environ['SSL_CERT_FILE'] = certifi.where()

logger = log.setup_logger(__name__)

anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client_instance = discord.Client(intents=intents)
tree = app_commands.CommandTree(client_instance)

@client_instance.event
async def on_ready():
    await tree.sync()
    logger.info(f'{client_instance.user} is now running!')
    logger.info("Synced application commands.")

@tree.error
async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(f"This command is on cooldown. Try again in {error.retry_after:.2f} seconds.")
    elif isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You don't have the required permissions to use this command.")
    else:
        await interaction.response.send_message(f"An error occurred: {str(error)}")
    logger.error(f"Error in command {interaction.command.name}: {str(error)}")

@tree.command(name="chat", description="Have a chat with Claude")
@enqueue
async def chat_command(interaction: discord.Interaction, message: str):
    await chat.handle_chat(interaction, message)

@tree.command(name="draw", description="Generate an image with the Dalle3, Stable Diffusion, or Replicate model")
@enqueue
async def draw_command(interaction: discord.Interaction, prompt: str):
    await draw.handle_draw(interaction, prompt)

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

@tree.command(name="help", description="Show help for the bot")
@enqueue
async def help_command(interaction: discord.Interaction):
    await help.handle_help(interaction)

def run_discord_bot():
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    client_instance.run(TOKEN)