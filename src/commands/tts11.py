"""ElevenLabs TTS slash command (/tts11) with a floating model + voice picker."""

from typing import List, Optional

import discord

from src import log, responses
from src.audio_bus import get_guild_bus
from src.tts import eleven
from src.tts.eleven import ElevenLabsConfigError, VoiceEntry
from src.voice import ffmpeg_available, ffmpeg_executable
from src.voice_session_manager import voice_session_manager

logger = log.setup_logger(__name__)


def _model_options(selected_model_id: str) -> List[discord.SelectOption]:
    return [
        discord.SelectOption(
            label=label,
            value=value,
            default=(value == selected_model_id),
        )
        for value, label in eleven.MODELS
    ]


def _voice_options(
    voices: List[VoiceEntry], selected_voice_id: Optional[str]
) -> List[discord.SelectOption]:
    if not voices:
        return [
            discord.SelectOption(
                label="No voices found",
                value="__none__",
                description="Click 'Search voices' to find one.",
                default=True,
            )
        ]

    options: List[discord.SelectOption] = []
    for v in voices[:25]:
        desc = (v.category or "").strip()[:100] or None
        options.append(
            discord.SelectOption(
                label=v.name[:100],
                value=v.voice_id,
                description=desc,
                default=(v.voice_id == selected_voice_id),
            )
        )
    return options


