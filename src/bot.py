import discord
import os
import openai
from random import randrange
from discord import app_commands
from src import responses
from src import log
from src import art
from src import personas
from PIL import Image
import io
import warnings

logger = log.setup_logger(__name__)

isPrivate = False



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
                    chat_model = os.getenv("CHAT_MODEL")
                    response = ""
                    if chat_model == "OFFICIAL":
                        response = f"{response}{await responses.official_handle_response(prompt)}"
                    elif chat_model == "UNOFFICIAL":
                        response = f"{response}{await responses.unofficial_handle_response(prompt)}"
                    channel = client_instance.get_channel(int(discord_channel_id))
                    await channel.send(response)
                    logger.info(f"Starting prompt response:{response}")
                else:
                    logger.info("No Channel selected. Skip sending starting prompt.")
        else:
            logger.info(f"No {prompt_name}. Skip sending starting prompt.")
    except Exception as e:
        logger.exception(f"Error while sending starting prompt: {e}")


async def send_start_prompt(client):
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
                    chat_model = os.getenv("CHAT_MODEL")
                    response = ""
                    if chat_model == "OFFICIAL":
                        response = f"{response}{await responses.official_handle_response(prompt)}"
                    elif chat_model == "UNOFFICIAL":
                        response = f"{response}{await responses.unofficial_handle_response(prompt)}"
                    channel = client.get_channel(int(discord_channel_id))
                    await channel.send(response)
                    logger.info(f"Starting prompt response:{response}")
                else:
                    logger.info("No Channel selected. Skip sending starting prompt.")
        else:
            logger.info(f"No {prompt_name}. Skip sending starting prompt.")
    except Exception as e:
        logger.exception(f"Error while sending starting prompt: {e}")

