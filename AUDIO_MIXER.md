## Audio Mixer Design (Planned - v3 with Opus)

This document describes a planned rework of voice audio handling so that:

- `/play` can keep a single music stream running.
- `/tts` calls can be layered on top of that music without pausing it.
- Multiple concurrent `/tts` calls from different users can overlap.
- Discord still only sees **one** audio stream per guild/voice connection.
- **Uses Opus encoding for efficient bandwidth usage.**

Nothing in this document is assumed to be implemented yet; it is a design
target to guide future changes.

---

## Problem & Goals

Discord voice allows only **one** audio stream per voice connection. Today:

- `music.py` pushes a single `FFmpegPCMAudio` into `voice_client.play(...)`.
- `tts.py` either:
  - Uses its own `voice_session_manager` to play TTS when no music is playing, or
  - Uses the older, file-based `audio_mixer.py` path that tries to render a
    pre‑mixed segment (`insert_tts_mixed`) and temporarily replaces music.

The segment approach is brittle:

- Needs the music file on disk and seeks into it (`temp_audio.mp3`).
- Interrupts the current stream, then tries to resume later.
- Cannot support **multiple** overlapping TTS clips cleanly.

What we actually want:

- Keep music playing continuously as one logical track.
- Allow many short‑lived TTS clips to start/stop independently on top.
- Mix everything into a single stream that is fed to Discord.
- **Use Opus encoding for bandwidth efficiency (3-10x reduction).**

---

## High-Level Architecture

### 1. MixedAudioSource with Opus Support

Create a custom `discord.AudioSource` implementation that handles mixing and Opus encoding.

**Key Design Decision: PCM Mixing → Opus Encoding**
- Mix tracks in PCM domain (easier math, no quality loss)
- Encode the final mixed result to Opus
- Discord.py handles Opus packet transmission

There are two implementation approaches:

#### Option A: Return PCM, Let Discord.py Encode (Simpler)

```python
class MixedAudioSource(discord.AudioSource):
    def is_opus(self) -> bool:
        return False  # Return PCM, discord.py encodes it
    
    def read(self) -> bytes:
        # Mix tracks and return PCM
        return mixed_pcm_frame  # 3840 bytes
```

**Pros:**
- Simpler implementation
- Discord.py handles Opus encoding automatically
- No manual Opus encoder management

**Cons:**
- Slightly higher CPU usage (discord.py does encoding)
- Less control over encoding parameters

#### Option B: Manual Opus Encoding (More Control)

```python
class MixedAudioSource(discord.AudioSource):
    def __init__(self, ...):
        self._encoder = discord.opus.Encoder()
        # Configure encoder: 48kHz, 2 channels, 192kbps
        self._encoder.set_bitrate(192)
        self._encoder.set_fec(True)  # Forward error correction
        self._encoder.set_expected_packet_loss(5)  # 5% expected loss
    
    def is_opus(self) -> bool:
        return True  # We return Opus packets
    
    def read(self) -> bytes:
        # Mix tracks in PCM
        mixed_pcm = self._mix_tracks()  # 3840 bytes
        
        # Encode to Opus (returns ~100-300 bytes typically)
        opus_data = self._encoder.encode(mixed_pcm, 960)  # 960 samples = 20ms
        return opus_data
```

**Pros:**
- Full control over encoding parameters
- Can optimize for music vs voice dynamically
- Lower bandwidth usage
- Can add FEC for packet loss resilience

**Cons:**
- More complex implementation
- Need to manage encoder lifecycle
- Must handle encoder errors

### Recommended Approach: Hybrid with Fallback

Start with Option A (simpler) and add Option B as an optimization:

