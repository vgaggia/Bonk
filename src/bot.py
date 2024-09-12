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

load_dotenv()
os.environ['SSL_CERT_FILE'] = certifi.where()

logger = log.setup_logger(__name__)

anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

intents = discord.Intents.default()
intents.message_content = True

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

@tree.command(name="draw", description="Generate an image with the Dalle3 or Stable Diffusion model")
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

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Image generation canceled.", view=None)
        self.stop()

    async def on_timeout(self):
        await self.interaction.edit_original_response(content="Image generation canceled due to timeout", view=None)

    async def generate_dalle_image(self, interaction):
        try:
            await interaction.response.edit_message(content="Generating image with Dall-E 3...", view=None)
            
            path = await art.draw(self.prompt, model_choice="dalle")
            file = discord.File(path, filename="image.png")
            embed = discord.Embed(title=f"> **{self.prompt}**")
            embed.description = "> **Model: Dall-E 3**"
            embed.set_image(url="attachment://image.png")
            
            await interaction.edit_original_response(content=None, attachments=[file], embed=embed, view=None)
            self.stop()
        except Exception as e:
            logger.exception(f"Error in generate_dalle_image: {str(e)}")
            await interaction.edit_original_response(content=f"> **Error: An error occurred while generating the image.**", view=None)
            self.stop()

class AspectRatioView(discord.ui.View):
    def __init__(self, parent_view):
        super().__init__(timeout=60.0)
        self.parent_view = parent_view

    @discord.ui.button(label="16:9", style=discord.ButtonStyle.secondary)
    async def ratio_16_9(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_sd_image(interaction, "16:9")

    @discord.ui.button(label="1:1", style=discord.ButtonStyle.secondary)
    async def ratio_1_1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_sd_image(interaction, "1:1")

    @discord.ui.button(label="21:9", style=discord.ButtonStyle.secondary)
    async def ratio_21_9(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_sd_image(interaction, "21:9")

    @discord.ui.button(label="2:3", style=discord.ButtonStyle.secondary)
    async def ratio_2_3(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_sd_image(interaction, "2:3")

    @discord.ui.button(label="3:2", style=discord.ButtonStyle.secondary)
    async def ratio_3_2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_sd_image(interaction, "3:2")

    @discord.ui.button(label="4:5", style=discord.ButtonStyle.secondary)
    async def ratio_4_5(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_sd_image(interaction, "4:5")

    @discord.ui.button(label="5:4", style=discord.ButtonStyle.secondary)
    async def ratio_5_4(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_sd_image(interaction, "5:4")

    @discord.ui.button(label="9:16", style=discord.ButtonStyle.secondary)
    async def ratio_9_16(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_sd_image(interaction, "9:16")

    @discord.ui.button(label="9:21", style=discord.ButtonStyle.secondary)
    async def ratio_9_21(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_sd_image(interaction, "9:21")

    async def generate_sd_image(self, interaction, aspect_ratio):
        try:
            await interaction.response.edit_message(content=f"Generating image with Stable Diffusion 3 (Aspect Ratio: {aspect_ratio})...", view=None)
            
            path = await art.draw(self.parent_view.prompt, model_choice="sd", aspect_ratio=aspect_ratio)
            file = discord.File(path, filename="image.png")
            embed = discord.Embed(title=f"> **{self.parent_view.prompt}**")
            embed.description = f"> **Model: Stable Diffusion 3**\n> **Aspect Ratio: {aspect_ratio}**"
            embed.set_image(url="attachment://image.png")
            
            await interaction.edit_original_response(content=None, attachments=[file], embed=embed, view=None)
            self.parent_view.stop()
        except Exception as e:
            logger.exception(f"Error in generate_sd_image: {str(e)}")
            await interaction.edit_original_response(content=f"> **Error: An error occurred while generating the image.**", view=None)
            self.parent_view.stop()

    async def on_timeout(self):
        await self.parent_view.interaction.edit_original_response(content="Image generation canceled due to timeout", view=None)

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
    - `/draw [prompt]` Generate an image with the Dalle3 or Stable Diffusion model
    - `/reset` Clear Claude conversation history
    """)
    logger.info("\x1b[31mSomeone needs help!\x1b[0m")

def run_discord_bot():
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    client_instance.run(TOKEN)