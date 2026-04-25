import asyncio
import audioop
import os
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import discord
from openai import OpenAI

from src import log, responses
from src.audio_bus import get_guild_bus

logger = log.setup_logger(__name__)

try:
    from discord.ext import voice_recv  # type: ignore[import]
except ImportError:  # pragma: no cover - optional dependency
    voice_recv = None  # type: ignore[assignment]

stt_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-1")

_stt_backend_env = os.getenv("LISTEN_STT_BACKEND", "").strip().lower()
if _stt_backend_env in ("whisperx_http", "whisperx-http", "whisperx"):
    STT_BACKEND = "whisperx_http"
elif _stt_backend_env in ("openai", "whisper", "api"):
    STT_BACKEND = "openai"
else:
    STT_BACKEND = "openai"

# VAD / segmentation parameters (seconds and amplitude)
# Optimized for natural speech with brief pauses
SILENCE_WINDOW = 0.8  # Cut after 800ms of silence (balance between speed and completeness)
BRIEF_PAUSE_WINDOW = 0.3  # Pauses shorter than this are kept within utterance
MIN_UTTERANCE_SECONDS = 0.3  # Accept short words
MAX_UTTERANCE_SECONDS = 12.0  # Maximum utterance length
PCM_SAMPLE_RATE = 48000
PCM_CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit
# Threshold tuned to detect speech while filtering background noise
# Higher value = less sensitive, reduces false positives
VAD_RMS_THRESHOLD = 400

# Name variants that should count as addressing the bot.
TRIGGER_NAMES = ("bonk", "balk", "bulk", "hulk", "bong", "bank", "bunk", "balks")

# Common Whisper hallucinations to ignore (works for both Local WhisperX and OpenAI)
HALLUCINATIONS = ("thank you", "thanks for watching", "thank you for watching", "thanks")

WHISPERX_HTTP_URL = os.getenv("WHISPERX_HTTP_URL", "http://127.0.0.1:5001/transcribe")


@dataclass
class Utterance:
    user_id: int
    text: str
    started_at: float
    ended_at: float


