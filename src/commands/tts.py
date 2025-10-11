import asyncio
import os
from pathlib import Path

import discord
from discord import app_commands
from openai import OpenAI
from typing import List, Dict, Optional

from src import log, responses
from src.voice import connect_to_user_channel, ensure_opus, ffmpeg_available, ffmpeg_executable
from src.voice_session_manager import voice_session_manager
from src.tts.playai import (
    get_cached_voices as playai_get_cached_voices,
    refresh_voices_cache as playai_refresh_voices,
    synthesize_to_file as playai_tts,
)

logger = log.setup_logger(__name__)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]

# Providers
PROVIDER_OPENAI = "openai"
PROVIDER_PLAYAI = "playai"

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
    """Play audio using the voice session manager to handle queuing."""
    audio_source = discord.FFmpegPCMAudio(audio_path, executable=ffmpeg_executable())
    after_callback = lambda e: logger.error(f'Player error: {e}') if e else None

    # Use the session manager to queue audio properly
    await voice_session_manager.queue_audio(voice_client, audio_source, after_callback)

async def handle_tts(interaction: discord.Interaction, text: str, voice: str, enhance: discord.app_commands.Choice[str] = None):
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
            await interaction.edit_original_response(content="You need to be in a voice channel to use this command.", view=None)
            return

        # Pre-flight checks for audio stack
        if not ffmpeg_available():
            await interaction.edit_original_response(
                content="FFmpeg not found. Install FFmpeg and ensure it's on PATH or set FFMPEG_BIN.",
                view=None
            )
            return

        # Note: Opus check bypassed - FFmpeg has built-in Opus support
        # This resolves DLL loading issues on Windows while maintaining functionality

        # Generate speech with OpenAI
        audio_file = await generate_speech(text, voice)

        # Join voice channel using session manager
        voice_client = await voice_session_manager.get_or_connect(interaction)
        if not voice_client:
            await interaction.edit_original_response(
                content="Couldn't connect to voice. Make sure you're in a voice channel and I have permissions.",
                view=None
            )
            return

        # Play audio (queued if something else is playing)
        await play_audio(voice_client, audio_file)

        # Note: Voice client will auto-disconnect after 3 minutes of inactivity

        # Delete the temporary audio file
        os.remove(audio_file)

        await interaction.edit_original_response(content=f"TTS audio played successfully using the {voice} voice.", view=None)
    except Exception as e:
        logger.error(f"Error in TTS command for {username}: {str(e)}")
        try:
            await interaction.edit_original_response(content=f"An error occurred: {str(e)}", view=None)
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

        # Join voice channel using session manager
        voice_client = await voice_session_manager.get_or_connect(interaction)
        if not voice_client:
            await interaction.followup.send(
                "Couldn't connect to voice. Make sure you're in a voice channel and I have permissions.")
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
    def __init__(self, text: str):
        options = [discord.SelectOption(label=voice, value=voice) for voice in VOICES]
        super().__init__(placeholder="Select a voice", options=options)
        self.text = text

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        await handle_tts(interaction, self.text, self.values[0])

class TTSView(discord.ui.View):
    def __init__(self, text: str):
        super().__init__()
        self.add_item(VoiceSelect(text))


# --------------------------- PlayAI Integration ---------------------------

async def handle_playai_tts(interaction: discord.Interaction, text: str, voice_id: str):
    username = str(interaction.user)
    try:
        if not interaction.user.voice:
            await interaction.edit_original_response(content="You need to be in a voice channel to use this command.", view=None)
            return

        if not ffmpeg_available():
            await interaction.edit_original_response(
                content="FFmpeg not found. Install FFmpeg and ensure it's on PATH or set FFMPEG_BIN.",
                view=None
            )
            return

        # Synthesize speech via PlayAI
        tmp_path = Path("temp_audio_playai.mp3")
        audio_file = await playai_tts(text=text, voice_id=voice_id, output_path=tmp_path, format="mp3")
        if not audio_file:
            await interaction.edit_original_response(content="PlayAI TTS failed. Check API key and voice ID.", view=None)
            return

        # Join voice channel using session manager
        voice_client = await voice_session_manager.get_or_connect(interaction)
        if not voice_client:
            await interaction.edit_original_response(
                content="Couldn't connect to voice. Make sure you're in a voice channel and I have permissions.",
                view=None
            )
            return

        await play_audio(voice_client, audio_file)

        # Remove temp file after queueing
        try:
            os.remove(audio_file)
        except OSError:
            pass

        await interaction.edit_original_response(content=f"TTS audio played successfully using PlayAI voice: {voice_id}.", view=None)
    except Exception as e:
        logger.error(f"Error in PlayAI TTS for {username}: {str(e)}")
        try:
            await interaction.edit_original_response(content=f"An error occurred: {str(e)}", view=None)
        except Exception:
            pass
        if interaction.guild.voice_client:
            voice_session_manager.cancel_session(interaction.guild.id)


