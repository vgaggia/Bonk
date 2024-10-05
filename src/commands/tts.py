import discord
from discord import app_commands
import os
from pathlib import Path
from openai import OpenAI
from src import log
import asyncio

logger = log.setup_logger(__name__)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]

async def generate_speech(text: str, voice: str) -> Path:
    speech_file_path = Path("temp_audio.mp3")
    response = client.audio.speech.create(
        model="tts-1",
        voice=voice,
        input=text
    )
    response.stream_to_file(speech_file_path)
    return speech_file_path

async def play_audio(voice_client, audio_path):
    audio_source = discord.FFmpegPCMAudio(audio_path)
    if not voice_client.is_playing():
        voice_client.play(audio_source, after=lambda e: print('Player error: %s' % e) if e else None)
        while voice_client.is_playing():
            await asyncio.sleep(1)
    else:
        print("Audio is already playing.")

async def handle_tts(interaction: discord.Interaction, text: str, voice: str):
    if not interaction.user.voice:
        await interaction.followup.send("You need to be in a voice channel to use this command.")
        return

    try:
        # Generate speech
        audio_file = await generate_speech(text, voice)

        # Join voice channel
        voice_channel = interaction.user.voice.channel
        voice_client = await voice_channel.connect()

        # Play audio
        await play_audio(voice_client, audio_file)

        # Disconnect after playing
        await voice_client.disconnect()

        # Delete the temporary audio file
        os.remove(audio_file)

        await interaction.followup.send(f"TTS audio played successfully using the {voice} voice.")
    except Exception as e:
        logger.error(f"Error in TTS command: {str(e)}")
        await interaction.followup.send(f"An error occurred: {str(e)}")
        if interaction.guild.voice_client:
            await interaction.guild.voice_client.disconnect()

class VoiceSelect(discord.ui.Select):
    def __init__(self, text: str):
        options = [discord.SelectOption(label=voice, value=voice) for voice in VOICES]
        super().__init__(placeholder="Select a voice", options=options)
        self.text = text

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await handle_tts(interaction, self.text, self.values[0])

class TTSView(discord.ui.View):
    def __init__(self, text: str):
        super().__init__()
        self.add_item(VoiceSelect(text))

async def tts_command(interaction: discord.Interaction, text: str):
    view = TTSView(text)
    await interaction.response.send_message("Select a voice for TTS:", view=view, ephemeral=True)

def setup(bot):
    @bot.tree.command(name="tts", description="Generate and play text-to-speech audio")
    @app_commands.describe(text="The text to convert to speech")
    async def tts(interaction: discord.Interaction, text: str):
        await tts_command(interaction, text)