class ListenSession:
    """Per-guild voice listening state."""

    def __init__(
        self,
        guild_id: int,
        voice_client: discord.VoiceClient,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.guild_id = guild_id
        self.channel_id = voice_client.channel.id if voice_client.channel else 0
        self.voice_client = voice_client
        self.loop = loop
        self.active: bool = False
        self.user_streams: Dict[int, UserStream] = {}
        self.last_utterances: List[Utterance] = []
        self.sink: Optional["TranscriptionSink"] = None
        self._flush_task: Optional[asyncio.Task] = None
        self.expecting_reply_until: float = 0.0

    def on_frame(self, user: Optional[discord.abc.User], pcm: bytes) -> None:
        """Receive one PCM frame for a specific user."""
        if not user:
            return

        # Ignore the bot's own audio to prevent feedback loops
        if user.id == self.voice_client.client.user.id:
            return

        # Ignore audio if the bot is currently speaking
        bus = get_guild_bus(self.guild_id)
        if bus.mixer.has_active_tracks():
            return

        user_id = user.id
        stream = self.user_streams.get(user_id)
        if stream is None:
            stream = UserStream(user_id=user_id, session=self)
            self.user_streams[user_id] = stream
        try:
            stream.add_audio(pcm)
        except Exception:
            logger.exception("Error processing audio frame for user %s", user_id)

    def enqueue_utterance(
        self, user_id: int, pcm: bytes, started_at: float, ended_at: float
    ) -> None:
        """Schedule processing of a completed utterance."""

        async def _process() -> None:
            await self._handle_utterance(user_id, pcm, started_at, ended_at)

        try:
            asyncio.run_coroutine_threadsafe(_process(), self.loop)
        except Exception:
            logger.exception("Failed to schedule utterance processing")

    async def _watchdog_task(self) -> None:
        """Monitor the voice receiver for crashes and restart if needed."""
        logger.info("Starting watchdog task for guild %s", self.guild_id)
        while self.active:
            try:
                await asyncio.sleep(1.0)
                # Check if the sink is still alive and functioning.
                # If discord-ext-voice-recv crashes, the reader loop usually dies.
                # We can check if the voice client is connected.
                if not self.voice_client.is_connected():
                    logger.warning("Watchdog: Voice client disconnected, stopping listener.")
                    self.active = False
                    break

                # If we have a sink, we might want to check if it's still attached?
                # Unfortunately, the extension doesn't expose a simple "is_running" on the sink easily
                # without poking internal state.
                # However, the user reported a crash in the packet router loop.
                # If that loop dies, we stop getting audio.

                # We can try to detect if the voice client's reader is dead if we knew how to access it.
                # For now, let's rely on a simpler check: if we catch the specific OpusError elsewhere,
                # we trigger a re-initialization.

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in watchdog task")

    async def _periodic_flush_check(self) -> None:
        """Background task to check for pending flushes even when no frames arrive."""
        try:
            while self.active:
                await asyncio.sleep(0.1)  # Check every 100ms
                now = time.time()

                # Check all user streams for pending flushes
                for stream in list(self.user_streams.values()):
                    if not stream.active or stream.last_voice_at is None:
                        continue

                    silence = now - stream.last_voice_at
                    if silence >= SILENCE_WINDOW:
                        # Trigger flush from outside add_audio callback
                        try:
                            stream.check_and_flush(now)
                        except Exception:
                            logger.exception("Error in periodic flush for user %s", stream.user_id)
        except asyncio.CancelledError:
            logger.debug("Periodic flush task cancelled for guild %s", self.guild_id)
        except Exception:
            logger.exception("Error in periodic flush task for guild %s", self.guild_id)

    def start_flush_task(self) -> None:
        """Start the background flush checking task."""
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._periodic_flush_check())

    def stop_flush_task(self) -> None:
        """Stop the background flush checking task."""
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()

    async def restart_listening(self) -> None:
        """Restart the listening process to recover from Opus errors."""
        logger.warning("Restarting voice listening due to error...")

        # Stop current listening
        self.active = False
        self.stop_flush_task()

        if self.sink:
            try:
                self.sink.cleanup()
            except Exception:
                pass
            self.sink = None

        try:
            stop_method = getattr(self.voice_client, "stop_listening", None)
            if callable(stop_method):
                stop_method()
        except Exception:
            pass

        await asyncio.sleep(0.5)  # Give it a moment to settle

        # Restart
        if not self.voice_client.is_connected():
            logger.warning("Cannot restart listening: voice client disconnected")
            return

        try:
            sink = TranscriptionSink(self)
            self.sink = sink
            self.active = True
            self.start_flush_task()

            listen_method = getattr(self.voice_client, "listen", None)
            if callable(listen_method):
                listen_method(sink)
                logger.info("Successfully restarted voice listening")
            else:
                logger.error("Failed to restart: listen method not found")
        except Exception:
            logger.exception("Failed to restart voice listening")

    async def _handle_utterance(
        self,
        user_id: int,
        pcm: bytes,
        started_at: float,
        ended_at: float,
    ) -> None:
        """Transcribe an utterance, decide whether to respond, and speak if needed."""
        duration = len(pcm) / (PCM_SAMPLE_RATE * SAMPLE_WIDTH * PCM_CHANNELS)
        if duration < MIN_UTTERANCE_SECONDS:
            return

        transcript = await transcribe_audio(pcm)
        if not transcript:
            return

        transcript = transcript.strip()
        if not transcript:
            return

        # Filter out common Whisper hallucinations
        # This catches "Thank you" artifacts from the local model too
        transcript_lower = transcript.lower().strip(" .!")
        if transcript_lower in HALLUCINATIONS:
            logger.info("Ignoring hallucinated transcript: '%s'", transcript)
            return

        # Resolve username
        username = "User"
        user = self.voice_client.guild.get_member(user_id)
        if user:
            username = user.display_name

        # Prepend username to transcript
        full_transcript = f"{username}: {transcript}"
        logger.info("Heard from user %s (%s): %s", user_id, username, transcript)

        # Update simple utterance history (bounded)
        self.last_utterances.append(
            Utterance(
                user_id=user_id, text=full_transcript, started_at=started_at, ended_at=ended_at
            )
        )
        if len(self.last_utterances) > 50:
            self.last_utterances.pop(0)

        now = time.time()
        expecting_reply = now < self.expecting_reply_until

        if not should_respond(
            transcript, len(self.last_utterances), expecting_reply=expecting_reply
        ):
            # Even if we don't respond, add to shared history so Bonk remembers what was said.
            # This creates a continuous shared context window for all users.
            responses.voice_message_history.add_message('voice_shared', "user", full_transcript)
            return

        # If we were expecting a reply, clear the flag since we're handling it now
        if expecting_reply:
            self.expecting_reply_until = 0.0

        # Use existing chat pipeline to generate a reply.
        # Use voice_mode=True for shorter, more conversational responses
        try:
            reply = await responses.handle_response(
                full_transcript, user_id=user_id, voice_mode=True, extra_context=None
            )
        except Exception:
            logger.exception("Error generating voice reply")
            return

        if not reply or not reply.strip():
            return

        # If the bot asked a question, listen for a response for a short window
        if reply.strip().endswith("?"):
            self.expecting_reply_until = time.time() + 10.0
            logger.info("Bot asked a question, expecting reply for 10s")

        # Generate TTS using the existing helper and play via the mixer.
        try:
            from src.commands.tts import generate_speech  # local import to avoid cycles

            audio_path = await generate_speech(reply, "alloy")
        except Exception:
            logger.exception("Failed to generate TTS for voice reply")
            return

        try:
            bus = get_guild_bus(self.guild_id)
            bus.attach_voice_client(self.voice_client)

            audio_source = discord.FFmpegPCMAudio(str(audio_path))

            def on_done(error: Optional[BaseException] = None) -> None:
                if error:
                    logger.error("Error during voice reply playback: %s", error)
                try:
                    Path(audio_path).unlink(missing_ok=True)
                except Exception:
                    logger.exception("Error deleting voice reply file")

            bus.add_track(audio_source, volume=1.0, on_done=on_done)
        except Exception:
            logger.exception("Failed to enqueue voice reply for playback")


