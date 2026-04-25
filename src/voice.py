import logging
import os
import shutil
from typing import Optional

import discord

logger = logging.getLogger(__name__)

try:
    # Optional extension that enables voice receive; when present we use
    # its VoiceRecvClient so the same connection can both send and receive.
    from discord.ext import voice_recv  # type: ignore[import]
except ImportError:  # pragma: no cover - optional dependency
    voice_recv = None  # type: ignore[assignment]


def ffmpeg_executable() -> str:
    """Return the ffmpeg binary to use (env override supported)."""
    return os.getenv("FFMPEG_BIN", "ffmpeg")


def ffmpeg_available() -> bool:
    """Return True if ffmpeg executable is discoverable."""
    return shutil.which(ffmpeg_executable()) is not None


def ensure_opus() -> bool:
    """Try to ensure libopus is loaded; return True if available."""
    try:
        if discord.opus.is_loaded():
            return True

        # Clear problematic OPUS_DLL_PATH if it exists
        import os

        if "OPUS_DLL_PATH" in os.environ:
            problematic_path = os.environ["OPUS_DLL_PATH"]
            if not os.path.exists(problematic_path):
                logger.info(f"Clearing invalid OPUS_DLL_PATH: {problematic_path}")
                del os.environ["OPUS_DLL_PATH"]

        # Try discord.py's default loader first (most reliable)
        try:
            discord.opus._load_default()
            if discord.opus.is_loaded():
                logger.info("Successfully loaded Opus using _load_default()")
                return True
        except Exception as e:
            logger.debug(f"_load_default() failed: {e}")

        # Try explicit path from env var (after cleanup)
        path = os.getenv("OPUS_DLL_PATH")
        if path:
            try:
                discord.opus.load_opus(path)
            except OSError as e:
                logger.warning(f"Failed to load opus from OPUS_DLL_PATH={path}: {e}")

        if discord.opus.is_loaded():
            return True

        # Try common Opus library names
        for name in ("opus", "libopus-0", "libopus", "libopus-0.dll", "libopus-0.x64.dll"):
            try:
                discord.opus.load_opus(name)
                if discord.opus.is_loaded():
                    logger.info(f"Successfully loaded Opus as '{name}'")
                    break
            except OSError:
                continue

        return discord.opus.is_loaded()
    except Exception as e:
        logger.warning(f"Failed to load opus: {e}")
        return False


async def connect_to_user_channel(
    interaction: discord.Interaction,
    reconnect: bool = False,
    timeout: float = 15.0,
    self_deaf: bool = True,
) -> discord.VoiceClient:
    """Ensure the bot is connected to the user's current voice channel.

    - Reuses the existing guild voice client when possible.
    - Moves the bot if it's in a different channel in the same guild.
    - Connects fresh if not connected.
    """
    if not interaction.user or not getattr(interaction.user, "voice", None):
        raise discord.ClientException("You must be in a voice channel.")

    channel: discord.VoiceChannel = interaction.user.voice.channel
    # Permission precheck
    try:
        me = interaction.guild.me if interaction.guild else None
    except Exception:
        me = None
    if me is not None:
        perms = channel.permissions_for(me)
        if not perms.connect:
            raise discord.Forbidden(channel, "Missing permission: CONNECT")
        if not perms.speak:
            raise discord.Forbidden(channel, "Missing permission: SPEAK")
    guild_client: Optional[discord.VoiceClient] = (
        interaction.guild.voice_client if interaction.guild else None
    )

    # Already connected to the same channel
    if guild_client and guild_client.is_connected():
        if guild_client.channel and guild_client.channel.id == channel.id:
            return guild_client
        # Move within the guild
        try:
            await guild_client.move_to(channel)
            return guild_client
        except Exception as e:
            logger.error(f"Failed to move to voice channel: {e}")
            # Fallback: disconnect and reconnect
            try:
                await guild_client.disconnect(force=True)
            except Exception:
                pass

    # Fresh connect
    try:
        connect_kwargs = {
            "timeout": timeout,
            "reconnect": reconnect,
            "self_deaf": self_deaf,
        }
        # If voice receive extension is available, use its VoiceRecvClient
        # so the same connection can be used for /listen.
        if voice_recv is not None:  # type: ignore[truthy-function]
            connect_kwargs["cls"] = voice_recv.VoiceRecvClient  # type: ignore[attr-defined]
            # When using voice receive, never self-deafen; otherwise Discord
            # will not send us other users' audio, and the receive pipeline
            # (including discord-ext-voice-recv's Opus decoder) can behave badly.
            connect_kwargs["self_deaf"] = False

        voice_client = await channel.connect(**connect_kwargs)
        return voice_client
    except discord.ClientException as e:
        # If we're already connected in this guild, reuse that client
        # instead of treating it as a hard error.
        if (
            "Already connected to a voice channel" in str(e)
            and interaction.guild
            and interaction.guild.voice_client
        ):
            logger.info(
                "Reusing existing voice client for guild %s after 'Already connected' error",
                interaction.guild.id,
            )
            return interaction.guild.voice_client
        logger.error(
            f"Voice connect failed (guild={interaction.guild_id}, channel={channel.id}): {e}"
        )
        raise
    except Exception as e:
        logger.error(
            f"Voice connect failed (guild={interaction.guild_id}, channel={channel.id}): {e}"
        )
        raise