```python
import threading
import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Callable, List
import numpy as np
import discord

class TrackType(Enum):
    MUSIC = 0
    TTS = 1  
    SFX = 2

@dataclass
class Track:
    source: discord.AudioSource
    volume: float = 1.0
    track_type: TrackType = TrackType.MUSIC
    on_done: Optional[Callable] = None
    buffer: bytearray = None
    
    def __post_init__(self):
        self.buffer = bytearray()

class MixedAudioSource(discord.AudioSource):
    def __init__(self, frame_size: int = 3840, 
                 loop: Optional[asyncio.AbstractEventLoop] = None,
                 use_opus: bool = False,
                 opus_kbps: int = 192):
        """
        Initialize the mixer.
        
        Args:
            frame_size: PCM frame size in bytes (3840 = 20ms @ 48kHz stereo)
            loop: Event loop for callbacks
            use_opus: If True, encode to Opus internally; if False, return PCM
            opus_kbps: Opus bitrate in kbps (64-510, default 192)
        """
        self._tracks: List[Track] = []
        self._frame_size = frame_size
        self._lock = threading.Lock()
        self._loop = loop or asyncio.get_event_loop()
        self._silence_pcm = b'\x00' * frame_size
        self._use_opus = use_opus
        
        # Opus encoder (only if use_opus=True)
        self._encoder = None
        if use_opus:
            try:
                if not discord.opus.is_loaded():
                    discord.opus.load_opus('opus')  # or appropriate path
                
                # Create encoder: 48kHz, 2 channels
                self._encoder = discord.opus.Encoder(48000, 2)
                
                # Configure for high quality
                bitrate_bps = opus_kbps * 1000
                self._encoder.set_bitrate(bitrate_bps)
                self._encoder.set_fec(True)  # Enable forward error correction
                self._encoder.set_expected_packet_loss(5)  # Expect 5% packet loss
                self._encoder.set_bandwidth('full')  # Full band for music
                
                # Cache silence in Opus format
                self._silence_opus = self._encoder.encode(self._silence_pcm, 960)
                
            except Exception as e:
                print(f"Failed to initialize Opus encoder: {e}")
                self._use_opus = False
                self._encoder = None

    def add_track(self, source: discord.AudioSource,
                  volume: float = 1.0,
                  track_type: TrackType = TrackType.MUSIC,
                  on_done: Optional[Callable[[], None]] = None) -> None:
        """Add a new audio track to the mixer (thread-safe)."""
        with self._lock:
            track = Track(source, volume, track_type, on_done)
            self._tracks.append(track)
            
            # Dynamically adjust encoder for voice if TTS added
            if self._encoder and track_type == TrackType.TTS:
                # Optimize for speech mixing
                self._encoder.set_signal_type('voice')

    def read(self) -> bytes:
        """Read and mix audio from all active tracks (called from Discord's thread)."""
        with self._lock:
            if not self._tracks:
                # Return silence (Opus or PCM based on mode)
                if self._use_opus and self._encoder:
                    return self._silence_opus
                else:
                    return self._silence_pcm
            
            # Collect PCM frames from all tracks
            frames = []
            tracks_to_remove = []
            
            # Check if TTS is playing for auto-ducking
            has_tts = any(t.track_type == TrackType.TTS for t in self._tracks)
            has_music = any(t.track_type == TrackType.MUSIC for t in self._tracks)
            
            # Adjust encoder signal type based on content
            if self._encoder:
                if has_tts and not has_music:
                    self._encoder.set_signal_type('voice')
                elif has_music:
                    self._encoder.set_signal_type('music')
                else:
                    self._encoder.set_signal_type('auto')
            
            for track in self._tracks:
                try:
                    # Read from source
                    data = track.source.read()
                    
                    if not data:  # EOF
                        tracks_to_remove.append(track)
                        continue
                    
                    # If source provides Opus, decode it first
                    if track.source.is_opus():
                        # We need to decode Opus sources to PCM for mixing
                        # This requires an Opus decoder - discord.py doesn't expose it directly
                        # For now, we'll require all sources to provide PCM
                        raise NotImplementedError("Opus sources not supported yet - use FFmpegPCMAudio")
                    
                    # Buffer management for partial reads
                    track.buffer.extend(data)
                    
                    if len(track.buffer) >= self._frame_size:
                        frame = bytes(track.buffer[:self._frame_size])
                        track.buffer = track.buffer[self._frame_size:]
                        
                        # Apply volume with auto-ducking
                        effective_volume = track.volume
                        if has_tts and track.track_type == TrackType.MUSIC:
                            effective_volume *= 0.3  # Duck music under TTS
                        
                        if effective_volume != 1.0:
                            frame = self._apply_volume(frame, effective_volume)
                        
                        frames.append(frame)
                
                except Exception as e:
                    # Log error and remove problematic track
                    print(f"Error reading track: {e}")
                    tracks_to_remove.append(track)
            
            # Remove finished/errored tracks and trigger callbacks
            for track in tracks_to_remove:
                self._tracks.remove(track)
                if track.on_done:
                    # Queue callback for async execution
                    asyncio.run_coroutine_threadsafe(
                        self._execute_callback(track.on_done),
                        self._loop
                    )
                # Cleanup the source
                if hasattr(track.source, 'cleanup'):
                    track.source.cleanup()
            
            # Mix frames in PCM domain
            if frames:
                mixed_pcm = self._mix_frames(frames)
                
                # Encode to Opus if configured
                if self._use_opus and self._encoder:
                    try:
                        # Encode PCM to Opus (960 samples = 20ms @ 48kHz)
                        opus_data = self._encoder.encode(mixed_pcm, 960)
                        return opus_data
                    except Exception as e:
                        print(f"Opus encoding failed: {e}")
                        # Fallback to PCM
                        return mixed_pcm
                else:
                    # Return PCM, let discord.py handle encoding
                    return mixed_pcm
            else:
                # Return silence
                if self._use_opus and self._encoder:
                    return self._silence_opus
                else:
                    return self._silence_pcm
    
    def _apply_volume(self, frame: bytes, volume: float) -> bytes:
        """Apply volume scaling to a PCM frame."""
        arr = np.frombuffer(frame, dtype=np.int16)
        arr = (arr * volume).astype(np.int16)
        return arr.tobytes()
    
    def _mix_frames(self, frames: List[bytes]) -> bytes:
        """Mix multiple PCM frames together with overflow protection."""
        if len(frames) == 1:
            return frames[0]
        
        # Convert to numpy arrays
        arrays = [np.frombuffer(f, dtype=np.int16) for f in frames]
        
        # Mix with overflow protection
        mixed = np.zeros(len(arrays[0]), dtype=np.int32)
        for arr in arrays:
            mixed += arr
        
        # Apply soft clipping for better sound quality
        # This prevents harsh distortion when multiple loud tracks play
        max_val = 32767
        over_limit = np.abs(mixed) > max_val
        if np.any(over_limit):
            # Soft clip using tanh (smoother than hard clipping)
            scale = max_val / np.max(np.abs(mixed))
            mixed = mixed * scale
        
        # Convert back to int16
        mixed = mixed.astype(np.int16)
        return mixed.tobytes()
    
    async def _execute_callback(self, callback: Callable):
        """Execute callback in async context."""
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback()
            else:
                callback()
        except Exception as e:
            print(f"Error in track callback: {e}")

    def is_opus(self) -> bool:
        """Return True if we output Opus packets, False for PCM."""
        return self._use_opus and self._encoder is not None

    def cleanup(self) -> None:
        """Cleanup all underlying sources and encoder."""
        with self._lock:
            for track in self._tracks:
                if hasattr(track.source, 'cleanup'):
                    track.source.cleanup()
            self._tracks.clear()
            
            # Cleanup Opus encoder
            if self._encoder:
                # discord.py's Encoder doesn't have explicit cleanup
                self._encoder = None
```

