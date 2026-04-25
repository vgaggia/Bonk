import asyncio
import os
import time

import discord
import yt_dlp

from src import log
from src.audio_bus import MixerTrack, get_guild_bus
from src.voice import connect_to_user_channel, ensure_opus, ffmpeg_available, ffmpeg_executable
from src.voice_session_manager import voice_session_manager

logger = log.setup_logger(__name__)


class MusicPlayer:
    def __init__(self):
        self.queue = []
        self.current_song = None
        self.is_playing = False
        self.voice_client = None
        self.ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [
                {
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }
            ],
            'outtmpl': 'temp_audio.%(ext)s',
        }
        # Optimized FFmpeg options for better buffering
        self.ffmpeg_options = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn -bufsize 512k',
        }
        self.livestream_ffmpeg_options = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn',
        }
        # Position tracking for TTS insertion
        self.play_start_time = None
        self.song_duration = None
        self.pause_time = None
        self.accumulated_pause_duration = 0
        # Idle timeout management (disconnect after 2 minutes of inactivity)
        self.idle_timeout_task = None
        self.idle_timeout_seconds = 120
        # Mixer integration: handle for the currently playing music/queue item
        self.current_track: MixerTrack | None = None
        # Base music volume when not ducked (used for mixer volume)
        self.music_base_volume = 0.4
        # Number of active TTS overlays currently ducking the music
        self.music_duck_count = 0

    def duck_for_tts(self) -> None:
        """Temporarily lower music volume while TTS is playing."""
        if not self.current_track:
            return
        self.music_duck_count += 1
        try:
            self.current_track.volume = self.music_base_volume * 0.5
        except Exception:
            logger.exception("Error ducking music for TTS")

    def unduck_for_tts(self) -> None:
        """Restore music volume after TTS has finished."""
        if not self.current_track or self.music_duck_count <= 0:
            return
        self.music_duck_count -= 1
        if self.music_duck_count == 0:
            try:
                self.current_track.volume = self.music_base_volume
            except Exception:
                logger.exception("Error restoring music volume after TTS")

    def get_current_position(self) -> float:
        """Get current playback position in seconds."""
        if not self.is_playing or not self.play_start_time:
            return 0.0

        elapsed = time.time() - self.play_start_time - self.accumulated_pause_duration
        return max(0.0, elapsed)

    def get_remaining_time(self) -> float:
        """Get remaining time in current song. Returns 0 if no song or livestream."""
        if not self.song_duration or self.song_duration <= 0:
            return 0.0

        remaining = self.song_duration - self.get_current_position()
        return max(0.0, remaining)

    def cancel_idle_timeout(self):
        """Cancel the idle timeout task."""
        if self.idle_timeout_task and not self.idle_timeout_task.done():
            self.idle_timeout_task.cancel()
            logger.debug("Cancelled idle timeout")

    async def start_idle_timeout(self):
        """Start idle timeout - disconnect after inactivity period."""
        self.cancel_idle_timeout()

        async def timeout_disconnect():
            try:
                await asyncio.sleep(self.idle_timeout_seconds)
                if self.voice_client and self.voice_client.is_connected():
                    guild_id = getattr(self.voice_client, 'guild', None)
                    guild_id = guild_id.id if guild_id else None
                    # Respect /stay mode
                    if guild_id and guild_id in voice_session_manager.stay_guilds:
                        logger.debug("Stay mode active, skipping idle disconnect")
                        return
                    logger.info(f"Disconnecting due to {self.idle_timeout_seconds}s inactivity")
                    await self.voice_client.disconnect()
                    self.voice_client = None
                    self.is_playing = False
                    # Clean up voice session manager too
                    if guild_id:
                        voice_session_manager.cancel_session(guild_id)
            except asyncio.CancelledError:
                logger.debug("Idle timeout cancelled")
            except Exception as e:
                logger.error(f"Error in idle timeout: {e}")

        self.idle_timeout_task = asyncio.create_task(timeout_disconnect())
        logger.info(f"Started idle timeout ({self.idle_timeout_seconds}s)")

    async def is_livestream(self, url):
        loop = asyncio.get_event_loop()

        def check_live():
            ydl_opts = {'quiet': True, 'extract_flat': True}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return info.get('is_live', False)

        return await loop.run_in_executor(None, check_live)

    async def play_next(self, interaction):
        if not self.queue:
            # Enter IDLE state
            self.is_playing = False
            # Reset tracking
            self.play_start_time = None
            self.song_duration = None
            self.accumulated_pause_duration = 0
            # Start idle timeout - only runs when nothing is playing
            await interaction.followup.send(
                "Queue is empty. Will disconnect after 2 minutes of inactivity."
            )
            await self.start_idle_timeout()
            return

        # Enter PLAYING state - cancel idle timeout
        self.cancel_idle_timeout()

        self.current_song = self.queue.pop(0)

        # If this is a placeholder that hasn't been filled yet, wait for it
        if self.current_song.get('is_placeholder', False):
            logger.info("Queue item is still processing, waiting...")
            # Put it back and wait
            self.queue.insert(0, self.current_song)
            self.current_song = None
            # Check again in 0.5 seconds
            await asyncio.sleep(0.5)
            return await self.play_next(interaction)

        self.is_playing = True
        # Store duration for position tracking
        self.song_duration = self.current_song.get('duration', 0)
        self.accumulated_pause_duration = 0

        if not self.voice_client or not self.voice_client.is_connected():
            try:
                self.voice_client = await connect_to_user_channel(interaction)
            except discord.Forbidden as e:
                await interaction.followup.send(
                    f"I lack voice permissions in this channel ({e}). Please grant CONNECT and SPEAK."
                )
                self.is_playing = False
                return
            except Exception as e:
                await interaction.followup.send(
                    "Couldn't connect to voice (code 4006). Try a different voice channel or region,"
                    " and ensure your network allows UDP traffic to Discord voice."
                )
                logger.error(f"Voice connect error: {e}")
                self.is_playing = False
                return

        # Pre-flight checks for audio stack
        if not ensure_opus():
            await interaction.followup.send(
                "Audio prerequisites missing: Opus not loaded. Install Opus and PyNaCl, then restart the bot."
            )
            await self.voice_client.disconnect()
            self.is_playing = False
            return
        if not ffmpeg_available():
            await interaction.followup.send(
                "FFmpeg not found. Install FFmpeg and ensure it's on PATH or set FFMPEG_BIN."
            )
            await self.voice_client.disconnect()
            self.is_playing = False
            return

        try:
            # Check if this is a local file (TTS) instead of a YouTube URL
            if self.current_song.get('is_local_file', False):
                await self.play_local_file(interaction)
            else:
                is_live = await self.is_livestream(self.current_song['url'])
                if is_live:
                    await self.play_livestream(interaction)
                else:
                    await self.play_regular_audio(interaction)

        except Exception as e:
            logger.error(f"Error playing {self.current_song['title']}: {str(e)}")
            await interaction.followup.send(
                f"An error occurred while playing {self.current_song['title']}: {str(e)}"
            )
            await self.song_finished(interaction)

    async def play_livestream(self, interaction):
        # Extract stream URL in executor to avoid blocking
        loop = asyncio.get_event_loop()

        def get_stream_url():
            ydl_opts = {'format': 'bestaudio/best', 'quiet': True}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.current_song['url'], download=False)
                return info['url']

        url = await loop.run_in_executor(None, get_stream_url)

        audio_source = discord.FFmpegPCMAudio(
            url, executable=ffmpeg_executable(), **self.livestream_ffmpeg_options
        )
        self.play_start_time = time.time()
        bus = get_guild_bus(interaction.guild.id)
        bus.attach_voice_client(self.voice_client)

        def on_done(error: Exception | None = None):
            if error:
                logger.error(f"Error during livestream playback: {error}")
            asyncio.run_coroutine_threadsafe(
                self.song_finished(interaction), interaction.client.loop
            )

        # Slightly lower volume so TTS over music remains clear.
        self.current_track = bus.add_track(
            audio_source, volume=self.music_base_volume, on_done=on_done
        )
        await interaction.followup.send(f"Now streaming: {self.current_song['title']}")
        logger.info(f"Started streaming: {self.current_song['title']}")

    async def play_regular_audio(self, interaction):
        # Extract stream URL in executor to avoid blocking
        loop = asyncio.get_event_loop()

        def get_stream_url():
            ydl_opts_stream = {'format': 'bestaudio/best', 'quiet': True}
            with yt_dlp.YoutubeDL(ydl_opts_stream) as ydl:
                info = ydl.extract_info(self.current_song['url'], download=False)
                return info['url']

        stream_url = await loop.run_in_executor(None, get_stream_url)

        # Play from stream URL immediately
        audio_source = discord.FFmpegPCMAudio(
            stream_url, executable=ffmpeg_executable(), **self.ffmpeg_options
        )
        self.play_start_time = time.time()
        bus = get_guild_bus(interaction.guild.id)
        bus.attach_voice_client(self.voice_client)

        def on_done(error: Exception | None = None):
            if error:
                logger.error(f"Error during audio playback: {error}")
            asyncio.run_coroutine_threadsafe(
                self.song_finished(interaction), interaction.client.loop
            )

        # Slightly lower volume so TTS over music remains clear.
        self.current_track = bus.add_track(
            audio_source, volume=self.music_base_volume, on_done=on_done
        )
        await interaction.followup.send(f"Now playing: {self.current_song['title']}")
        logger.info(f"Started playing: {self.current_song['title']}")

    async def play_local_file(self, interaction):
        """Play a local audio file (for TTS)."""
        file_path = str(self.current_song['file_path'])  # Ensure it's a string

        # Safety check - file path should never be None at this point
        if not file_path or not os.path.exists(file_path):
            logger.error(f"Local file not found: {file_path}")
            await interaction.followup.send("Error: Audio file not found")
            await self.song_finished(interaction)
            return

        logger.info(
            f"Playing TTS file from queue: {file_path}, exists: {os.path.exists(file_path)}, "
            f"size: {os.path.getsize(file_path) if os.path.exists(file_path) else 0}"
        )

        # Use simpler FFmpeg options for local files
        simple_options = {'options': '-vn'}

        audio_source = discord.FFmpegPCMAudio(
            file_path, executable=ffmpeg_executable(), **simple_options
        )
        self.play_start_time = time.time()

        bus = get_guild_bus(interaction.guild.id)
        bus.attach_voice_client(self.voice_client)

        def on_done(error: Exception | None = None):
            if error:
                logger.error(f"Error during queued TTS playback: {error}")
            asyncio.run_coroutine_threadsafe(
                self.song_finished(interaction), interaction.client.loop
            )

        self.current_track = bus.add_track(audio_source, volume=1.0, on_done=on_done)
        # Only send message if we can (don't fail if interaction is old)
        try:
            await interaction.followup.send(f"Now playing: {self.current_song['title']}")
        except Exception:
            pass
        logger.info(f"Playing local file: {self.current_song['title']}")

    async def song_finished(self, interaction):
        self.is_playing = False
        # Clear the handle for the track that just finished and reset ducking
        self.current_track = None
        self.music_duck_count = 0

        # Save reference to the song that just finished
        finished_song = self.current_song

        # Play next song (this will update self.current_song)
        await self.play_next(interaction)

        # Now clean up the finished song's file if it was local
        if finished_song and finished_song.get('is_local_file', False):
            file_path = finished_song.get('file_path')
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    logger.info(f"Cleaned up TTS file: {file_path}")
                except Exception as e:
                    logger.error(f"Error cleaning up TTS file: {e}")


