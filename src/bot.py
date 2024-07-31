import discord
import os
from openai import OpenAI
import anthropic
from discord import app_commands
from src import responses
from src import log
from src import art
from PIL import Image
import io
import warnings

logger = log.setup_logger(__name__)

isPrivate = False
anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

async def send_message(interaction, message):
    try:
        response = await responses.handle_response(message)
        if isPrivate:
            await interaction.user.send(response)
            await interaction.followup.send("> **Info: The answer has been sent via private message. 😉**")
        else:
            await interaction.followup.send(response)
    except Exception as e:
        await interaction.followup.send("> **Error: Something went wrong, please try again later! 😿**")
        logger.exception(f"Error while sending message: {e}")

async def send_start_prompt(client_instance):
    import os.path

    config_dir = os.path.abspath(f"{__file__}/../../")
    prompt_name = 'starting-prompt.txt'
    prompt_path = os.path.join(config_dir, prompt_name)
    discord_channel_id = os.getenv("DISCORD_CHANNEL_ID")
    try:
        if os.path.isfile(prompt_path) and os.path.getsize(prompt_path) > 0:
            with open(prompt_path, "r", encoding="utf-8") as f:
                prompt = f.read()
                if (discord_channel_id):
                    logger.info(f"Send starting prompt with size {len(prompt)}")
                    response = await responses.handle_response(prompt)
                    channel = client_instance.get_channel(int(discord_channel_id))
                    await channel.send(response)
                    logger.info(f"Starting prompt response:{response}")
                else:
                    logger.info("No Channel selected. Skip sending starting prompt.")
    except Exception as e:
        logger.exception(f"Error while sending starting prompt: {e}")

