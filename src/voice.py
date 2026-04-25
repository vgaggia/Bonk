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


def _voice_client_healthy(vc: Optional[discord.VoiceClient]) -> bool:
    """Return True if vc is actually usable, not just flagged as connected.

    discord.py's `is_connected()` only checks the `_connected` event flag, which
    is not cleared on every voice-gateway close code (notably 4017). Augment it
    with a check on the underlying WebSocket so we don't hand a zombie client
    back to callers like `VoiceRecvClient.listen()` that pass the precheck and
    then fail downstream.
    """
    if vc is None or not vc.is_connected():
        return False
    ws = getattr(vc, "ws", None)
    if ws is None or getattr(ws, "closed", False):
        return False
    sock = getattr(ws, "socket", None)
    if sock is not None and getattr(sock, "closed", False):
        return False
    return True


async def _force_cleanup_voice_client(vc: Optional[discord.VoiceClient]) -> None:
    """Best-effort full teardown of a (possibly half-dead) VoiceClient.

    Sends the voice-state update so Discord's server stops listing the bot in
    the channel, runs the discord.py cleanup that removes the client from the
    guild's cache, and clears the stale `_connected` flag as a belt-and-braces
    so any lingering reference can't pass `is_connected()` afterwards.
    """
    if vc is None:
        return
    try:
        await vc.disconnect(force=True)
    except Exception:
        logger.debug("force disconnect raised; continuing", exc_info=True)
    try:
        cleanup = getattr(vc, "cleanup", None)
        if callable(cleanup):
            cleanup()
    except Exception:
        logger.debug("cleanup() raised; continuing", exc_info=True)
    try:
        connected = getattr(vc, "_connected", None)
        if connected is not None and hasattr(connected, "clear"):
            connected.clear()
    except Exception:
        pass


async def connect_to_user_channel(
    interaction: discord.Interaction,
    reconnect: bool = False,
    timeout: float = 15.0,
    self_deaf: bool = True,
) -> discord.VoiceClient:
    """Ensure the bot is connected to the user's current voice channel.

    - Reuses the existing guild voice client when it passes the health check.
    - Moves the bot if it's in a different channel in the same guild.
    - Force-cleans any zombie client and connects fresh otherwise.
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

    if guild_client is not None:
        if _voice_client_healthy(guild_client):
            if guild_client.channel and guild_client.channel.id == channel.id:
                return guild_client
            try:
                await guild_client.move_to(channel)
                return guild_client
            except Exception as e:
                logger.error("Failed to move to voice channel: %s", e)
        else:
            logger.warning(
                "Guild %s voice_client failed health check (zombie after unclean close); "
                "forcing cleanup before reconnect",
                interaction.guild.id if interaction.guild else "?",
            )
        # Either the client is a zombie or move_to failed: force-disconnect so
        # the next channel.connect() doesn't see "Already connected" and so
        # Discord's server actually retracts the bot from the channel.
        await _force_cleanup_voice_client(guild_client)

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
        # If we're already connected in this guild, reuse that client only when
        # it's actually healthy; otherwise force-clean it and let the original
        # failure propagate so the caller can surface a useful error.
        if (
            "Already connected to a voice channel" in str(e)
            and interaction.guild
            and _voice_client_healthy(interaction.guild.voice_client)
        ):
            logger.info(
                "Reusing existing voice client for guild %s after 'Already connected' error",
                interaction.guild.id,
            )
            return interaction.guild.voice_client
        logger.error(
            "Voice connect failed (guild=%s, channel=%s): %s",
            interaction.guild_id, channel.id, e,
        )
        if interaction.guild is not None:
            await _force_cleanup_voice_client(interaction.guild.voice_client)
        raise
    except Exception as e:
        logger.error(
            "Voice connect failed (guild=%s, channel=%s): %s",
            interaction.guild_id, channel.id, e,
        )
        # A close mid-handshake (e.g. 4017 before DAVE) leaves the guild
        # voice_client half-attached. Force cleanup so the next attempt starts
        # clean and Discord removes the bot from the channel server-side.
        if interaction.guild is not None:
            await _force_cleanup_voice_client(interaction.guild.voice_client)
        raise
