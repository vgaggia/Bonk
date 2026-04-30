"""/voice — pick the TTS backend & voice used by /listen replies.

Mirrors /tts11's picker UI so users can choose either:
  - OpenAI (built-in) — one of six preset voices via OpenAI's tts-1.
  - ElevenLabs — model + library voice (with search), same plumbing as /tts11.

Selections are auto-saved to the per-guild ListenVoiceConfig held in
voice_listen.py and consumed by ListenSession._handle_utterance.
"""

from typing import List, Optional, Tuple

import discord

from src import log, voice_listen
from src.tts import eleven
from src.tts.eleven import ElevenLabsConfigError, VoiceEntry
from src.voice_listen import (
    LISTEN_BACKEND_ELEVENLABS,
    LISTEN_BACKEND_OPENAI,
    OPENAI_TTS_VOICES,
    ListenVoiceConfig,
)

logger = log.setup_logger(__name__)


async def _try_load_eleven_voices() -> Tuple[List[VoiceEntry], Optional[str]]:
    """Best-effort fetch of ElevenLabs voices. Never raises."""
    try:
        result = await eleven.search_voices(query=None, page_size=25)
        return result.voices, None
    except ElevenLabsConfigError as e:
        return [], str(e)
    except Exception as e:
        logger.error(f"ElevenLabs voice search failed: {e}")
        return [], f"Failed to load ElevenLabs voices: {e}"


class BackendSelect(discord.ui.Select):
    def __init__(self, parent: "ListenVoiceView") -> None:
        options = [
            discord.SelectOption(
                label="OpenAI (built-in)",
                value=LISTEN_BACKEND_OPENAI,
                description="Six preset voices via tts-1.",
                default=parent.config.backend == LISTEN_BACKEND_OPENAI,
            ),
            discord.SelectOption(
                label="ElevenLabs",
                value=LISTEN_BACKEND_ELEVENLABS,
                description="Higher quality, custom voices (needs ELEVENLABS_API_KEY).",
                default=parent.config.backend == LISTEN_BACKEND_ELEVENLABS,
            ),
        ]
        super().__init__(placeholder="TTS backend", options=options, row=0)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        self.parent_view.config.backend = self.values[0]
        self.parent_view.rebuild()
        await interaction.response.edit_message(
            content=self.parent_view.content_text(), view=self.parent_view
        )


class OpenAIVoiceSelect(discord.ui.Select):
    def __init__(self, parent: "ListenVoiceView") -> None:
        options = [
            discord.SelectOption(
                label=v, value=v, default=v == parent.config.openai_voice
            )
            for v in OPENAI_TTS_VOICES
        ]
        super().__init__(placeholder="OpenAI voice", options=options, row=1)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        self.parent_view.config.openai_voice = self.values[0]
        for opt in self.options:
            opt.default = opt.value == self.values[0]
        await interaction.response.edit_message(
            content=self.parent_view.content_text(), view=self.parent_view
        )


class ElevenModelSelect(discord.ui.Select):
    def __init__(self, parent: "ListenVoiceView") -> None:
        options = [
            discord.SelectOption(
                label=label,
                value=value,
                default=value == parent.config.eleven_model_id,
            )
            for value, label in eleven.MODELS
        ]
        super().__init__(placeholder="ElevenLabs model", options=options, row=1)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        self.parent_view.config.eleven_model_id = self.values[0]
        for opt in self.options:
            opt.default = opt.value == self.values[0]
        await interaction.response.edit_message(
            content=self.parent_view.content_text(), view=self.parent_view
        )


class ElevenVoiceSelect(discord.ui.Select):
    def __init__(self, parent: "ListenVoiceView") -> None:
        if not parent.eleven_voices:
            options = [
                discord.SelectOption(
                    label="No voices loaded",
                    value="__none__",
                    description="Click 'Search voices' to find one.",
                    default=True,
                )
            ]
        else:
            options = []
            for v in parent.eleven_voices[:25]:
                desc = (v.category or "").strip()[:100] or None
                options.append(
                    discord.SelectOption(
                        label=v.name[:100],
                        value=v.voice_id,
                        description=desc,
                        default=v.voice_id == parent.config.eleven_voice_id,
                    )
                )
        super().__init__(placeholder="ElevenLabs voice", options=options, row=2)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == "__none__":
            await interaction.response.defer()
            return
        self.parent_view.config.eleven_voice_id = self.values[0]
        for opt in self.options:
            opt.default = opt.value == self.values[0]
        await interaction.response.edit_message(
            content=self.parent_view.content_text(), view=self.parent_view
        )


class ElevenSearchModal(discord.ui.Modal, title="Search ElevenLabs voices"):
    query = discord.ui.TextInput(
        label="Search query",
        placeholder="e.g. rachel, deep, narrator",
        required=True,
        max_length=80,
    )

    def __init__(self, parent: "ListenVoiceView") -> None:
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

        view = self.parent_view
        view.eleven_voices = result.voices
        view.eleven_error = None
        known_ids = {v.voice_id for v in result.voices}
        if result.voices and view.config.eleven_voice_id not in known_ids:
            view.config.eleven_voice_id = result.voices[0].voice_id

        view.rebuild()
        await interaction.response.edit_message(content=view.content_text(), view=view)


class ElevenSearchButton(discord.ui.Button):
    def __init__(self, parent: "ListenVoiceView") -> None:
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label="Search voices",
            emoji="🔍",
            row=3,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(ElevenSearchModal(parent=self.parent_view))


class ListenVoiceView(discord.ui.View):
    def __init__(
        self,
        config: ListenVoiceConfig,
        eleven_voices: List[VoiceEntry],
        eleven_error: Optional[str],
    ) -> None:
        super().__init__(timeout=300)
        self.config = config
        self.eleven_voices = eleven_voices
        self.eleven_error = eleven_error
        self.rebuild()

    def content_text(self) -> str:
        cfg = self.config
        if cfg.backend == LISTEN_BACKEND_OPENAI:
            line = f"Backend: **OpenAI** · voice `{cfg.openai_voice}`"
        else:
            line = (
                f"Backend: **ElevenLabs** · voice `{cfg.eleven_voice_id or '—'}` "
                f"· model `{cfg.eleven_model_id}`"
            )
        body = f"Configure /listen reply voice (auto-saved):\n{line}"
        if self.eleven_error and cfg.backend == LISTEN_BACKEND_ELEVENLABS:
            body += f"\n⚠ {self.eleven_error}"
        return body

    def rebuild(self) -> None:
        self.clear_items()
        self.add_item(BackendSelect(self))
        if self.config.backend == LISTEN_BACKEND_OPENAI:
            self.add_item(OpenAIVoiceSelect(self))
        else:
            self.add_item(ElevenModelSelect(self))
            self.add_item(ElevenVoiceSelect(self))
            self.add_item(ElevenSearchButton(self))


async def voice_command(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        await interaction.response.send_message(
            "This command only works in a server.", ephemeral=True
        )
        return

    config = voice_listen.get_listen_voice_config(interaction.guild.id)
    await interaction.response.defer(ephemeral=True, thinking=True)

    eleven_voices, eleven_error = await _try_load_eleven_voices()

    view = ListenVoiceView(config, eleven_voices, eleven_error)
    await interaction.followup.send(
        content=view.content_text(), view=view, ephemeral=True
    )
