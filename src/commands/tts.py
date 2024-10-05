import discord
from discord import app_commands
import os
from pathlib import Path
from openai import OpenAI
from src import log

logger = log.setup_logger(__name__)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]

class VoiceSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=voice, value=voice) for voice in VOICES]
        super().__init__(placeholder="Choose a voice", max_values=1, min_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await generate_speech(interaction, self.values[0], self.view.text)

class VoiceView(discord.ui.View):
    def __init__(self, text):
        super().__init__()
        self.add_item(VoiceSelect())
        self.text = text

async def generate_speech(interaction: discord.Interaction, voice: str, text: str):
    try:
        speech_file_path = Path(__file__).parent / f"speech_{interaction.id}.mp3"
        response = client.audio.speech.create(
            model="tts-1",
            voice=voice,
            input=text
        )

        response.stream_to_file(speech_file_path)

        await interaction.followup.send(f"Here's your TTS audio using the {voice} voice:", file=discord.File(speech_file_path))
        os.remove(speech_file_path)
    except Exception as e:
        logger.error(f"Error generating TTS: {str(e)}")
        await interaction.followup.send(f"An error occurred while generating the TTS audio: {str(e)}")

async def handle_tts(interaction: discord.Interaction, text: str):
    await interaction.response.send_message("Please select a voice for the TTS:", view=VoiceView(text))