def run_discord_bot():
    client_instance = discord.Client(intents=discord.Intents.default())
    client_instance.tree = app_commands.CommandTree(client_instance)
    client_instance.activity = discord.Activity(type=discord.ActivityType.listening, name="/chat | /help")

    @client_instance.event
    async def on_ready():
        #await send_start_prompt(client_instance)
        await client_instance.tree.sync()
        logger.info(f'{client_instance.user} is now running!')



    @client_instance.tree.command(name="chat", description="Have a chat with ChatGPT")
    async def chat(interaction: discord.Interaction, *, message: str):
        isReplyAll =  os.getenv("REPLYING_ALL")
        if isReplyAll == "True":
            await interaction.response.defer(ephemeral=False)
            await interaction.followup.send(
                "> **Warn: You already on replyAll mode. If you want to use slash command, switch to normal mode, use `/replyall` again**")
            logger.warning("\x1b[31mYou already on replyAll mode, can't use slash command!\x1b[0m")
            return
        if interaction.user == client.user:
            return
        username = str(interaction.user)
        channel = str(interaction.channel)
        logger.info(
            f"\x1b[31m{username}\x1b[0m : /chat [{message}] in ({channel})")
        await send_message(interaction, message)


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
            logger.info("You already on private mode!")
            await interaction.followup.send(
                "> **Warn: You already on private mode. If you want to switch to public mode, use `/public`**")


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
                "> **Warn: You already on public mode. If you want to switch to private mode, use `/private`**")
            logger.info("You already on public mode!")


    @client_instance.tree.command(name="replyall", description="Toggle replyAll access")
    async def replyall(interaction: discord.Interaction):
        isReplyAll = os.getenv("REPLYING_ALL")
        os.environ["REPLYING_ALL_DISCORD_CHANNEL_ID"] = str(interaction.channel_id)
        await interaction.response.defer(ephemeral=False)
        if isReplyAll == "True":
            os.environ["REPLYING_ALL"] = "False"
            await interaction.followup.send(
                "> **Info: The bot will only response to the slash command `/chat` next. If you want to switch back to replyAll mode, use `/replyAll` again.**")
            logger.warning("\x1b[31mSwitch to normal mode\x1b[0m")
        elif isReplyAll == "False":
            os.environ["REPLYING_ALL"] = "True"
            await interaction.followup.send(
                "> **Info: Next, the bot will response to all message in this channel only.If you want to switch back to normal mode, use `/replyAll` again.**")
            logger.warning("\x1b[31mSwitch to replyAll mode\x1b[0m")


    # @client.tree.command(name="chat-model", description="Switch different chat model")
    # @app_commands.choices(choices=[
    #     app_commands.Choice(name="Official GPT-3.5", value="OFFICIAL"),
    #     app_commands.Choice(name="Website ChatGPT", value="UNOFFICIAL")
    # ])
    # async def chat_model(interaction: discord.Interaction, choices: app_commands.Choice[str]):
    #     await interaction.response.defer(ephemeral=False)
    #     if choices.value == "OFFICIAL":
    #         responses.chatbot = responses.get_chatbot_model("OFFICIAL")
    #         os.environ["CHAT_MODEL"] = "OFFICIAL"
    #         await interaction.followup.send(
    #             "> **Info: You are now in Official GPT-3.5 model.**\n> You need to set your `OPENAI_API_KEY` in `env` file.")
    #         logger.warning("\x1b[31mSwitch to OFFICIAL chat model\x1b[0m")
    #     elif choices.value == "UNOFFICIAL":
    #         responses.chatbot = responses.get_chatbot_model("UNOFFICIAL")
    #         os.environ["CHAT_MODEL"] = "UNOFFICIAL"
    #         await interaction.followup.send(
    #             "> **Info: You are now in Website ChatGPT model.**\n> You need to set your `SESSION_TOKEN` or `OPENAI_EMAIL` and `OPENAI_PASSWORD` in `env` file.")
    #         logger.warning("\x1b[31mSwitch to UNOFFICIAL(Website) chat model\x1b[0m")


    @client_instance.tree.command(name="reset", description="Complete reset ChatGPT conversation history")
    async def reset(interaction: discord.Interaction):
        chat_model = os.getenv("CHAT_MODEL")
        if chat_model == "OFFICIAL":
            responses.chatbot.reset()
        elif chat_model == "UNOFFICIAL":
            responses.chatbot.reset_chat()
        await interaction.response.defer(ephemeral=False)
        await interaction.followup.send("> **Info: I have forgotten everything.**")
        personas.current_persona = "standard"
        logger.warning(
            "\x1b[31mChatGPT bot has been successfully reset\x1b[0m")
        await send_start_prompt(client)


    @client_instance.tree.command(name="help", description="Show help for the bot")
    async def help(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        await interaction.followup.send(""":star:**BASIC COMMANDS** \n
        - `/chat [message]` Chat with ChatGPT!
        - `/draw [prompt]` Generate an image with the Dalle2 model
        - `/switchpersona [persona]` Switch between optional chatGPT jailbreaks
                `random`: Picks a random persona
                `chatgpt`: Standard chatGPT mode
                `dan`: Dan Mode 11.0, infamous Do Anything Now Mode
                `sda`: Superior DAN has even more freedom in DAN Mode
                `confidant`: Evil Confidant, evil trusted confidant
                `based`: BasedGPT v2, sexy gpt
                `oppo`: OPPO says exact opposite of what chatGPT would say
                `dev`: Developer Mode, v2 Developer mode enabled

        - `/private` ChatGPT switch to private mode
        - `/public` ChatGPT switch to public mode
        - `/replyall` ChatGPT switch between replyAll mode and default mode
        - `/reset` Clear ChatGPT conversation history
        - `/chat-model` Switch different chat model
                `OFFICIAL`: GPT-3.5 model
                `UNOFFICIAL`: Website ChatGPT
                Modifying CHAT_MODEL field in the .env file change the default model""")

        logger.info(
            "\x1b[31mSomeone needs help!\x1b[0m")

    @client_instance.tree.command(name="draw", description="Generate an image with the Dalle2 or Stable Diffusion model")
    async def draw(interaction: discord.Interaction, *, prompt: str):
        isReplyAll = os.getenv("REPLYING_ALL")
        if isReplyAll == "True":
            await interaction.response.defer(ephemeral=False)
            await interaction.followup.send(
                "> **Warn: You already on replyAll mode. If you want to use slash command, switch to normal mode, use `/replyall` again**")
            logger.warning("\x1b[31mYou already on replyAll mode, can't use slash command!\x1b[0m")
            return
        if interaction.user == client_instance.user:
            return

        username = str(interaction.user)
        channel = str(interaction.channel)
        logger.info(f"\x1b[31m{username}\x1b[0m : /draw [{prompt}] in ({channel})")

        view = DrawButtons(prompt)
        await view.start(interaction)

    class DrawButtons(discord.ui.View):
        def __init__(self, prompt):
            super().__init__(timeout=60.0)
            self.prompt = prompt
            self.message = None  # Add a reference to the message containing the buttons
            self.buttons_message = None  # Initialize the buttons_message attribute

        async def start(self, interaction: discord.Interaction):
            self.message = await interaction.response.send_message("Select the model you want to use:", view=self)
            self.buttons_message = self.message
            
        @discord.ui.button(label="Dall-E 3", style=discord.ButtonStyle.primary)
        async def dalle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            try:
                await interaction.response.defer()
                await interaction.message.edit(content="Generating image...", view=None)
                path = await art.draw(self.prompt, model_choice="dalle")
                file = discord.File(path, filename="image.png")
                embed = discord.Embed(title=f"> **{self.prompt}**")
                embed.description = "> **Model: Dall-E 3**"
                embed.set_image(url="attachment://image.png")
                await interaction.message.edit(content=None, attachments=[file], embed=embed, view=None)
                self.stop()
            except Exception as e:
                await interaction.message.edit(content=f"> **Error: {str(e)}**", embed=None, view=None)

        @discord.ui.button(label="Stable Diffusion 3", style=discord.ButtonStyle.secondary)
        async def stable_diffusion_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            try:
                await interaction.response.defer()
                await interaction.message.edit(content="Generating image...", view=None)
                path = await art.draw(self.prompt, model_choice="sd")
                file = discord.File(path, filename="image.png")
                embed = discord.Embed(title=f"> **{self.prompt}**")
                embed.description = "> **Model: Stable Diffusion 3**"
                embed.set_image(url="attachment://image.png")
                await interaction.message.edit(content=None, attachments=[file], embed=embed, view=None)
                self.stop()
            except Exception as e:
                await interaction.message.edit(content=f"> **Error: {str(e)}**", embed=None, view=None)
        @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
        async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                await interaction.message.delete()  # Delete the original interaction response message
                await interaction.response.send_message("Image generation canceled.", ephemeral=True)

        async def on_timeout(self):
                await interaction.message.delete()  # Delete the original interaction response message
                await interaction.response.send_message("Image generation canceled due to timeout", ephemeral=True)

    @client_instance.tree.command(name="switchpersona", description="Switch between optional chatGPT jailbreaks")
    @app_commands.choices(persona=[
        app_commands.Choice(name="Random", value="random"),
        app_commands.Choice(name="Standard", value="standard"),
        app_commands.Choice(name="Do Anything Now 11.0", value="dan"),
        app_commands.Choice(name="Superior Do Anything", value="sda"),
        app_commands.Choice(name="Evil Confidant", value="confidant"),
        app_commands.Choice(name="BasedGPT v2", value="based"),
        app_commands.Choice(name="OPPO", value="oppo"),
        app_commands.Choice(name="Developer Mode v2", value="dev")
    ])
    async def chat(interaction: discord.Interaction, persona: app_commands.Choice[str]):
        isReplyAll =  os.getenv("REPLYING_ALL")
        if isReplyAll == "True":
            await interaction.response.defer(ephemeral=False)
            await interaction.followup.send(
                "> **Warn: You already on replyAll mode. If you want to use slash command, switch to normal mode, use `/replyall` again**")
            logger.warning("\x1b[31mYou already on replyAll mode, can't use slash command!\x1b[0m")
            return
        if interaction.user == client.user:
            return

        await interaction.response.defer(thinking=True)
        username = str(interaction.user)
        channel = str(interaction.channel)
        logger.info(
            f"\x1b[31m{username}\x1b[0m : '/switchpersona [{persona.value}]' ({channel})")

        persona = persona.value

        if persona == personas.current_persona:
            await interaction.followup.send(f"> **Warn: Already set to `{persona}` persona**")

        elif persona == "standard":
            chat_model = os.getenv("CHAT_MODEL")
            if chat_model == "OFFICIAL":
                responses.chatbot.reset()
            elif chat_model == "UNOFFICIAL":
                responses.chatbot.reset_chat()

            personas.current_persona = "standard"
            await interaction.followup.send(
                f"> **Info: Switched to `{persona}` persona**")

        elif persona == "random":
            choices = list(personas.PERSONAS.keys())
            choice = randrange(0, 6)
            chosen_persona = choices[choice]
            personas.current_persona = chosen_persona
            await responses.switch_persona(chosen_persona)
            await interaction.followup.send(
                f"> **Info: Switched to `{chosen_persona}` persona**")


        elif persona in personas.PERSONAS:
            try:
                await responses.switch_persona(persona)
                personas.current_persona = persona
                await interaction.followup.send(
                f"> **Info: Switched to `{persona}` persona**")
            except Exception as e:
                await interaction.followup.send(
                    "> **Error: Something went wrong, please try again later! 😿**")
                logger.exception(f"Error while switching persona: {e}")

        else:
            await interaction.followup.send(
                f"> **Error: No available persona: `{persona}` 😿**")
            logger.info(
                f'{username} requested an unavailable persona: `{persona}`')

    @client_instance.event
    async def on_message(message):
        isReplyAll =  os.getenv("REPLYING_ALL")
        if isReplyAll == "True" and message.channel.id == int(os.getenv("REPLYING_ALL_DISCORD_CHANNEL_ID")):
            if message.author == client.user:
                return
            username = str(message.author)
            user_message = str(message.content)
            channel = str(message.channel)
            logger.info(f"\x1b[31m{username}\x1b[0m : '{user_message}' ({channel})")
            await send_message(message, user_message)

    TOKEN = os.getenv("DISCORD_BOT_TOKEN")

    client_instance.run(TOKEN)