### 2. GuildAudioBus with Opus Configuration

The bus manager can decide whether to use Opus encoding based on various factors:

```python
class GuildAudioBus:
    def __init__(self, opus_mode: str = 'auto'):
        """
        Initialize audio bus.
        
        Args:
            opus_mode: 'always' (force Opus), 'never' (force PCM), or 'auto'
        """
        self.voice_client: Optional[discord.VoiceClient] = None
        self.mixer: Optional[MixedAudioSource] = None
        self._playing = False
        self._lock = asyncio.Lock()
        self._opus_mode = opus_mode

    async def ensure_connected(
        self, interaction: discord.Interaction
    ) -> discord.VoiceClient:
        """Ensure voice connection is established."""
        from src.voice import connect_to_user_channel, ensure_opus
        
        if not self.voice_client or not self.voice_client.is_connected():
            self.voice_client = await connect_to_user_channel(interaction)
        
        return self.voice_client

    async def add_track(
        self,
        interaction: discord.Interaction,
        source: discord.AudioSource,
        *,
        volume: float = 1.0,
        track_type: TrackType = TrackType.MUSIC,
        on_done: Optional[Callable[[], None]] = None,
    ) -> None:
        """Add a track to the mixer and start playback if needed."""
        async with self._lock:
            # Ensure connected
            voice_client = await self.ensure_connected(interaction)
            
            # Initialize mixer if needed
            if not self.mixer:
                # Decide whether to use Opus encoding
                use_opus = self._should_use_opus(voice_client)
                
                # Create mixer with appropriate configuration
                self.mixer = MixedAudioSource(
                    loop=interaction.client.loop,
                    use_opus=use_opus,
                    opus_kbps=192 if track_type == TrackType.MUSIC else 96
                )
            
            # Add track to mixer
            self.mixer.add_track(source, volume, track_type, on_done)
            
            # Start playback if not already playing
            if not self._playing:
                voice_client.play(self.mixer)
                self._playing = True
    
    def _should_use_opus(self, voice_client: discord.VoiceClient) -> bool:
        """Determine whether to use Opus encoding."""
        if self._opus_mode == 'always':
            return True
        elif self._opus_mode == 'never':
            return False
        else:  # auto mode
            # Use Opus if it's available and loaded
            try:
                return discord.opus.is_loaded()
            except:
                return False
    
    async def stop(self):
        """Stop playback and cleanup."""
        async with self._lock:
            if self.voice_client and self.voice_client.is_playing():
                self.voice_client.stop()
            
            if self.mixer:
                self.mixer.cleanup()
                self.mixer = None
            
            self._playing = False
            
            if self.voice_client and self.voice_client.is_connected():
                await self.voice_client.disconnect()
                self.voice_client = None
```