music_player = MusicPlayer()


async def play(interaction: discord.Interaction, search: str):
    if not interaction.user.voice:
        await interaction.followup.send("You need to be in a voice channel to use this command.")
        return

    channel = interaction.user.voice.channel

    # Sync music_player.voice_client with the actual guild voice client
    # to avoid desync when TTS or /listen connected the bot independently.
    guild_vc = interaction.guild.voice_client if interaction.guild else None
    if not music_player.voice_client or not music_player.voice_client.is_connected():
        if guild_vc and guild_vc.is_connected():
            music_player.voice_client = guild_vc

    if (
        not music_player.voice_client
        or not music_player.voice_client.is_connected()
        or music_player.voice_client.channel != channel
    ):
        try:
            music_player.voice_client = await connect_to_user_channel(interaction)
        except discord.Forbidden as e:
            await interaction.followup.send(
                f"I lack voice permissions in this channel ({e}). Please grant CONNECT and SPEAK."
            )
            return
        except Exception as e:
            await interaction.followup.send(
                "Couldn’t connect to voice (code 4006). Try a different voice channel or region,"
                " and ensure your network allows UDP traffic to Discord voice."
            )
            logger.error(f"Voice connect error: {e}")
            return

    try:
        # Determine if it's a URL or search query
        if not search.startswith('http://') and not search.startswith('https://'):
            # Use yt-dlp's search functionality
            search_query = f"ytsearch1:{search}"
        else:
            search_query = search

        # Run info extraction in executor to avoid blocking
        loop = asyncio.get_event_loop()

        def extract_info():
            is_live_check = False
            with yt_dlp.YoutubeDL({'quiet': True, 'extract_flat': True}) as ydl:
                info_check = ydl.extract_info(search_query, download=False)
                # Handle search results
                if 'entries' in info_check:
                    if not info_check['entries']:
                        return None
                    info_check = info_check['entries'][0]
                is_live_check = info_check.get('is_live', False)

            with yt_dlp.YoutubeDL(music_player.ydl_opts) as ydl:
                info = ydl.extract_info(search_query, download=False)
                if 'entries' in info:
                    if not info['entries']:
                        return None
                    info = info['entries'][0]
                return {
                    'url': info.get('webpage_url', info.get('url')),
                    'title': info['title'],
                    'duration': info.get('duration', 0) if not is_live_check else 0,
                    'is_live': is_live_check,
                }

        info_data = await loop.run_in_executor(None, extract_info)

        if not info_data:
            await interaction.followup.send("No results found for the given query.")
            return

        music_player.queue.append(
            {
                'url': info_data['url'],
                'title': info_data['title'],
                'is_live': info_data['is_live'],
                'duration': info_data['duration'],
            }
        )

        await interaction.followup.send(
            f"Added to queue: {info_data['title']}"
            + (" (Livestream)" if info_data['is_live'] else "")
        )

        # If idle, start playing (this will cancel the timeout automatically)
        if not music_player.is_playing:
            await music_player.play_next(interaction)
    except Exception as e:
        logger.error(f"Error in play command: {str(e)}")
        await interaction.followup.send(
            f"An error occurred while trying to play the video: {str(e)}"
        )


