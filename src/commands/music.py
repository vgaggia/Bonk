import asyncio
import os
import time

import discord
import yt_dlp

from src import log
from src.voice import connect_to_user_channel, ensure_opus, ffmpeg_available, ffmpeg_executable

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
        # Track if download is ready for TTS mixing
        self.download_ready = asyncio.Event()
        # Flag to prevent file deletion during TTS mixing
        self.is_mixing_tts = False
        # Idle timeout management (disconnect after 2 minutes of inactivity)
        self.idle_timeout_task = None
        self.idle_timeout_seconds = 120

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
                    logger.info(f"Disconnecting due to {self.idle_timeout_seconds}s inactivity")
                    await self.voice_client.disconnect()
                    self.voice_client = None
                    self.is_playing = False
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
            await interaction.followup.send("Queue is empty. Will disconnect after 2 minutes of inactivity.")
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
        self.voice_client.play(
            audio_source,
            after=lambda e: asyncio.run_coroutine_threadsafe(
                self.song_finished(interaction), interaction.client.loop
            ),
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
        self.voice_client.play(
            audio_source,
            after=lambda e: asyncio.run_coroutine_threadsafe(
                self.song_finished(interaction), interaction.client.loop
            ),
        )
        await interaction.followup.send(f"Now playing: {self.current_song['title']}")
        logger.info(f"Started playing: {self.current_song['title']}")

    async def play_local_file(self, interaction):
        """Play a local audio file (for TTS)."""
        file_path = str(self.current_song['file_path'])  # Ensure it's a string

        # Safety check - file path should never be None at this point
        if not file_path or not os.path.exists(file_path):
            logger.error(f"Local file not found: {file_path}")
            await interaction.followup.send(f"Error: Audio file not found")
            await self.song_finished(interaction)
            return

        # Check if voice client is already playing something
        if self.voice_client.is_playing():
            logger.warning("Voice client is still playing, waiting for it to finish...")
            await asyncio.sleep(0.5)  # Increased wait time
            if self.voice_client.is_playing():
                logger.error("Voice client still playing after wait, stopping it")
                self.voice_client.stop()
                await asyncio.sleep(0.3)  # More time for cleanup

        logger.info(f"Playing TTS file: {file_path}, exists: {os.path.exists(file_path)}, size: {os.path.getsize(file_path) if os.path.exists(file_path) else 0}")

        # Use simpler FFmpeg options for local files
        simple_options = {
            'options': '-vn'
        }

        audio_source = discord.FFmpegPCMAudio(
            file_path, executable=ffmpeg_executable(), **simple_options
        )
        self.play_start_time = time.time()

        def after_playback(error):
            if error:
                logger.error(f"Error during TTS playback: {error}")
            asyncio.run_coroutine_threadsafe(
                self.song_finished(interaction), interaction.client.loop
            )

        self.voice_client.play(audio_source, after=after_playback)
        # Only send message if we can (don't fail if interaction is old)
        try:
            await interaction.followup.send(f"Now playing: {self.current_song['title']}")
        except Exception:
            pass
        logger.info(f"Playing local file: {self.current_song['title']}")

    async def song_finished(self, interaction):
        self.is_playing = False

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

    def reserve_tts_slot(self, title: str = "TTS Audio (processing...)"):
        """Reserve a slot in the queue for TTS that's being generated."""
        placeholder = {
            'file_path': None,  # Will be filled in later
            'title': title,
            'is_local_file': True,
            'is_live': False,
            'duration': 0,
            'is_placeholder': True,
        }
        # Insert at the front of the queue (plays next)
        self.queue.insert(0, placeholder)
        logger.info(f"Reserved TTS slot in queue at position 0")
        return placeholder

    def update_tts_slot(self, placeholder: dict, file_path: str, title: str):
        """Update a reserved TTS slot with the actual file."""
        placeholder['file_path'] = file_path
        placeholder['title'] = title
        placeholder['is_placeholder'] = False
        logger.info(f"Updated TTS slot with file: {file_path}")

    def remove_tts_slot(self, placeholder: dict):
        """Remove a reserved TTS slot if generation failed."""
        if placeholder in self.queue:
            self.queue.remove(placeholder)
            logger.info("Removed failed TTS slot from queue")

    async def insert_tts_mixed(self, tts_file: str, interaction: discord.Interaction) -> bool:
        """
        Insert TTS audio by mixing it with current music playback.
        Seamlessly swaps to mixed segment and back to original music.

        Args:
            tts_file: Path to TTS audio file
            interaction: Discord interaction for followup messages

        Returns:
            True if successful, False otherwise
        """
        from src.audio_mixer import create_mixed_tts_segment, get_audio_duration

        if not self.is_playing or not self.current_song:
            logger.warning("Cannot insert TTS: no music playing")
            return False

        # Don't mix into livestreams
        if self.current_song.get('is_live', False):
            logger.info("Current song is livestream, queueing TTS instead")
            return False

        try:
            # Set flag to prevent file deletion during mixing
            self.is_mixing_tts = True

            # Wait for download to complete (with timeout)
            try:
                await asyncio.wait_for(self.download_ready.wait(), timeout=30.0)
            except asyncio.TimeoutError:
                logger.warning("Download not ready after 30s, falling back to queue")
                self.is_mixing_tts = False
                return False

            # Verify file exists
            if not os.path.exists('temp_audio.mp3'):
                logger.warning("temp_audio.mp3 not found, falling back to queue")
                self.is_mixing_tts = False
                return False

            # Create a copy of temp_audio.mp3 to avoid file locking issues
            import shutil

            music_copy = 'temp_audio_copy.mp3'
            try:
                shutil.copy2('temp_audio.mp3', music_copy)
                logger.info("Created copy of temp_audio.mp3 for mixing")
            except Exception as e:
                logger.error(f"Failed to copy audio file: {e}")
                self.is_mixing_tts = False
                return False

            # Get current state
            current_position = self.get_current_position()
            remaining_time = self.get_remaining_time()

            # Segment length is minimum of 30s or remaining time
            segment_duration = min(30.0, remaining_time)

            logger.info(
                f"Inserting TTS at position {current_position:.1f}s, segment: {segment_duration:.1f}s"
            )

            # Create mixed segment using the copy
            mixed_file = create_mixed_tts_segment(
                music_file=music_copy,
                tts_file=tts_file,
                current_position=current_position,
                segment_duration=segment_duration,
                output_file='temp_mixed_tts.mp3',
            )

            if not mixed_file:
                logger.error("Failed to create mixed segment")
                # Clean up copy
                if os.path.exists(music_copy):
                    os.remove(music_copy)
                return False

            # Clean up the copy now that mixing is done
            if os.path.exists(music_copy):
                os.remove(music_copy)
            logger.info("Cleaned up music copy")

            # Calculate when to resume original music
            logger.info("Getting mixed file duration...")
            mixed_duration = get_audio_duration(mixed_file)
            logger.info(f"Mixed file duration: {mixed_duration}")

            if not mixed_duration:
                logger.error("Could not determine mixed file duration")
                if os.path.exists(mixed_file):
                    os.remove(mixed_file)
                return False

            # Stop current playback
            logger.info("Stopping current playback...")
            if self.voice_client and self.voice_client.is_playing():
                self.voice_client.stop()
                await asyncio.sleep(0.1)  # Brief pause for clean stop
            logger.info("Current playback stopped")

            # Play mixed segment
            logger.info("Creating audio source for mixed segment...")
            audio_source = discord.FFmpegPCMAudio(
                mixed_file, executable=ffmpeg_executable(), **self.ffmpeg_options
            )
            logger.info("Audio source created")

            resume_position = current_position + segment_duration
            resume_interaction = interaction

            def after_mixed(error):
                """Callback after mixed segment finishes."""
                if error:
                    logger.error(f"Error during mixed playback: {error}")

                # Clean up mixed file
                if os.path.exists('temp_mixed_tts.mp3'):
                    try:
                        os.remove('temp_mixed_tts.mp3')
                    except Exception as e:
                        logger.error(f"Error removing mixed file: {e}")

                # Clear mixing flag so file can be deleted later if needed
                self.is_mixing_tts = False

                # Resume original music from calculated position
                asyncio.run_coroutine_threadsafe(
                    self._resume_music_at_position(resume_position, resume_interaction),
                    resume_interaction.client.loop,
                )

            logger.info("Starting playback of mixed segment...")
            self.voice_client.play(audio_source, after=after_mixed)
            logger.info(f"Playing mixed segment, will resume at {resume_position:.1f}s")

            return True

        except Exception as e:
            logger.error(f"Error inserting TTS: {e}", exc_info=True)
            # Clear mixing flag on error
            self.is_mixing_tts = False
            # Clean up on error
            if os.path.exists('temp_mixed_tts.mp3'):
                try:
                    os.remove('temp_mixed_tts.mp3')
                except Exception:
                    pass
            if os.path.exists(music_copy):
                try:
                    os.remove(music_copy)
                except Exception:
                    pass
            return False

    async def _resume_music_at_position(self, position: float, interaction: discord.Interaction):
        """Resume original music from a specific position."""
        try:
            if not self.current_song or not os.path.exists('temp_audio.mp3'):
                logger.warning("Cannot resume: music file not found")
                await self.song_finished(interaction)
                return

            # Create audio source starting from position
            # Combine seek with existing before_options
            resume_options = self.ffmpeg_options.copy()
            resume_options['before_options'] = f"{resume_options['before_options']} -ss {position}"

            audio_source = discord.FFmpegPCMAudio(
                'temp_audio.mp3',
                executable=ffmpeg_executable(),
                **resume_options,
            )

            # Update tracking to reflect new position
            self.play_start_time = time.time() - position

            # Play from position
            self.voice_client.play(
                audio_source,
                after=lambda e: asyncio.run_coroutine_threadsafe(
                    self.song_finished(interaction), interaction.client.loop
                ),
            )

            logger.info(f"Resumed music at position {position:.1f}s")

        except Exception as e:
            logger.error(f"Error resuming music: {e}")
            await self.song_finished(interaction)


music_player = MusicPlayer()


async def play(interaction: discord.Interaction, search: str):
    if not interaction.user.voice:
        await interaction.followup.send("You need to be in a voice channel to use this command.")
        return

    channel = interaction.user.voice.channel
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
    if music_player.voice_client:
        music_player.is_playing = False
        music_player.cancel_idle_timeout()
        music_player.voice_client.stop()
        await music_player.voice_client.disconnect()
        music_player.queue.clear()
        music_player.current_song = None
        music_player.voice_client = None
        await interaction.followup.send("Stopped playback and cleared the queue.")
    else:
        await interaction.followup.send("I'm not currently in a voice channel.")


async def pause(interaction: discord.Interaction):
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
        music_player.voice_client.stop()
        await interaction.followup.send("Skipping to the next song.")
    else:
        await interaction.followup.send("No song is currently playing.")
