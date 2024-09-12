import os
import certifi
import uuid
import discord
from discord import app_commands
from src import responses, log, art
from PIL import Image
from dotenv import load_dotenv
import anthropic
from openai import OpenAI
import asyncio
import io
import random

load_dotenv()
os.environ['SSL_CERT_FILE'] = certifi.where()

logger = log.setup_logger(__name__)

anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # Add this line to enable member intents

client_instance = discord.Client(intents=intents)
tree = app_commands.CommandTree(client_instance)

@client_instance.event
async def on_ready():
    await tree.sync()
    logger.info(f'{client_instance.user} is now running!')
    logger.info("Synced application commands.")

@tree.command(name="chat", description="Have a chat with Claude")
async def chat(interaction: discord.Interaction, message: str):
    message_id = str(uuid.uuid4())[:8]
    logger.info(f"[{message_id}] Received chat command from {interaction.user} : /chat [{message}] in ({interaction.channel})")
    
    try:
        logger.info(f"[{message_id}] Calling handle_response")
        response = await responses.handle_response(message)
        logger.info(f"[{message_id}] Received response from handle_response")
        
        logger.info(f"[{message_id}] Sending response to user")
        await interaction.response.send_message(response)
        logger.info(f"[{message_id}] Response sent to user")
    except Exception as e:
        logger.exception(f"[{message_id}] Error in chat command: {str(e)}")
        await interaction.response.send_message("An error occurred while processing your request.")

@tree.command(name="draw", description="Generate an image with the Dalle3, Stable Diffusion, or Replicate model")
async def draw(interaction: discord.Interaction, prompt: str):
    username = str(interaction.user)
    channel = str(interaction.channel)
    logger.info(f"\x1b[31m{username}\x1b[0m : /draw [{prompt}] in ({channel})")

    try:
        await interaction.response.defer(thinking=True)
        view = DrawButtons(prompt, interaction)
        await view.start()
    except Exception as e:
        logger.exception(f"Error in draw command: {str(e)}")
        await interaction.followup.send("An error occurred while preparing image generation options.")

class DrawButtons(discord.ui.View):
    def __init__(self, prompt, interaction):
        super().__init__(timeout=60.0)
        self.prompt = prompt
        self.interaction = interaction
        self.aspect_ratio_view = None
        self.image_path = None
        self.interaction_completed = False  # New flag

    async def start(self):
        await self.interaction.followup.send(content="Select the model you want to use:", view=self)

    @discord.ui.button(label="Dall-E 3", style=discord.ButtonStyle.primary)
    async def dalle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_dalle_image(interaction)

    @discord.ui.button(label="Stable Diffusion 3", style=discord.ButtonStyle.secondary)
    async def stable_diffusion_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.aspect_ratio_view:
            self.aspect_ratio_view = AspectRatioView(self)
        await interaction.response.edit_message(content="Select the aspect ratio:", view=self.aspect_ratio_view)

    @discord.ui.button(label="Replicate", style=discord.ButtonStyle.success)
    async def replicate_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.aspect_ratio_view:
            self.aspect_ratio_view = AspectRatioView(self, model="replicate")
        await interaction.response.edit_message(content="Select the aspect ratio for Replicate:", view=self.aspect_ratio_view)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Image generation canceled.", view=None)
        self.interaction_completed = True  # Mark as completed
        self.stop()

    async def on_timeout(self):
        if not self.interaction_completed:  # Only send timeout message if interaction wasn't completed
            try:
                await self.interaction.edit_original_response(content="Image generation canceled due to timeout", view=None)
            except discord.errors.NotFound:
                # If the message was deleted, ignore the error
                pass
        self.stop()

    async def generate_dalle_image(self, interaction):
        try:
            await interaction.response.edit_message(content="Generating image with Dall-E 3...", view=None)
            
            self.image_path = await art.draw(self.prompt, model_choice="dalle")
            file = discord.File(self.image_path, filename="image.png")
            embed = discord.Embed(title=f"> **{self.prompt}**")
            embed.description = "> **Model: Dall-E 3**"
            embed.set_image(url="attachment://image.png")
            
            # Create a new view with only the Generate Video button
            view = GenerateVideoView(self.image_path)
            
            await interaction.edit_original_response(content=None, attachments=[file], embed=embed, view=view)
            self.interaction_completed = True  # Mark as completed
            self.stop()
        except Exception as e:
            logger.exception(f"Error in generate_dalle_image: {str(e)}")
            await interaction.edit_original_response(content=f"> **Error: An error occurred while generating the image.**", view=None)
            self.interaction_completed = True  # Mark as completed
            self.stop()