async def stop(interaction: discord.Interaction):
    """Stop all audio and disconnect from voice, even if only TTS is playing."""
    # Prefer the music player's voice client if present
    voice_client = music_player.voice_client or (
        interaction.guild and interaction.guild.voice_client
    )

    if voice_client:
        music_player.is_playing = False
        music_player.cancel_idle_timeout()
        # Stop all tracks on the guild audio bus as well
        try:
            bus = get_guild_bus(interaction.guild.id)
            bus.stop_all()
        except Exception:
            # Fallback to stopping the voice client directly
            voice_client.stop()
        await voice_client.disconnect()
        music_player.queue.clear()
        music_player.current_song = None
        music_player.current_track = None
        music_player.music_duck_count = 0
        music_player.voice_client = None
        # Clean up voice session manager to prevent stale timers
        if interaction.guild:
            voice_session_manager.cancel_session(interaction.guild.id)
            voice_session_manager.stay_guilds.discard(interaction.guild.id)
        await interaction.followup.send("Stopped playback and disconnected from voice.")
    else:
        await interaction.followup.send("I'm not currently in a voice channel.")


async def pause(interaction: discord.Interaction):
    # TODO: This pauses the entire voice client (including TTS) because the mixer
    # architecture routes all audio through a single MixedAudioSource. Selectively
    # pausing only music would require per-track pause support in the mixer.
    if music_player.voice_client and music_player.voice_client.is_playing():
        music_player.voice_client.pause()
        await interaction.followup.send("Playback paused.")
    else:
        await interaction.followup.send("Nothing is playing right now.")


async def resume(interaction: discord.Interaction):
    if music_player.voice_client and music_player.voice_client.is_paused():
        music_player.voice_client.resume()
        await interaction.followup.send("Playback resumed.")
    else:
        await interaction.followup.send("Playback is not paused.")


async def next(interaction: discord.Interaction):
    if music_player.voice_client and music_player.is_playing:
        # Stop only the current music track; other mixed audio (e.g. TTS)
        # is allowed to continue playing.
        if music_player.current_track is not None:
            try:
                bus = get_guild_bus(interaction.guild.id)
                bus.stop_track(music_player.current_track)
            except Exception:
                # Fallback: stop the whole voice client if bus is unavailable
                music_player.voice_client.stop()
        else:
            music_player.voice_client.stop()
        await interaction.followup.send("Skipping to the next song.")
        # Manually advance the queue, since stopping the track early
        # suppresses the normal on_done callback that would call
        # song_finished() when playback ends.
        await music_player.song_finished(interaction)
    else:
        await interaction.followup.send("No song is currently playing.")
