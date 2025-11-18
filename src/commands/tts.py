import asyncio
import os
from pathlib import Path

import discord
from openai import OpenAI

from src import log, responses
from src.voice import ffmpeg_available, ffmpeg_executable
from src.voice_session_manager import voice_session_manager
from src.audio_bus import get_guild_bus

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
    """Compatibility shim – not used with the new mixer-based playback."""
    logger.warning("play_audio() is deprecated and no longer used with the mixer.")


async def handle_tts(
    interaction: discord.Interaction,
    text: str,
    voice: str,
    enhance: discord.app_commands.Choice[str] = None,
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
            await interaction.followup.send(
                "You need to be in a voice channel to use this command.", ephemeral=True
            )
            return

        # Pre-flight checks for audio stack
        if not ffmpeg_available():
            await interaction.followup.send(
                "FFmpeg not found. Install FFmpeg and ensure it's on PATH or set FFMPEG_BIN.",
                ephemeral=True,
            )
            return

        # Note: Opus check bypassed - FFmpeg has built-in Opus support
        # This resolves DLL loading issues on Windows while maintaining functionality

        # Generate speech with OpenAI (this takes time)
        try:
            audio_file = await generate_speech(text, voice)
            # Ensure file is fully written and closed
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Failed to generate TTS: {e}")
            await interaction.followup.send(
                f"Failed to generate TTS: {str(e)}", ephemeral=True
            )
            return

        # Ensure we are connected to the user's voice channel.
        voice_client = await voice_session_manager.get_or_connect(interaction)
        if not voice_client:
            await interaction.followup.send(
                "Couldn't connect to voice. Make sure you're in a voice channel and I have permissions.",
                ephemeral=True,
            )
            return

        # Route playback through the per-guild audio bus so TTS can mix
        # with music and other TTS clips.
        bus = get_guild_bus(interaction.guild.id)
        bus.attach_voice_client(voice_client)

        audio_source = discord.FFmpegPCMAudio(str(audio_file), executable=ffmpeg_executable())

        # Duck music while this TTS clip is playing
        try:
            from src.commands.music import music_player

            music_player.duck_for_tts()
        except Exception:
            # If music subsystem is unavailable, just skip ducking
            pass

        def on_done(error: Exception | None = None):
            if error:
                logger.error(f"Error during TTS playback: {error}")
            try:
                os.remove(audio_file)
            except OSError as e:
                logger.error(f"Error deleting TTS file {audio_file}: {e}")
            # Restore music volume when this TTS clip finishes
            try:
                from src.commands.music import music_player

                music_player.unduck_for_tts()
            except Exception:
                pass

        bus.add_track(audio_source, volume=1.0, on_done=on_done)

        # Ephemeral confirmation for the user
        try:
            await interaction.followup.send(
                content=f"TTS audio queued using the {voice} voice.",
                ephemeral=True,
            )
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Error in TTS command for {username}: {str(e)}")
        try:
            await interaction.followup.send(
                content=f"An error occurred: {str(e)}", ephemeral=True
            )
        except Exception:
            pass
        if interaction.guild.voice_client:
            voice_session_manager.cancel_session(interaction.guild.id)


async def handle_tts_for_chat(interaction: discord.Interaction, text: str):
    if not interaction.user.voice:
        await interaction.followup.send("You need to be in a voice channel to use TTS.")
        return

    try:
        # Generate speech using a default OpenAI voice
        audio_file = await generate_speech(text, "alloy")

        # Join voice channel using session manager and play via the mixer
        voice_client = await voice_session_manager.get_or_connect(interaction)
        if not voice_client:
            await interaction.followup.send(
                "Couldn't connect to voice. Make sure you're in a voice channel and I have permissions."
            )
            return

        bus = get_guild_bus(interaction.guild.id)
        bus.attach_voice_client(voice_client)

        audio_source = discord.FFmpegPCMAudio(str(audio_file), executable=ffmpeg_executable())

        # Duck music while this chat TTS clip is playing
        try:
            from src.commands.music import music_player

            music_player.duck_for_tts()
        except Exception:
            pass

        def on_done(error: Exception | None = None):
            if error:
                logger.error(f"Error during chat TTS playback: {error}")
            try:
                os.remove(audio_file)
            except OSError as e:
                logger.error(f"Error deleting chat TTS file {audio_file}: {e}")
            # Restore music volume when this TTS clip finishes
            try:
                from src.commands.music import music_player

                music_player.unduck_for_tts()
            except Exception:
                pass

        bus.add_track(audio_source, volume=1.0, on_done=on_done)

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
        # Update the ephemeral selector message so only the invoking
        # user sees the generation status.
        await interaction.response.edit_message(
            content=f"Generating TTS using {self.values[0]} voice...", view=None
        )

        # Create enhance choice object if needed
        enhance_choice = None
        if self.enhance:
            enhance_choice = discord.app_commands.Choice(name="yes", value="yes")

        await handle_tts(interaction, self.text, self.values[0], enhance_choice)


class TTSView(discord.ui.View):
    def __init__(self, text: str, enhance: bool = False):
        super().__init__()
        self.add_item(VoiceSelect(text, enhance))


async def tts_command(interaction: discord.Interaction, text: str, enhance: bool = False):
    view = TTSView(text, enhance)
    # The enqueue decorator has already deferred the interaction; keep the
    # public message minimal and show the selector only to the invoking user.
    await interaction.edit_original_response(
        content=f"{interaction.user.display_name} used /tts",
        view=None,
    )
    await interaction.followup.send(
        content="Select a voice for TTS:", view=view, ephemeral=True
    )


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