class AspectRatioView(discord.ui.View):
    def __init__(self, parent_view, model="sd"):
        super().__init__(timeout=60.0)
        self.parent_view = parent_view
        self.model = model

    @discord.ui.button(label="16:9", style=discord.ButtonStyle.secondary)
    async def ratio_16_9(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_image(interaction, "16:9")

    @discord.ui.button(label="1:1", style=discord.ButtonStyle.secondary)
    async def ratio_1_1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_image(interaction, "1:1")

    @discord.ui.button(label="21:9", style=discord.ButtonStyle.secondary)
    async def ratio_21_9(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_image(interaction, "21:9")

    @discord.ui.button(label="2:3", style=discord.ButtonStyle.secondary)
    async def ratio_2_3(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_image(interaction, "2:3")

    @discord.ui.button(label="3:2", style=discord.ButtonStyle.secondary)
    async def ratio_3_2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_image(interaction, "3:2")

    @discord.ui.button(label="4:5", style=discord.ButtonStyle.secondary)
    async def ratio_4_5(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_image(interaction, "4:5")

    @discord.ui.button(label="5:4", style=discord.ButtonStyle.secondary)
    async def ratio_5_4(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_image(interaction, "5:4")

    @discord.ui.button(label="9:16", style=discord.ButtonStyle.secondary)
    async def ratio_9_16(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_image(interaction, "9:16")

    @discord.ui.button(label="9:21", style=discord.ButtonStyle.secondary)
    async def ratio_9_21(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_image(interaction, "9:21")

    async def generate_image(self, interaction, aspect_ratio):
        try:
            model_name = "Stable Diffusion 3" if self.model == "sd" else "Replicate"
            await interaction.response.defer(thinking=True)
            await interaction.followup.send(f"Generating image with {model_name} (Aspect Ratio: {aspect_ratio})... This may take a minute or two.")
            
            self.parent_view.image_path = await art.draw(self.parent_view.prompt, model_choice=self.model, aspect_ratio=aspect_ratio)
            file = discord.File(self.parent_view.image_path, filename="image.png")
            embed = discord.Embed(title=f"> **{self.parent_view.prompt}**")
            embed.description = f"> **Model: {model_name}**\n> **Aspect Ratio: {aspect_ratio}**"
            embed.set_image(url="attachment://image.png")
            
            # Create a new view with only the Generate Video button
            view = GenerateVideoView(self.parent_view.image_path)
            
            await interaction.edit_original_response(content=None, attachments=[file], embed=embed, view=view)
            self.parent_view.interaction_completed = True  # Mark as completed
            self.parent_view.stop()
        except Exception as e:
            logger.exception(f"Error in generate_image: {str(e)}")
            await interaction.edit_original_response(content=f"> **Error: An error occurred while generating the image.**", view=None)
            self.parent_view.interaction_completed = True  # Mark as completed
            self.parent_view.stop()

    async def on_timeout(self):
        if not self.parent_view.interaction_completed:  # Only send timeout message if interaction wasn't completed
            try:
                await self.parent_view.interaction.edit_original_response(content="Image generation canceled due to timeout", view=None)
            except discord.errors.NotFound:
                # If the message was deleted, ignore the error
                pass
        self.stop()

class GenerateVideoView(discord.ui.View):
    def __init__(self, image_path):
        super().__init__()
        self.image_path = image_path

    @discord.ui.button(label="Generate Video", style=discord.ButtonStyle.success)
    async def generate_video_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(thinking=True)
        try:
            video_path = await art.generate_video(self.image_path)
            file = discord.File(video_path, filename="video.mp4")
            await interaction.followup.send(content="Here's your generated video:", file=file)
            
            # Remove the button from the original message
            self.clear_items()
            await interaction.message.edit(view=self)
        except Exception as e:
            logger.exception(f"Error in generate_video_button: {str(e)}")
            await interaction.followup.send(content="An error occurred while generating the video.")

@tree.command(name="imagine", description="Animate user profile pictures or an attached image")
async def imagine(interaction: discord.Interaction, user: discord.Member = None):
    await interaction.response.defer(thinking=True)
    
    try:
        if user:
            # Animate user's profile picture
            avatar_url = user.avatar.url if user.avatar else user.default_avatar.url
            image_path = await art.download_image_from_url(avatar_url)
        elif interaction.message and interaction.message.attachments:
            # Animate the attached image
            attachment = interaction.message.attachments[0]
            image_path = await art.download_image_from_url(attachment.url)
        else:
            # Animate a random user's profile picture
            guild_members = interaction.guild.members
            random_user = random.choice(guild_members)
            avatar_url = random_user.avatar.url if random_user.avatar else random_user.default_avatar.url
            image_path = await art.download_image_from_url(avatar_url)

        video_path = await art.generate_video(image_path)
        file = discord.File(video_path, filename="animated.mp4")
        await interaction.followup.send(content="Here's your animated image:", file=file)

    except Exception as e:
        logger.exception(f"Error in imagine command: {str(e)}")
        await interaction.followup.send(content="An error occurred while processing the image.")

@tree.command(name="reset", description="Reset Claude conversation history")
async def reset(interaction: discord.Interaction):
    await interaction.response.defer()
    await interaction.followup.send("> **Info: I have forgotten everything.**")
    logger.warning("\x1b[31mClaude bot has been successfully reset\x1b[0m")

@tree.command(name="help", description="Show help for the bot")
async def help(interaction: discord.Interaction):
    await interaction.response.defer()
    await interaction.followup.send(""":star:**BASIC COMMANDS** \n
    - `/chat [message]` Chat with Claude!
    - `/draw [prompt]` Generate an image with the Dalle3, Stable Diffusion, or Replicate model
    - `/imagine [user]` Animate a user's profile picture or an attached image
    - `/reset` Clear Claude conversation history
    """)
    logger.info("\x1b[31mSomeone needs help!\x1b[0m")

def run_discord_bot():
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    client_instance.run(TOKEN)