class PlayAIVoiceButton(discord.ui.Button):
    def __init__(self, voice: Dict[str, str]):
        # Prefer name; fall back to id
        label = voice.get("name") or voice.get("id") or "voice"
        if len(label) > 80:
            label = label[:77] + "..."
        super().__init__(label=label, style=discord.ButtonStyle.success)
        self.voice = voice

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await handle_playai_tts(interaction, self.view.text, self.voice.get("id") or self.voice.get("name"))


class PlayAIVoiceSelectionView(discord.ui.View):
    def __init__(self, text: str, voices: List[Dict[str, str]], query: Optional[str] = None):
        super().__init__(timeout=300.0)
        self.text = text
        self.voices = voices
        self.query = query or ""
        self.page = 0
        self.page_size = 5
        self._rebuild()

    def _rebuild(self):
        self.clear_items()
        start = self.page * self.page_size
        end = min(len(self.voices), start + self.page_size)
        for v in self.voices[start:end]:
            self.add_item(PlayAIVoiceButton(v))

        # Navigation
        if self.page > 0:
            prev_btn = discord.ui.Button(label="Previous", style=discord.ButtonStyle.secondary)
            async def prev_cb(interaction: discord.Interaction):
                await interaction.response.defer(ephemeral=True)
                self.page -= 1
                self._rebuild()
                await interaction.response.edit_message(view=self)
            prev_btn.callback = prev_cb
            self.add_item(prev_btn)

        if end < len(self.voices):
            next_btn = discord.ui.Button(label="Next", style=discord.ButtonStyle.secondary)
            async def next_cb(interaction: discord.Interaction):
                await interaction.response.defer(ephemeral=True)
                self.page += 1
                self._rebuild()
                await interaction.response.edit_message(view=self)
            next_btn.callback = next_cb
            self.add_item(next_btn)

        # Refresh button to update the cached list on demand
        refresh_btn = discord.ui.Button(label="Refresh Voices", style=discord.ButtonStyle.secondary)
        async def refresh_cb(interaction: discord.Interaction):
            await interaction.response.defer(thinking=True)
            new_list = await playai_refresh_voices()
            self.voices = new_list
            self.page = 0
            self._rebuild()
            await interaction.edit_original_response(view=self)
        refresh_btn.callback = refresh_cb
        self.add_item(refresh_btn)

        cancel_btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.danger)
        async def cancel_cb(interaction: discord.Interaction):
            try:
                await interaction.response.edit_message(content="PlayAI selection cancelled.", view=None)
            except (discord.errors.NotFound, discord.errors.InteractionResponded):
                pass
            self.stop()
        cancel_btn.callback = cancel_cb
        self.add_item(cancel_btn)


# Removed modal-based search; we now use a cached, paginated list with optional refresh


class ProviderSelect(discord.ui.Select):
    def __init__(self, text: str):
        options = [
            discord.SelectOption(label="OpenAI TTS", value=PROVIDER_OPENAI, description="Use OpenAI tts-1"),
            discord.SelectOption(label="PlayAI TTS", value=PROVIDER_PLAYAI, description="Use Play.ht voices"),
        ]
        super().__init__(placeholder="Select TTS Provider", options=options)
        self.text = text

    async def callback(self, interaction: discord.Interaction):
        # Route to provider-specific flows. Do not defer first if we're sending a modal.
        if self.values[0] == PROVIDER_OPENAI:
            view = TTSView(self.text)
            try:
                await interaction.response.edit_message(content="Select a voice for TTS:", view=view)
            except (discord.errors.NotFound, discord.errors.InteractionResponded):
                try:
                    await interaction.edit_original_response(content="Select a voice for TTS:", view=view)
                except Exception:
                    pass
        else:
            # Show cached PlayAI voices with pagination; avoid extra API calls
            voices = playai_get_cached_voices()
            view = PlayAIVoiceSelectionView(self.text, voices)
            try:
                await interaction.response.edit_message(content="Select a PlayAI voice:", view=view)
            except (discord.errors.NotFound, discord.errors.InteractionResponded):
                try:
                    await interaction.edit_original_response(content="Select a PlayAI voice:", view=view)
                except Exception:
                    pass


class ProviderView(discord.ui.View):
    def __init__(self, text: str):
        super().__init__()
        self.add_item(ProviderSelect(text))

async def tts_command(interaction: discord.Interaction, text: str):
    view = ProviderView(text)
    # The enqueue decorator has already deferred the interaction; edit original message
    await interaction.edit_original_response(content="Select a TTS provider:", view=view)

async def disconnect_voice(interaction: discord.Interaction):
    """Manually disconnect the bot from voice channel."""
    if interaction.guild.voice_client:
        voice_session_manager.cancel_session(interaction.guild.id)
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("Disconnected from voice channel.", ephemeral=True)
    else:
        await interaction.response.send_message("Not connected to any voice channel.", ephemeral=True)

def setup(bot):
    @bot.tree.command(name="tts", description="Generate and play text-to-speech audio")
    @app_commands.describe(text="The text to convert to speech")
    async def tts(interaction: discord.Interaction, text: str):
        await tts_command(interaction, text)
