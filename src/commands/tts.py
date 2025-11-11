import asyncio
import os
from pathlib import Path

import discord
from openai import OpenAI

from src import log, responses
from src.voice import ffmpeg_available, ffmpeg_executable
from src.voice_session_manager import voice_session_manager

logger = log.setup_logger(__name__)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]


async def generate_speech(text: str, voice: str) -> Path:
    import time
    # Use unique filename to prevent overwriting queued TTS
    timestamp = int(time.time() * 1000)
    speech_file_path = Path(f"temp_tts_{timestamp}.mp3")

    response = client.audio.speech.create(model="tts-1", voice=voice, input=text)

    # Use stream_to_file from OpenAI SDK
    response.stream_to_file(speech_file_path)

    logger.info(f"Generated TTS file: {speech_file_path}, size: {speech_file_path.stat().st_size}")
    return speech_file_path


async def play_audio(voice_client, audio_path):
    """Play audio using the voice session manager to handle queuing."""
    audio_source = discord.FFmpegPCMAudio(audio_path, executable=ffmpeg_executable())

    def after_callback(e):
        if e:
            logger.error(f'Player error: {e}')

    # Use the session manager to queue audio properly
    await voice_session_manager.queue_audio(voice_client, audio_source, after_callback)


async def handle_tts(
    interaction: discord.Interaction,
    text: str,
    voice: str,
    enhance: discord.app_commands.Choice[str] = None,
    placeholder=None,
):
    username = str(interaction.user)

    try:
        # Check if prompt enhancement is requested
        if enhance and enhance.value == "yes":
            logger.info(f"Enhancing TTS prompt: {text}")

            # Use the existing enhance_prompt function to improve the text, passing 'tts' context
            enhanced_text = await responses.enhance_prompt(text, context='tts')

            text = enhanced_text
            logger.info(f"Enhanced TTS prompt: {text}")

        if not interaction.user.voice:
            await interaction.edit_original_response(
                content="You need to be in a voice channel to use this command.", view=None
            )
            if placeholder:
                from src.commands.music import music_player
                music_player.remove_tts_slot(placeholder)
            return

        # Pre-flight checks for audio stack
        if not ffmpeg_available():
            await interaction.edit_original_response(
                content="FFmpeg not found. Install FFmpeg and ensure it's on PATH or set FFMPEG_BIN.",
                view=None,
            )
            if placeholder:
                from src.commands.music import music_player
                music_player.remove_tts_slot(placeholder)
            return

        # Note: Opus check bypassed - FFmpeg has built-in Opus support
        # This resolves DLL loading issues on Windows while maintaining functionality

        # Check if music is currently playing
        from src.commands.music import music_player

        # Generate speech with OpenAI (this takes time)
        try:
            audio_file = await generate_speech(text, voice)
            # Ensure file is fully written and closed
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Failed to generate TTS: {e}")
            if placeholder:
                music_player.remove_tts_slot(placeholder)
            await interaction.edit_original_response(
                content=f"Failed to generate TTS: {str(e)}", view=None
            )
            return

        # If we reserved a slot, update it with the actual file
        if placeholder:
            music_player.update_tts_slot(placeholder, str(audio_file), title=f"TTS: {text[:30]}...")
            await interaction.edit_original_response(
                content=f"TTS queued to play after current song using the {voice} voice.", view=None
            )
            logger.info("TTS slot updated with generated audio")
            return

        # No music playing - play TTS normally
        voice_client = await voice_session_manager.get_or_connect(interaction)
        if not voice_client:
            await interaction.edit_original_response(
                content="Couldn't connect to voice. Make sure you're in a voice channel and I have permissions.",
                view=None,
            )
            return

        # Play audio immediately
        await play_audio(voice_client, audio_file)

        # Delete the temporary audio file
        os.remove(audio_file)

        await interaction.edit_original_response(
            content=f"TTS audio played successfully using the {voice} voice.", view=None
        )
    except Exception as e:
        logger.error(f"Error in TTS command for {username}: {str(e)}")
        try:
            await interaction.edit_original_response(
                content=f"An error occurred: {str(e)}", view=None
            )
        except Exception:
            pass
        if placeholder:
            from src.commands.music import music_player
            music_player.remove_tts_slot(placeholder)
        if interaction.guild.voice_client:
            voice_session_manager.cancel_session(interaction.guild.id)


async def handle_tts_for_chat(interaction: discord.Interaction, text: str):
    if not interaction.user.voice:
        await interaction.followup.send("You need to be in a voice channel to use TTS.")
        return

    try:
        # Generate speech using a default OpenAI voice
        audio_file = await generate_speech(text, "alloy")

        # Join voice channel using session manager
        voice_client = await voice_session_manager.get_or_connect(interaction)
        if not voice_client:
            await interaction.followup.send(
                "Couldn't connect to voice. Make sure you're in a voice channel and I have permissions."
            )
            return

        # Play audio (queued if something else is playing)
        await play_audio(voice_client, audio_file)

        # Note: Voice client will auto-disconnect after 3 minutes of inactivity

        # Delete the temporary audio file
        os.remove(audio_file)

        await interaction.followup.send("TTS audio played successfully.")
    except Exception as e:
        logger.error(f"Error in TTS for chat: {str(e)}")
        await interaction.followup.send(f"An error occurred while playing TTS: {str(e)}")
        if interaction.guild.voice_client:
            voice_session_manager.cancel_session(interaction.guild.id)


class VoiceSelect(discord.ui.Select):
    def __init__(self, text: str, enhance: bool = False):
        options = [discord.SelectOption(label=voice, value=voice) for voice in VOICES]
        super().__init__(placeholder="Select a voice", options=options)
        self.text = text
        self.enhance = enhance

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)

        # Reserve queue slot NOW (when voice is selected) to prevent race conditions
        from src.commands.music import music_player
        placeholder = None
        if music_player.is_playing and music_player.voice_client:
            placeholder = music_player.reserve_tts_slot(title=f"TTS: {self.text[:30]}... (generating)")
            await interaction.edit_original_response(
                content=f"Generating TTS using {self.values[0]} voice...", view=None
            )

        # Create enhance choice object if needed
        enhance_choice = None
        if self.enhance:
            enhance_choice = discord.app_commands.Choice(name="yes", value="yes")

        # Pass placeholder to handle_tts
        await handle_tts(interaction, self.text, self.values[0], enhance_choice, placeholder)


class TTSView(discord.ui.View):
    def __init__(self, text: str, enhance: bool = False):
        super().__init__()
        self.add_item(VoiceSelect(text, enhance))


async def tts_command(interaction: discord.Interaction, text: str, enhance: bool = False):
    view = TTSView(text, enhance)
    # The enqueue decorator has already deferred the interaction; edit original message
    await interaction.edit_original_response(content="Select a voice for TTS:", view=view)


async def disconnect_voice(interaction: discord.Interaction):
    """Manually disconnect the bot from voice channel."""
    if interaction.guild.voice_client:
        voice_session_manager.cancel_session(interaction.guild.id)
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("Disconnected from voice channel.", ephemeral=True)
    else:
        await interaction.response.send_message(
            "Not connected to any voice channel.", ephemeral=True
        )
