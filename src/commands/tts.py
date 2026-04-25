import asyncio
import os
import time
from pathlib import Path

import discord
from openai import AsyncOpenAI

from src import log, responses
from src.audio_bus import get_guild_bus
from src.voice import ffmpeg_available, ffmpeg_executable
from src.voice_session_manager import voice_session_manager

logger = log.setup_logger(__name__)

# Use AsyncOpenAI to prevent blocking the event loop
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]


async def generate_speech(text: str, voice: str) -> Path:
    # Use unique filename to prevent overwriting queued TTS
    timestamp = int(time.time() * 1000)
    speech_file_path = Path(f"temp_tts_{timestamp}.mp3")

    response = await client.audio.speech.create(model="tts-1", voice=voice, input=text)

    # Streaming to file asynchronously
    response.stream_to_file(speech_file_path)

    logger.info(f"Generated TTS file: {speech_file_path}, size: {speech_file_path.stat().st_size}")
    return speech_file_path


async def handle_tts(
    interaction: discord.Interaction,
    text: str,
    voice: str,
    enhance: discord.app_commands.Choice[str] = None,
) -> None:
    username = str(interaction.user)

    try:
        # Optional prompt enhancement
        if enhance and enhance.value == "yes":
            logger.info(f"Enhancing TTS prompt: {text}")
            enhanced_text = await responses.enhance_prompt(text, context="tts")
            text = enhanced_text
            logger.info(f"Enhanced TTS prompt: {text}")

        if not interaction.user.voice:
            await interaction.followup.send(
                "You need to be in a voice channel to use this command.",
                ephemeral=True,
            )
            return

        if not ffmpeg_available():
            await interaction.followup.send(
                "FFmpeg not found. Install FFmpeg and ensure it's on PATH or set FFMPEG_BIN.",
                ephemeral=True,
            )
            return

        # Generate speech audio
        try:
            audio_file = await generate_speech(text, voice)
            # Small sleep not strictly necessary but harmless
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Failed to generate TTS: {e}")
            await interaction.followup.send(f"Failed to generate TTS: {str(e)}", ephemeral=True)
            return

        # Connect or reuse voice client
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
            pass

        def on_done(error: Exception | None = None) -> None:
            if error:
                logger.error(f"Error during TTS playback: {error}")
            try:
                audio_file.unlink(missing_ok=True)
            except Exception as e:
                logger.error(f"Error deleting TTS file {audio_file}: {e}")

            try:
                from src.commands.music import music_player

                music_player.unduck_for_tts()
            except Exception:
                pass

        bus.add_track(audio_source, volume=1.0, on_done=on_done)

        # Ephemeral confirmation for the user (who + voice)
        try:
            await interaction.followup.send(
                content=(
                    f"TTS audio queued for {interaction.user.display_name} using the {voice} voice."
                ),
                ephemeral=True,
            )
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Error in TTS command for {username}: {str(e)}")
        try:
            await interaction.followup.send(content=f"An error occurred: {str(e)}", ephemeral=True)
        except Exception:
            pass
        if interaction.guild and interaction.guild.voice_client:
            voice_session_manager.cancel_session(interaction.guild.id)


async def handle_tts_for_chat(interaction: discord.Interaction, text: str) -> None:
    if not interaction.user.voice:
        await interaction.followup.send("You need to be in a voice channel to use TTS.")
        return

    try:
        audio_file = await generate_speech(text, "alloy")

        voice_client = await voice_session_manager.get_or_connect(interaction)
        if not voice_client:
            await interaction.followup.send(
                "Couldn't connect to voice. Make sure you're in a voice channel and I have permissions."
            )
            return

        bus = get_guild_bus(interaction.guild.id)
        bus.attach_voice_client(voice_client)

        audio_source = discord.FFmpegPCMAudio(str(audio_file), executable=ffmpeg_executable())

        try:
            from src.commands.music import music_player

            music_player.duck_for_tts()
        except Exception:
            pass

        def on_done(error: Exception | None = None) -> None:
            if error:
                logger.error(f"Error during chat TTS playback: {error}")
            try:
                audio_file.unlink(missing_ok=True)
            except Exception as e:
                logger.error(f"Error deleting chat TTS file {audio_file}: {e}")
            try:
                from src.commands.music import music_player

                music_player.unduck_for_tts()
            except Exception:
                pass

        bus.add_track(audio_source, volume=1.0, on_done=on_done)

        await interaction.followup.send(f"TTS audio played for {interaction.user.display_name}.")
    except Exception as e:
        logger.error(f"Error in TTS for chat: {str(e)}")
        await interaction.followup.send(f"An error occurred while playing TTS: {str(e)}")
        if interaction.guild and interaction.guild.voice_client:
            voice_session_manager.cancel_session(interaction.guild.id)


class VoiceSelect(discord.ui.Select):
    def __init__(self, text: str, enhance: bool = False) -> None:
        options = [discord.SelectOption(label=voice, value=voice) for voice in VOICES]
        super().__init__(placeholder="Select a voice", options=options)
        self.text = text
        self.enhance = enhance

    async def callback(self, interaction: discord.Interaction) -> None:
        # Keep the menu around so the user can repeat TTS.
        await interaction.response.defer(thinking=True)

        enhance_choice = None
        if self.enhance:
            enhance_choice = discord.app_commands.Choice(name="yes", value="yes")

        await handle_tts(interaction, self.text, self.values[0], enhance_choice)


class TTSView(discord.ui.View):
    def __init__(self, text: str, enhance: bool = False) -> None:
        super().__init__()
        self.add_item(VoiceSelect(text, enhance))


async def tts_command(interaction: discord.Interaction, text: str, enhance: bool = False) -> None:
    # Must defer here because we are doing away with @enqueue
    await interaction.response.defer(thinking=True)

    view = TTSView(text, enhance)
    # Public message (blue command text) stays simple.
    await interaction.edit_original_response(
        content=f"{interaction.user.display_name} used /tts",
        view=None,
    )
    # Voice selection is ephemeral and only visible to the invoker.
    await interaction.followup.send(content="Select a voice for TTS:", view=view, ephemeral=True)


async def disconnect_voice(interaction: discord.Interaction) -> None:
    """Manually disconnect the bot from voice channel."""
    if interaction.guild and interaction.guild.voice_client:
        voice_session_manager.cancel_session(interaction.guild.id)
        # Also clean up music player state to prevent desync
        try:
            from src.commands.music import music_player

            music_player.is_playing = False
            music_player.cancel_idle_timeout()
            music_player.queue.clear()
            music_player.current_song = None
            music_player.current_track = None
            music_player.music_duck_count = 0
            music_player.voice_client = None
        except Exception:
            pass
        # Stop all tracks on the audio bus
        try:
            bus = get_guild_bus(interaction.guild.id)
            bus.stop_all()
        except Exception:
            pass
        voice_session_manager.stay_guilds.discard(interaction.guild.id)
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("Disconnected from voice channel.", ephemeral=True)
    else:
        await interaction.response.send_message(
            "Not connected to any voice channel.", ephemeral=True
        )