---

## Opus vs PCM Trade-offs

### When to Use PCM Mode (is_opus=False)
- **Simpler implementation** - Let discord.py handle encoding
- **Better compatibility** - Works even if Opus library issues
- **Easier debugging** - Can inspect raw PCM data
- **Use when:**
  - Starting initial implementation
  - Opus library not available
  - Need maximum compatibility

### When to Use Opus Mode (is_opus=True)
- **3-10x bandwidth reduction** - Opus is very efficient
- **Better for poor connections** - FEC helps with packet loss
- **Dynamic optimization** - Can adjust for music vs voice
- **Use when:**
  - Production deployment
  - Many concurrent voice connections
  - Users on limited bandwidth
  - Need best audio quality

### Bandwidth Comparison
| Mode | Bandwidth | Use Case |
|------|-----------|----------|
| PCM | ~187.5 KB/s | Development/testing |
| Opus @ 64kbps | ~8 KB/s | Voice/TTS only |
| Opus @ 96kbps | ~12 KB/s | Mixed voice + music |
| Opus @ 192kbps | ~24 KB/s | High quality music |

---

## Integration Examples

### Playing Music with Opus

```python
from src.audio_bus import get_guild_bus, TrackType

async def play_regular_audio(self, interaction: discord.Interaction):
    stream_url = await ...  # yt_dlp extraction
    bus = get_guild_bus(interaction.guild.id)

    # FFmpegPCMAudio outputs PCM, mixer will encode to Opus
    audio_source = discord.FFmpegPCMAudio(
        stream_url,
        executable=ffmpeg_executable(),
        **self.ffmpeg_options,
    )

    async def on_song_done():
        await self.song_finished(interaction)

    await bus.add_track(
        interaction, 
        audio_source, 
        volume=1.0,
        track_type=TrackType.MUSIC,
        on_done=on_song_done
    )
```

