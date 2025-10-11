import asyncio
import logging
from typing import Dict, Optional
import discord

logger = logging.getLogger(__name__)


class VoiceSessionManager:
    """Manages persistent voice connections with automatic timeout disconnection."""

    def __init__(self, timeout_minutes: int = 3):
        self.timeout_seconds = timeout_minutes * 60
        self.active_sessions: Dict[int, asyncio.Task] = {}  # guild_id -> disconnect task

    async def get_or_connect(self, interaction: discord.Interaction) -> Optional[discord.VoiceClient]:
        """Get existing voice client or connect to user's channel."""
        from src.voice import connect_to_user_channel

        if not interaction.user.voice:
            return None

        guild_id = interaction.guild.id

        try:
            # Connect or reuse existing connection
            voice_client = await connect_to_user_channel(interaction)

            # Cancel existing disconnect timer for this guild
            if guild_id in self.active_sessions:
                self.active_sessions[guild_id].cancel()
                logger.debug(f"Cancelled existing disconnect timer for guild {guild_id}")

            # Start new disconnect timer
            self.active_sessions[guild_id] = asyncio.create_task(
                self._schedule_disconnect(voice_client, guild_id)
            )
            logger.info(f"Voice session active for guild {guild_id}, will disconnect in {self.timeout_seconds}s")

            return voice_client

        except Exception as e:
            logger.error(f"Failed to get voice client: {e}")
            return None

    async def _schedule_disconnect(self, voice_client: discord.VoiceClient, guild_id: int):
        """Schedule automatic disconnection after timeout."""
        try:
            await asyncio.sleep(self.timeout_seconds)

            if voice_client.is_connected():
                await voice_client.disconnect()
                logger.info(f"Auto-disconnected from voice channel in guild {guild_id} after timeout")

            # Clean up the session
            if guild_id in self.active_sessions:
                del self.active_sessions[guild_id]

        except asyncio.CancelledError:
            # Timer was cancelled - this is normal when extending the session
            logger.debug(f"Disconnect timer cancelled for guild {guild_id}")
        except Exception as e:
            logger.error(f"Error in voice session timer: {e}")
            # Clean up session on error
            if guild_id in self.active_sessions:
                del self.active_sessions[guild_id]

    async def queue_audio(self, voice_client: discord.VoiceClient, audio_source, after_callback=None):
        """Queue audio to play, waiting if something is already playing."""
        # If something is playing, wait for it to finish
        while voice_client.is_playing():
            await asyncio.sleep(0.1)

        # Play the audio
        voice_client.play(audio_source, after=after_callback)

        # Wait for this audio to finish
        while voice_client.is_playing():
            await asyncio.sleep(0.1)

    def cancel_session(self, guild_id: int):
        """Manually cancel a voice session (force disconnect)."""
        if guild_id in self.active_sessions:
            self.active_sessions[guild_id].cancel()
            del self.active_sessions[guild_id]


# Global instance
voice_session_manager = VoiceSessionManager(timeout_minutes=3)