class UserStream:
    """Buffer and segment PCM for a single user."""

    def __init__(self, user_id: int, session: ListenSession) -> None:
        self.user_id = user_id
        self.session = session
        self.buffer = bytearray()
        self.first_frame_at: Optional[float] = None
        self.last_voice_at: Optional[float] = None
        self.active: bool = False

    def add_audio(self, pcm: bytes) -> None:
        # Discord sends stereo (2 channels) audio by default (3840 bytes for 20ms).
        # We need Mono for the wave file and Whisper.
        # If we don't downmix, 2ch data read as 1ch plays at 0.5x speed (demon voice).
        if len(pcm) == 3840:
            pcm = audioop.tomono(pcm, SAMPLE_WIDTH, 0.5, 0.5)
        elif len(pcm) > 3840 and len(pcm) % 4 == 0:
            # Fallback for non-standard frame sizes that look like stereo (align 4 bytes)
            # Heuristic: If it's stereo, downmixing is safe.
            # If it was somehow mono, this would speed it up 2x (chipmunk).
            # Given Discord defaults, Stereo is the overwhelming likelihood.
            try:
                pcm = audioop.tomono(pcm, SAMPLE_WIDTH, 0.5, 0.5)
            except Exception:
                pass

        now = time.time()

        # Simple VAD: use RMS to detect "voice-like" energy.
        try:
            rms = audioop.rms(pcm, SAMPLE_WIDTH)
        except Exception:
            rms = 0

        is_voice = rms >= VAD_RMS_THRESHOLD

        # If we're starting a new utterance and have a stale buffer, flush it first
        if is_voice and not self.active and self.buffer:
            # There's old audio from a previous utterance - flush it before starting new one
            if self.first_frame_at is not None:
                self._flush(now)

        if self.first_frame_at is None:
            self.first_frame_at = now

        if is_voice:
            self.active = True
            self.last_voice_at = now

        # Always buffer; we'll cut on silence window.
        self.buffer.extend(pcm)

        if not self.active:
            # Not yet inside an utterance, keep buffering until we detect voice.
            self._trim_if_too_long(now)
            return

        # Check for utterance end: long silence or max duration exceeded.
        if self.last_voice_at is not None:
            silence = now - self.last_voice_at
        else:
            silence = 0.0

        total_duration = now - (self.first_frame_at or now)

        if silence >= SILENCE_WINDOW or total_duration >= MAX_UTTERANCE_SECONDS:
            self._flush(now)

    def _trim_if_too_long(self, now: float) -> None:
        """Prevent unbounded growth if no clear utterance is detected."""
        if self.first_frame_at is None:
            return
        total_duration = now - self.first_frame_at
        if total_duration > MAX_UTTERANCE_SECONDS:
            # Drop buffered data if we've been idle for too long.
            self.buffer.clear()
            self.first_frame_at = None
            self.last_voice_at = None
            self.active = False

    def _flush(self, ended_at: float) -> None:
        if not self.buffer or self.first_frame_at is None:
            # Nothing useful to flush.
            self.buffer.clear()
            self.first_frame_at = None
            self.last_voice_at = None
            self.active = False
            return

        pcm = bytes(self.buffer)

        # Trim leading silence for better transcription quality
        # Find first frame with significant energy
        frame_size = PCM_SAMPLE_RATE * SAMPLE_WIDTH * PCM_CHANNELS // 50  # 20ms frames
        trimmed_start = 0
        for i in range(0, len(pcm) - frame_size, frame_size):
            frame = pcm[i : i + frame_size]
            try:
                rms = audioop.rms(frame, SAMPLE_WIDTH)
                if rms >= VAD_RMS_THRESHOLD:
                    trimmed_start = i
                    break
            except Exception:
                break

        if trimmed_start > 0:
            pcm = pcm[trimmed_start:]
            logger.debug(
                f"Trimmed {trimmed_start / (PCM_SAMPLE_RATE * SAMPLE_WIDTH * PCM_CHANNELS):.2f}s of leading silence"
            )

        # Normalize audio to boost quiet input (Discord sends quiet audio)
        # Target RMS around 3000-5000 for good Whisper recognition
        try:
            current_rms = audioop.rms(pcm, SAMPLE_WIDTH)
            if current_rms > 0:
                target_rms = 4000  # Good level for speech
                gain = min(target_rms / current_rms, 8.0)  # Cap at 8x gain (less aggressive)
                if gain > 1.5:  # Only boost if needed
                    pcm = audioop.mul(pcm, SAMPLE_WIDTH, gain)
                    final_rms = audioop.rms(pcm, SAMPLE_WIDTH)
                    logger.debug(
                        f"Audio quality: RMS {current_rms} -> {final_rms} (gain: {gain:.1f}x)"
                    )
            else:
                logger.warning("Audio has zero RMS - likely silent or corrupted")
        except Exception:
            logger.debug("Failed to normalize audio", exc_info=True)

        duration = len(pcm) / (PCM_SAMPLE_RATE * SAMPLE_WIDTH * PCM_CHANNELS)
        logger.debug(f"Flushing {duration:.2f}s audio for user {self.user_id}")

        started_at = self.first_frame_at

        # Reset state before handing off to the session.
        self.buffer.clear()
        self.first_frame_at = None
        self.last_voice_at = None
        self.active = False

        self.session.enqueue_utterance(self.user_id, pcm, started_at, ended_at)

    def check_and_flush(self, now: float) -> None:
        """Check if this stream should be flushed based on current time."""
        if not self.active or self.last_voice_at is None:
            return

        silence = now - self.last_voice_at
        if silence >= SILENCE_WINDOW:
            self._flush(now)

    def flush(self) -> None:
        """Force-flush any buffered audio as a final utterance."""
        if self.first_frame_at is None:
            return
        self._flush(time.time())