### Playing TTS with Lower Bitrate

```python
async def play_tts_via_mixer(
    interaction: discord.Interaction,
    audio_file: Path,
    *,
    volume: float = 1.0,
) -> None:
    bus = get_guild_bus(interaction.guild.id)
    
    # TTS doesn't need high bitrate
    source = discord.FFmpegPCMAudio(str(audio_file), executable=ffmpeg_executable())

    def on_done():
        try:
            os.remove(audio_file)
        except OSError:
            logger.warning(f"Failed to remove TTS file {audio_file}")

    await bus.add_track(
        interaction, 
        source, 
        volume=volume,
        track_type=TrackType.TTS,  # Mixer can use lower bitrate for TTS
        on_done=on_done
    )
```

---

## Migration Plan

### Phase 1: PCM Mixing (Simpler)
1. Implement MixedAudioSource with `is_opus() = False`
2. Test mixing functionality
3. Deploy with PCM output (discord.py handles Opus encoding)

### Phase 2: Opus Optimization (Optional)
1. Add Opus encoder support to MixedAudioSource
2. Test with `use_opus=True` in development
3. Monitor CPU and bandwidth usage
4. Roll out Opus mode gradually

### Testing Strategy

```python
import pytest

@pytest.mark.asyncio
async def test_pcm_mode():
    """Test mixer in PCM mode."""
    mixer = MixedAudioSource(use_opus=False)
    assert mixer.is_opus() == False
    
    # Should return PCM data
    frame = mixer.read()
    assert len(frame) == 3840

@pytest.mark.asyncio
async def test_opus_mode():
    """Test mixer in Opus mode."""
    # Skip if Opus not available
    if not discord.opus.is_loaded():
        pytest.skip("Opus not available")
    
    mixer = MixedAudioSource(use_opus=True)
    assert mixer.is_opus() == True
    
    # Should return compressed Opus data
    frame = mixer.read()
    assert len(frame) < 3840  # Opus is compressed

@pytest.mark.asyncio
async def test_opus_fallback():
    """Test fallback to PCM if Opus fails."""
    # Create mixer with invalid Opus settings
    mixer = MixedAudioSource(use_opus=True)
    
    # Even if Opus requested, should fallback gracefully
    frame = mixer.read()
    assert frame is not None
```

---

## Performance Considerations

### CPU Usage
- **PCM mode**: Mixing only, discord.py does encoding (~5% CPU per connection)
- **Opus mode**: Mixing + encoding (~7% CPU per connection)
- Monitor CPU usage and adjust based on server capacity

### Memory Usage
- Opus encoder adds ~100KB per guild
- Consider limiting concurrent connections if memory-constrained

### Network Usage
- PCM mode: Higher bandwidth but handled by discord.py
- Opus mode: Significantly lower bandwidth (3-10x reduction)

---

## Configuration

```python
# Environment variables
AUDIO_MIXER_MODE = "auto"  # "pcm", "opus", or "auto"
AUDIO_OPUS_BITRATE = 128   # kbps for Opus encoding
AUDIO_OPUS_FEC = "true"     # Enable forward error correction
AUDIO_MAX_TRACKS = 10       # Max concurrent tracks per guild
```

---

## Benefits

- **Multiple concurrent TTS**: Any number of `/tts` invocations can overlap
- **Music + TTS**: Music continues with auto-ducking
- **Bandwidth efficient**: Opus reduces bandwidth 3-10x
- **Flexible**: Can run in PCM or Opus mode based on needs
- **Resilient**: FEC in Opus mode handles packet loss
- **Single Discord stream**: Discord requirements satisfied

---

## Summary

The updated design provides flexibility to use either:

1. **PCM mode (simpler)**: Return PCM, let discord.py encode to Opus
2. **Opus mode (optimal)**: Encode to Opus ourselves for better control

Start with PCM mode for simplicity, then optimize with Opus mode if needed. Both approaches are production-ready and the architecture supports switching between them dynamically.