def run_discord_bot():
    client_instance = discord.Client(intents=discord.Intents.default())
    client_instance.tree = app_commands.CommandTree(client_instance)
    client_instance.activity = discord.Activity(type=discord.ActivityType.listening, name="/chat | /help")

    @client_instance.event
    async def on_ready():
        await client_instance.tree.sync()
        logger.info(f'{client_instance.user} is now running!')

    @client_instance.tree.command(name="chat", description="Have a chat with Claude")
    async def chat(interaction: discord.Interaction, *, message: str):
        if interaction.user == client_instance.user:
            return
        username = str(interaction.user)
        channel = str(interaction.channel)
        logger.info(
            f"\x1b[31m{username}\x1b[0m : /chat [{message}] in ({channel})")
        await interaction.response.defer(ephemeral=isPrivate)
        await send_message(interaction, message)

    @client_instance.tree.command(name="draw", description="Generate an image with the Dalle3 or Stable Diffusion model")
    async def draw(interaction: discord.Interaction, *, prompt: str):
        if interaction.user == client_instance.user:
            return

        username = str(interaction.user)
        channel = str(interaction.channel)
        logger.info(f"\x1b[31m{username}\x1b[0m : /draw [{prompt}] in ({channel})")

        await interaction.response.send_message("Preparing image generation options...")
        view = DrawButtons(prompt, interaction)
        await view.start()

    class DrawButtons(discord.ui.View):
        def __init__(self, prompt, interaction):
            super().__init__(timeout=60.0)
            self.prompt = prompt
            self.interaction = interaction
            self.aspect_ratio_view = None

        async def start(self):
            await self.interaction.edit_original_response(content="Select the model you want to use:", view=self)

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
                await interaction.response.defer(thinking=True)
                await self.interaction.edit_original_response(content="Generating image with Dall-E 3...", view=None)
                
                path = await art.draw(self.prompt, model_choice="dalle")
                file = discord.File(path, filename="image.png")
                embed = discord.Embed(title=f"> **{self.prompt}**")
                embed.description = "> **Model: Dall-E 3**"
                embed.set_image(url="attachment://image.png")
                
                await self.interaction.edit_original_response(content=None, attachments=[file], embed=embed, view=None)
                self.stop()
            except Exception as e:
                logger.exception(f"Error in generate_dalle_image: {e}")
                await self.interaction.edit_original_response(content=f"> **Error: {str(e)}**", view=None)
                self.stop()

    class AspectRatioView(discord.ui.View):
        def __init__(self, parent_view):
            super().__init__(timeout=60.0)
            self.parent_view = parent_view

        @discord.ui.button(label="1:1", style=discord.ButtonStyle.secondary)
        async def ratio_1_1(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self.generate_sd_image(interaction, "1:1")

        @discord.ui.button(label="4:3", style=discord.ButtonStyle.secondary)
        async def ratio_4_3(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self.generate_sd_image(interaction, "4:3")

        @discord.ui.button(label="3:4", style=discord.ButtonStyle.secondary)
        async def ratio_3_4(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self.generate_sd_image(interaction, "3:4")

        @discord.ui.button(label="16:9", style=discord.ButtonStyle.secondary)
        async def ratio_16_9(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self.generate_sd_image(interaction, "16:9")

        async def generate_sd_image(self, interaction, aspect_ratio):
            try:
                await interaction.response.defer(thinking=True)
                await self.parent_view.interaction.edit_original_response(content=f"Generating image with Stable Diffusion 3 (Aspect Ratio: {aspect_ratio})...", view=None)
                
                path = await art.draw(self.parent_view.prompt, model_choice="sd", aspect_ratio=aspect_ratio)
                file = discord.File(path, filename="image.png")
                embed = discord.Embed(title=f"> **{self.parent_view.prompt}**")
                embed.description = f"> **Model: Stable Diffusion 3**\n> **Aspect Ratio: {aspect_ratio}**"
                embed.set_image(url="attachment://image.png")
                
                await self.parent_view.interaction.edit_original_response(content=None, attachments=[file], embed=embed, view=None)
                self.parent_view.stop()
            except Exception as e:
                logger.exception(f"Error in generate_sd_image: {e}")
                await self.parent_view.interaction.edit_original_response(content=f"> **Error: {str(e)}**", view=None)
                self.parent_view.stop()

    @client_instance.tree.command(name="private", description="Toggle private access")
    async def private(interaction: discord.Interaction):
        global isPrivate
        await interaction.response.defer(ephemeral=False)
        if not isPrivate:
            isPrivate = not isPrivate
            logger.warning("\x1b[31mSwitch to private mode\x1b[0m")
            await interaction.followup.send(
                "> **Info: Next, the response will be sent via private message. If you want to switch back to public mode, use `/public`**")
        else:
            logger.info("You are already on private mode!")
            await interaction.followup.send(
                "> **Warn: You are already on private mode. If you want to switch to public mode, use `/private`**")

    @client_instance.tree.command(name="public", description="Toggle public access")
    async def public(interaction: discord.Interaction):
        global isPrivate
        await interaction.response.defer(ephemeral=False)
        if isPrivate:
            isPrivate = not isPrivate
            await interaction.followup.send(
                "> **Info: Next, the response will be sent to the channel directly. If you want to switch back to private mode, use `/private`**")
            logger.warning("\x1b[31mSwitch to public mode\x1b[0m")
        else:
            await interaction.followup.send(
                "> **Warn: You are already on public mode. If you want to switch to private mode, use `/private`**")
            logger.info("You are already on public mode!")

    @client_instance.tree.command(name="reset", description="Reset Claude conversation history")
    async def reset(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        await interaction.followup.send("> **Info: I have forgotten everything.**")
        logger.warning("\x1b[31mClaude bot has been successfully reset\x1b[0m")
        await send_start_prompt(client_instance)

    @client_instance.tree.command(name="help", description="Show help for the bot")
    async def help(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        await interaction.followup.send(""":star:**BASIC COMMANDS** \n
        - `/chat [message]` Chat with Claude!
        - `/draw [prompt]` Generate an image with the Dalle3 or Stable Diffusion model
        - `/private` Claude switch to private mode
        - `/public` Claude switch to public mode
        - `/reset` Clear Claude conversation history
        """)
        logger.info("\x1b[31mSomeone needs help!\x1b[0m")

    TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    client_instance.run(TOKEN)