if voice_recv is not None:
    # Monkey-patch discord.ext.voice_recv.opus.VoiceDecoder.decode to handle OpusErrors gracefully
    # This is necessary because the library's internal loop crashes on these errors before they reach our sink.
    #
    # Post Discord-DAVE-enforcement (2026-03-02), every incoming frame in a non-stage voice
    # channel arrives MLS-encrypted at the Opus layer because discord-ext-voice-recv hasn't
    # implemented DAVE receive-side decryption yet (upstream issue #53). Every frame raises
    # OpusError("corrupted stream"), which floods the log. Rate-limit to one summary line
    # per 30 seconds so the log stays useful.
    from discord.ext.voice_recv import opus as _recv_opus  # type: ignore

    _original_decode = _recv_opus.Decoder.decode
    _OPUS_ERROR_LOG_INTERVAL = 30.0
    _opus_error_state = {"count": 0, "last_logged": 0.0, "last_error": ""}

    def _safe_decode(self, *args, **kwargs):
        try:
            return _original_decode(self, *args, **kwargs)
        except discord.opus.OpusError as e:
            now = time.time()
            _opus_error_state["count"] += 1
            _opus_error_state["last_error"] = str(e)
            if now - _opus_error_state["last_logged"] >= _OPUS_ERROR_LOG_INTERVAL:
                logger.warning(
                    "OpusError suppressed %d frame(s) in last %.0fs (last: %s) — "
                    "likely DAVE-encrypted audio that voice_recv can't decrypt yet "
                    "(see imayhaveborkedit/discord-ext-voice-recv#53)",
                    _opus_error_state["count"],
                    _OPUS_ERROR_LOG_INTERVAL,
                    _opus_error_state["last_error"],
                )
                _opus_error_state["count"] = 0
                _opus_error_state["last_logged"] = now
            # Return silent PCM frame (20ms of silence at 48kHz stereo 16-bit = 3840 bytes)
            return b'\x00' * 3840

    _recv_opus.Decoder.decode = _safe_decode
    logger.info("Monkey-patched discord.ext.voice_recv.opus.Decoder.decode for safety")

    class TranscriptionSink(voice_recv.AudioSink):  # type: ignore[misc]
        """Audio sink that feeds per-user PCM into a ListenSession."""

        def __init__(self, session: ListenSession) -> None:
            super().__init__()
            self.session = session

        def wants_opus(self) -> bool:
            # Ask the extension to decode to PCM for us.
            return False

        def write(self, user: Optional[discord.Member], data: "voice_recv.VoiceData") -> None:  # type: ignore[name-defined]
            try:
                self.session.on_frame(user, data.pcm)  # type: ignore[attr-defined]
            except Exception:
                logger.exception("Error in TranscriptionSink.write")

        def cleanup(self) -> None:
            # Flush any remaining audio buffers.
            for stream in list(self.session.user_streams.values()):
                try:
                    stream.flush()
                except Exception:
                    logger.exception("Error flushing UserStream during cleanup")
            self.session.user_streams.clear()

