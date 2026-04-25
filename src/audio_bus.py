import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import discord

from src import log

logger = log.setup_logger(__name__)


@dataclass
class MixerTrack:
    """Represents a single track in the mixed audio stream."""

    source: discord.AudioSource
    volume: float = 1.0
    on_done: Optional[Callable[[Optional[BaseException]], None]] = None
    stopped: bool = False


class MixedAudioSource(discord.AudioSource):
    """AudioSource that mixes multiple PCM tracks into a single stream.

    - Expects each underlying source to provide 16-bit PCM at 48kHz stereo.
    - Returns 20ms frames (3840 bytes) suitable for discord.py's Opus encoder.
    - Mixing is done in the PCM domain using audioop with clipping.
    """

    def __init__(self, frame_size: int = 3840) -> None:
        self._frame_size = frame_size
        self._tracks: List[MixerTrack] = []
        self._lock = threading.Lock()

    def add_track(
        self,
        source: discord.AudioSource,
        volume: float = 1.0,
        on_done: Optional[Callable[[Optional[BaseException]], None]] = None,
    ) -> MixerTrack:
        """Add a new track to be mixed.

        Returns a MixerTrack handle that can be used with stop_track().
        """
        track = MixerTrack(source=source, volume=volume, on_done=on_done)
        with self._lock:
            self._tracks.append(track)
        return track

    def stop_track(self, track: MixerTrack) -> None:
        """Mark a track as stopped; it will be removed on the next read()."""
        with self._lock:
            track.stopped = True

    def clear_tracks(self, error: Optional[BaseException] = None) -> None:
        """Stop and remove all tracks, invoking callbacks and cleanup."""
        with self._lock:
            tracks = list(self._tracks)
            self._tracks.clear()

        for track in tracks:
            try:
                if track.on_done:
                    track.on_done(error)
            except Exception:
                logger.exception("Error in on_done callback during clear_tracks")
            try:
                cleanup = getattr(track.source, "cleanup", None)
                if callable(cleanup):
                    cleanup()
            except Exception:
                logger.exception("Error cleaning up track source during clear_tracks")

    def has_active_tracks(self) -> bool:
        """Return True if any tracks are currently active."""
        with self._lock:
            return any(not t.stopped for t in self._tracks)

    def read(self) -> bytes:
        """Read and mix one frame of audio from all active tracks."""
        import audioop

        with self._lock:
            tracks = list(self._tracks)

        if not tracks:
            # No tracks -> end of stream
            return b""

        mixed: Optional[bytes] = None
        finished: List[MixerTrack] = []

        for track in tracks:
            if track.stopped:
                finished.append(track)
                continue

            try:
                chunk = track.source.read()
            except Exception as exc:
                logger.error("Error reading from audio source", exc_info=True)
                finished.append(track)
                if track.on_done:
                    try:
                        track.on_done(exc)
                    except Exception:
                        logger.exception("Error in on_done callback after read error")
                continue

            if not chunk:
                finished.append(track)
                continue

            # Normalise frame size
            if len(chunk) < self._frame_size:
                chunk = chunk.ljust(self._frame_size, b"\x00")
            elif len(chunk) > self._frame_size:
                chunk = chunk[: self._frame_size]

            # Apply per-track volume
            if track.volume != 1.0:
                try:
                    chunk = audioop.mul(chunk, 2, track.volume)
                except Exception:
                    logger.exception("Error applying volume to track")
                    # Fallback to unscaled chunk

            if mixed is None:
                mixed = chunk
            else:
                try:
                    mixed = audioop.add(mixed, chunk, 2)
                except Exception:
                    logger.exception("Error mixing audio chunks")
                    # Fallback: keep previous mixed buffer

        if finished:
            with self._lock:
                for track in finished:
                    if track in self._tracks:
                        self._tracks.remove(track)

            for track in finished:
                try:
                    cleanup = getattr(track.source, "cleanup", None)
                    if callable(cleanup):
                        cleanup()
                except Exception:
                    logger.exception("Error cleaning up finished track source")

                if track.on_done and not track.stopped:
                    try:
                        track.on_done(None)
                    except Exception:
                        logger.exception("Error in on_done callback for finished track")

        if mixed is None:
            # All tracks finished or stopped without producing audio
            return b""

        return mixed

    def is_opus(self) -> bool:
        """Return False to indicate PCM output (discord.py handles Opus)."""
        return False

    def cleanup(self) -> None:
        """Clean up all tracks."""
        self.clear_tracks()


class GuildAudioBus:
    """Per-guild audio bus that owns a MixedAudioSource and a VoiceClient.

    This class does not manage connections or timeouts; it assumes callers
    attach an already-connected VoiceClient and handles only playback/mixing.
    """

    def __init__(self) -> None:
        self.voice_client: Optional[discord.VoiceClient] = None
        self.mixer = MixedAudioSource()
        self._lock = threading.Lock()
        self._is_playing = False

    def attach_voice_client(self, voice_client: discord.VoiceClient) -> None:
        """Attach the VoiceClient that will play the mixer."""
        self.voice_client = voice_client

    def add_track(
        self,
        source: discord.AudioSource,
        volume: float = 1.0,
        on_done: Optional[Callable[[Optional[BaseException]], None]] = None,
    ) -> MixerTrack:
        """Add a track to the mixer and start playback if needed."""
        if not self.voice_client or not self.voice_client.is_connected():
            raise RuntimeError("Voice client is not attached or not connected")

        track = self.mixer.add_track(source, volume=volume, on_done=on_done)

        with self._lock:
            if not self._is_playing or not self.voice_client.is_playing():
                try:
                    self.voice_client.play(self.mixer, after=self._handle_stream_end)
                    self._is_playing = True
                except Exception:
                    logger.exception("Failed to start mixer playback")
                    # Ensure we do not leak the track if playback fails
                    self.mixer.stop_track(track)
                    raise

        return track

    def stop_track(self, track: MixerTrack) -> None:
        """Stop a specific track early."""
        self.mixer.stop_track(track)

    def stop_all(self) -> None:
        """Stop all tracks and halt playback."""
        self.mixer.clear_tracks()
        with self._lock:
            if self.voice_client and self.voice_client.is_playing():
                try:
                    self.voice_client.stop()
                except Exception:
                    logger.exception("Error stopping voice client")
            self._is_playing = False

    def _handle_stream_end(self, error: Optional[BaseException]) -> None:
        """Called by discord.py when mixer playback ends."""
        if error:
            logger.error("Error in mixed audio playback", exc_info=error)
        with self._lock:
            self._is_playing = False


_buses: Dict[int, GuildAudioBus] = {}


def get_guild_bus(guild_id: int) -> GuildAudioBus:
    """Get or create the GuildAudioBus for a guild."""
    bus = _buses.get(guild_id)
    if bus is None:
        bus = GuildAudioBus()
        _buses[guild_id] = bus
    return bus