class ModelSelect(discord.ui.Select):
    def __init__(self, parent: "ElevenLabsView") -> None:
        super().__init__(
            placeholder="Model",
            options=_model_options(parent.selected_model_id),
            row=0,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        self.parent_view.selected_model_id = self.values[0]
        for opt in self.options:
            opt.default = opt.value == self.values[0]
        await interaction.response.defer()


class VoiceSelect(discord.ui.Select):
    def __init__(self, parent: "ElevenLabsView") -> None:
        super().__init__(
            placeholder="Voice",
            options=_voice_options(parent.voices, parent.selected_voice_id),
            row=1,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == "__none__":
            await interaction.response.defer()
            return
        self.parent_view.selected_voice_id = self.values[0]
        for opt in self.options:
            opt.default = opt.value == self.values[0]
        await interaction.response.defer()


class VoiceSearchModal(discord.ui.Modal, title="Search ElevenLabs voices"):
    query = discord.ui.TextInput(
        label="Search query",
        placeholder="e.g. rachel, deep, narrator",
        required=True,
        max_length=80,
    )

    def __init__(self, parent: "ElevenLabsView") -> None:
        super().__init__()
        self.parent_view = parent

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            result = await eleven.search_voices(query=str(self.query.value), page_size=25)
        except ElevenLabsConfigError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        except Exception as e:
            logger.error(f"ElevenLabs voice search failed: {e}")
            await interaction.response.send_message(
                f"Voice search failed: {e}", ephemeral=True
            )
            return

        self.parent_view.voices = result.voices
        known_ids = {v.voice_id for v in result.voices}
        if result.voices and self.parent_view.selected_voice_id not in known_ids:
            self.parent_view.selected_voice_id = result.voices[0].voice_id

        self.parent_view.rebuild()
        await interaction.response.edit_message(view=self.parent_view)


class SearchVoicesButton(discord.ui.Button):
    def __init__(self, parent: "ElevenLabsView") -> None:
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label="Search voices",
            emoji="🔍",
            row=2,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(VoiceSearchModal(parent=self.parent_view))


class SpeakButton(discord.ui.Button):
    def __init__(self, parent: "ElevenLabsView") -> None:
        super().__init__(
            style=discord.ButtonStyle.primary,
            label="Speak",
            emoji="🔊",
            row=2,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.parent_view
        if not view.selected_voice_id or view.selected_voice_id == "__none__":
            await interaction.response.send_message(
                "Pick a voice first (or use 'Search voices').", ephemeral=True
            )
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        await handle_tts11(
            interaction,
            text=view.text,
            voice_id=view.selected_voice_id,
            model_id=view.selected_model_id,
            enhance=view.enhance,
        )


class ElevenLabsView(discord.ui.View):
    def __init__(self, text: str, enhance: bool, voices: List[VoiceEntry]) -> None:
        super().__init__(timeout=300)
        self.text = text
        self.enhance = enhance
        self.voices = voices
        self.selected_model_id: str = eleven.default_model_id()
        self.selected_voice_id: Optional[str] = (
            voices[0].voice_id if voices else eleven.default_voice_id()
        )
        self.rebuild()

    def rebuild(self) -> None:
        self.clear_items()
        self.add_item(ModelSelect(self))
        self.add_item(VoiceSelect(self))
        self.add_item(SearchVoicesButton(self))
        self.add_item(SpeakButton(self))


async def tts11_command(
    interaction: discord.Interaction, text: str, enhance: bool = False
) -> None:
    await interaction.response.defer(thinking=True)

    voices: List[VoiceEntry] = []
    error_msg: Optional[str] = None

    try:
        result = await eleven.search_voices(query=None, page_size=25)
        voices = result.voices
    except ElevenLabsConfigError as e:
        error_msg = str(e)
    except Exception as e:
        logger.error(f"Initial ElevenLabs voice search failed: {e}")
        error_msg = f"Failed to load ElevenLabs voices: {e}"

    if error_msg:
        try:
            await interaction.edit_original_response(content=error_msg)
        except Exception:
            await interaction.followup.send(error_msg, ephemeral=True)
        return

    view = ElevenLabsView(text=text, enhance=enhance, voices=voices)
    await interaction.edit_original_response(
        content=f"{interaction.user.display_name} used /tts11",
        view=None,
    )
    await interaction.followup.send(
        content=f"Configure voice & model for: `{text[:120]}`",
        view=view,
        ephemeral=True,
    )


async def handle_tts11(
    interaction: discord.Interaction,
    text: str,
    voice_id: str,
    model_id: str,
    enhance: bool = False,
) -> None:
    username = str(interaction.user)

    try:
        if enhance:
            try:
                logger.info(f"Enhancing TTS prompt: {text}")
                text = await responses.enhance_prompt(text, context="tts")
                logger.info(f"Enhanced TTS prompt: {text}")
            except Exception as e:
                logger.error(f"Failed to enhance prompt, using original: {e}")

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

        try:
            audio_file = await eleven.synthesize_to_file(
                text=text,
                voice_id=voice_id,
                model_id=model_id,
                output_format=eleven.default_output_format(),
            )
        except ElevenLabsConfigError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        except Exception as e:
            logger.error(f"Failed to generate ElevenLabs TTS: {e}")
            await interaction.followup.send(
                f"Failed to generate ElevenLabs TTS: {e}", ephemeral=True
            )
            return

        voice_client = await voice_session_manager.get_or_connect(interaction)
        if not voice_client:
            await interaction.followup.send(
                "Couldn't connect to voice. Make sure you're in a voice channel and I have permissions.",
                ephemeral=True,
            )
            try:
                audio_file.unlink(missing_ok=True)
            except Exception:
                pass
            return

        bus = get_guild_bus(interaction.guild.id)
        bus.attach_voice_client(voice_client)

        audio_source = discord.FFmpegPCMAudio(str(audio_file), executable=ffmpeg_executable())

        try:
            from src.commands.music import music_player

            music_player.duck_for_tts()
        except Exception:
            pass

        def on_done(error: Optional[Exception] = None) -> None:
            if error:
                logger.error(f"Error during ElevenLabs TTS playback: {error}")
            try:
                audio_file.unlink(missing_ok=True)
            except Exception as e:
                logger.error(f"Error deleting ElevenLabs TTS file {audio_file}: {e}")
            try:
                from src.commands.music import music_player

                music_player.unduck_for_tts()
            except Exception:
                pass

        bus.add_track(audio_source, volume=1.0, on_done=on_done)

        try:
            await interaction.followup.send(
                content=f"Speaking · model `{model_id}` · voice `{voice_id}`",
                ephemeral=True,
            )
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Error in /tts11 for {username}: {e}")
        try:
            await interaction.followup.send(content=f"An error occurred: {e}", ephemeral=True)
        except Exception:
            pass
        if interaction.guild and interaction.guild.voice_client:
            voice_session_manager.cancel_session(interaction.guild.id)