else:

    class TranscriptionSink:  # type: ignore[no-redef]
        """Placeholder sink when discord-ext-voice-recv is not installed."""

        def __init__(self, session: ListenSession) -> None:
            self.session = session

        def wants_opus(self) -> bool:
            return False

        def write(self, user: Optional[discord.Member], data: object) -> None:
            raise RuntimeError("discord-ext-voice-recv is not installed")

        def cleanup(self) -> None:
            self.session.user_streams.clear()


listen_sessions: Dict[int, ListenSession] = {}


def get_listen_session(guild_id: int) -> Optional[ListenSession]:
    return listen_sessions.get(guild_id)


async def handle_listen(interaction: discord.Interaction, enable: bool = True) -> None:
    """Toggle voice listening mode for the guild."""
    if not interaction.guild:
        await interaction.followup.send("Listening is only available in guilds.", ephemeral=True)
        return

    if voice_recv is None:
        await interaction.followup.send(
            "Voice listening is not available: install 'discord-ext-voice-recv' to enable /listen.",
            ephemeral=True,
        )
        return

    if not interaction.user or not getattr(interaction.user, "voice", None):
        await interaction.followup.send(
            "You need to be in a voice channel to use /listen.", ephemeral=True
        )
        return

    from src.voice import connect_to_user_channel

    guild_id = interaction.guild.id

    try:
        # For listening we must *not* join self-deafened, otherwise
        # Discord will not send us other users' audio.
        voice_client = await connect_to_user_channel(
            interaction,
            self_deaf=False,
        )
    except Exception as e:
        logger.error("Failed to connect for /listen: %s", e)
        await interaction.followup.send(
            "Couldn't connect to your voice channel for listening.", ephemeral=True
        )
        return

    session = listen_sessions.get(guild_id)
    if session is None:
        session = ListenSession(
            guild_id=guild_id,
            voice_client=voice_client,
            loop=interaction.client.loop,
        )
        listen_sessions[guild_id] = session
    else:
        session.voice_client = voice_client
        session.channel_id = voice_client.channel.id if voice_client.channel else 0
        session.loop = interaction.client.loop

    if enable:
        if session.active:
            await interaction.followup.send(
                f"Already listening in {voice_client.channel.mention}.", ephemeral=True
            )
            return

        sink = TranscriptionSink(session)
        session.sink = sink
        session.active = True

        # Start background flush checking task
        session.start_flush_task()

        logger.info(
            "Listening enabled in guild %s, channel %s using STT backend '%s'",
            guild_id,
            voice_client.channel.id if voice_client.channel else "unknown",
            STT_BACKEND,
        )

        # Attach sink using the extension's API.
        try:
            listen_method = getattr(voice_client, "listen", None)
            if callable(listen_method):
                listen_method(sink)
            else:
                raise RuntimeError("Voice client does not support listening")
        except Exception:
            logger.exception("Failed to start listening on voice client")
            session.active = False
            session.sink = None
            session.stop_flush_task()
            # The voice client is almost certainly a zombie at this point
            # (handshake closed mid-stream). Tear it down so subsequent /listen
            # attempts start clean and the bot leaves the channel server-side.
            from src.voice import _force_cleanup_voice_client

            await _force_cleanup_voice_client(voice_client)
            listen_sessions.pop(guild_id, None)
            await interaction.followup.send(
                "Couldn't start listening — the voice connection dropped. Try again; "
                "if I'm still stuck in the channel, run `/disconnect`.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"🎧 Now listening in {voice_client.channel.mention}. Only you can see this.",
            ephemeral=True,
        )
    else:
        if not session.active:
            await interaction.followup.send("Listening is not currently enabled.", ephemeral=True)
            return

        session.active = False
        session.stop_flush_task()

        logger.info("Listening disabled in guild %s", guild_id)
        # Best-effort stop: call stop_listening if available.
        try:
            stop_method = getattr(session.voice_client, "stop_listening", None)
            if callable(stop_method):
                stop_method()
        except Exception:
            logger.exception("Error stopping voice listening")

        if session.sink is not None:
            try:
                session.sink.cleanup()
            except Exception:
                logger.exception("Error cleaning up TranscriptionSink")
            session.sink = None

        session.user_streams.clear()

        await interaction.followup.send(
            f"Stopped listening in {session.voice_client.channel.mention if session.voice_client.channel else 'this guild'}.",
            ephemeral=True,
        )


def should_respond(transcript: str, utterance_count: int, expecting_reply: bool = False) -> bool:
    """Heuristic for whether Bonk should reply to an utterance."""
    text = transcript.lower()

    # If we asked a question recently, we are expecting a reply.
    if expecting_reply:
        return True

    # Respond if clearly addressed by name (including common mis-hearings)
    # or if it's a direct question.
    if not any(name in text for name in TRIGGER_NAMES) and "?" not in text:
        return False

    # Optionally, avoid over-responding in very busy channels.
    if utterance_count > 100:
        # If lots of recent chatter, be a bit quieter.
        return False

    return True


def _transcribe_with_whisperx_http(path: Path) -> Optional[str]:
    """Transcribe WAV audio using a local whisperx HTTP sidecar."""
    import requests  # imported lazily to keep dependencies light

    try:
        with path.open("rb") as audio_file:
            files = {"audio": audio_file}
            resp = requests.post(WHISPERX_HTTP_URL, files=files, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        text = data.get("text")
        if isinstance(text, str):
            return text
        logger.error("whisperx HTTP sidecar returned invalid payload: %s", data)
        return None
    except Exception as exc:
        logger.error("whisperx HTTP transcription failed: %s", exc)
        return None


def _transcribe_with_openai(path: Path) -> Optional[str]:
    """Transcribe WAV audio using OpenAI Whisper API."""
    with path.open("rb") as audio_file:
        try:
            result = stt_client.audio.transcriptions.create(
                model=WHISPER_MODEL,
                file=audio_file,
                language="en",  # Hint to improve accuracy
                prompt=(
                    "This is an English Discord voice chat. "
                    "The assistant's name is Bonk, sometimes pronounced like 'balk'."
                ),
            )
        except Exception as exc:  # pragma: no cover - network error path
            logger.error("OpenAI Whisper transcription failed: %s", exc)
            return None
    return getattr(result, "text", None)


async def transcribe_audio(pcm: bytes, sample_rate: int = PCM_SAMPLE_RATE) -> Optional[str]:
    """Transcribe raw PCM using the configured STT backend."""
    if not pcm:
        return None

    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            with wave.open(tmp, "wb") as wf:
                wf.setnchannels(PCM_CHANNELS)
                wf.setsampwidth(SAMPLE_WIDTH)
                wf.setframerate(sample_rate)
                wf.writeframes(pcm)

        def _sync_transcribe(path: Path) -> Optional[str]:
            logger.info("Transcribing utterance with backend '%s'", STT_BACKEND)
            try:
                if STT_BACKEND == "whisperx_http":
                    return _transcribe_with_whisperx_http(path)
                return _transcribe_with_openai(path)
            except Exception as exc:
                logger.error("Transcription failed: %s", exc)
                return None

        loop = asyncio.get_running_loop()
        text: Optional[str] = await loop.run_in_executor(None, _sync_transcribe, tmp_path)
        return text
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                logger.debug("Failed to remove temp audio file", exc_info=True)
