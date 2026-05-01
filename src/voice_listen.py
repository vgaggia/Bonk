import asyncio
import audioop
import concurrent.futures
import os
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional, Set

import discord
from openai import OpenAI

from src import log, responses
from src.audio_bus import get_guild_bus
from src.voice_memory import (
    EXTRACTION_TRANSCRIPT_LINES,
    MEMORY_EXTRACTION_MODEL,
    ExtractionWorker,
    voice_memory_store,
)

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


def _log_task_exception(task: asyncio.Task) -> None:
    """Done-callback that surfaces unhandled exceptions in fire-and-forget tasks."""
    if task.cancelled():
        return
    try:
        exc = task.exception()
    except (asyncio.CancelledError, asyncio.InvalidStateError):
        return
    if exc is not None:
        logger.error("Background voice task failed", exc_info=exc)


def _strip_name_prefix(text: str, name: str) -> str:
    """Remove a leading 'Name: ' prefix if it matches the given name."""
    prefix = f"{name}: "
    if text.startswith(prefix):
        return text[len(prefix):]
    return text


LISTEN_BACKEND_OPENAI = "openai"
LISTEN_BACKEND_ELEVENLABS = "elevenlabs"
OPENAI_TTS_VOICES = ("alloy", "echo", "fable", "onyx", "nova", "shimmer")
DEFAULT_OPENAI_TTS_VOICE = "alloy"


@dataclass
class ListenVoiceConfig:
    """TTS backend + voice used by /listen replies in a single guild."""

    backend: str = LISTEN_BACKEND_OPENAI
    openai_voice: str = DEFAULT_OPENAI_TTS_VOICE
    eleven_voice_id: str = ""
    eleven_model_id: str = ""


listen_voice_configs: Dict[int, ListenVoiceConfig] = {}


def get_listen_voice_config(guild_id: int) -> ListenVoiceConfig:
    """Return (and lazily create) the per-guild listen voice config."""
    cfg = listen_voice_configs.get(guild_id)
    if cfg is None:
        from src.tts import eleven  # local import: keeps bot startup light

        cfg = ListenVoiceConfig(
            backend=LISTEN_BACKEND_OPENAI,
            openai_voice=DEFAULT_OPENAI_TTS_VOICE,
            eleven_voice_id=eleven.default_voice_id(),
            eleven_model_id=eleven.default_model_id(),
        )
        listen_voice_configs[guild_id] = cfg
    return cfg


@dataclass
class BatchedUtterance:
    """One transcribed utterance handed to the ReplyScheduler.

    Snapshots `expecting_reply` and `passes_should_respond` at handoff time
    rather than re-evaluating later, so a flag flipped during the merge
    window doesn't change the batched decision retroactively.
    """

    user_id: int
    display_name: str
    transcript: str
    full_transcript: str
    arrived_at: float
    started_at: float
    ended_at: float
    expecting_reply: bool
    passes_should_respond: bool


class ReplyScheduler:
    """Per-guild coalescing scheduler for /listen replies.

    Replaces the old "every utterance fires its own reply" pattern with a
    batched one-reply-at-a-time pipeline:

    - Single drainer task, so only one batch is ever in flight.
    - Utterances arriving while a reply is being produced queue up and are
      delivered together in the next batch.
    - Single-speaker case adds only ~MERGE_SETTLE seconds of latency; the
      multi-speaker case waits up to MERGE_DEADLINE_SHORT (1.5s) for a
      sibling utterance to land, with a hard cap at MERGE_DEADLINE_HARD (3s).
    """

    MERGE_SETTLE = 0.20
    MERGE_DEADLINE_SHORT = 1.50
    MERGE_DEADLINE_HARD = 3.00
    POLL_INTERVAL = 0.10

    def __init__(
        self,
        session: "ListenSession",
        produce_reply: Optional[Callable[[List[BatchedUtterance]], Awaitable[None]]] = None,
        probe_user_streams: Optional[Callable[[], Set[int]]] = None,
    ) -> None:
        self.session = session
        self.pending: List[BatchedUtterance] = []
        self.in_flight: bool = False
        self._kick = asyncio.Event()
        self._loop_task: Optional[asyncio.Task] = None
        self._stopping: bool = False
        self._produce_reply = produce_reply or self._default_produce_reply
        self._probe_user_streams = probe_user_streams or self._default_probe_user_streams

    def _default_probe_user_streams(self) -> Set[int]:
        try:
            return {
                uid for uid, s in self.session.user_streams.items()
                if getattr(s, "active", False)
            }
        except Exception:
            return set()

    def add(self, u: BatchedUtterance) -> None:
        self.pending.append(u)
        self._kick.set()

    def start(self) -> None:
        if self._loop_task is None or self._loop_task.done():
            self._stopping = False
            self._loop_task = asyncio.create_task(self._loop())
            self._loop_task.add_done_callback(_log_task_exception)

    def stop(self) -> None:
        self._stopping = True
        self._kick.set()
        # Don't cancel: let any in-flight _produce_reply finish so we don't
        # leave history half-written. Callers should `await flush()` first
        # for a clean drain.

    async def flush(self, timeout: float = 4.0) -> None:
        """Drain pending utterances and wait for any in-flight reply.

        Bounded by `timeout`. Returns even if some pending utterances were
        not produced (they'll be logged)."""
        deadline = time.time() + timeout
        self._kick.set()
        while time.time() < deadline:
            if not self.pending and not self.in_flight:
                return
            await asyncio.sleep(0.05)
        if self.pending or self.in_flight:
            logger.warning(
                "ReplyScheduler.flush timed out: %d pending, in_flight=%s",
                len(self.pending), self.in_flight,
            )

    async def _loop(self) -> None:
        try:
            while not self._stopping:
                await self._kick.wait()
                self._kick.clear()
                if self._stopping:
                    return
                if not self.pending:
                    continue
                await self._wait_for_quiet()
                if not self.pending:
                    continue
                batch = list(self.pending)
                self.pending.clear()
                self.in_flight = True
                try:
                    await self._produce_reply(batch)
                except Exception:
                    logger.exception("ReplyScheduler._produce_reply failed")
                finally:
                    self.in_flight = False
                    if self.pending:
                        self._kick.set()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("ReplyScheduler._loop crashed")

    async def _wait_for_quiet(self) -> None:
        """Wait until the merge window settles or one of the deadlines fires.

        Exit conditions, in priority order:
        - Hard cap: `elapsed_first >= MERGE_DEADLINE_HARD`. Always exits.
        - Settled fast path: nobody else is mid-utterance AND the most recent
          arrival is at least `MERGE_SETTLE` old. Covers single-speaker.
        - Short cap with no others currently speaking: `elapsed_first
          >= MERGE_DEADLINE_SHORT` and nobody else is mid-utterance. Snaps the
          batch closed in busy multi-speaker scenarios where the settle
          condition keeps being reset.

        While someone *is* mid-utterance and HARD hasn't fired, keep waiting
        — they're about to flush and join this batch.
        """
        while not self._stopping and self.pending:
            now = time.time()
            first = self.pending[0]
            last = self.pending[-1]
            elapsed = now - first.arrived_at
            if elapsed >= self.MERGE_DEADLINE_HARD:
                return
            batched_uids = {u.user_id for u in self.pending}
            try:
                others_speaking = self._probe_user_streams() - batched_uids
            except Exception:
                others_speaking = set()
            if not others_speaking and (now - last.arrived_at) >= self.MERGE_SETTLE:
                return
            if elapsed >= self.MERGE_DEADLINE_SHORT and not others_speaking:
                return
            await asyncio.sleep(self.POLL_INTERVAL)

    async def _default_produce_reply(self, batch: List[BatchedUtterance]) -> None:
        """Build the prompt for a batch and feed the rest of the reply pipeline."""
        any_respondable = any(
            u.passes_should_respond or u.expecting_reply for u in batch
        )
        if not any_respondable:
            # User-side history was already recorded by _handle_utterance.
            return

        present_user_ids, present_names_by_id = self.session._present_voice_users()
        speaker = batch[-1]

        memory_block: Optional[str] = None
        try:
            memory_block = voice_memory_store.format_for_prompt(
                guild_id=self.session.guild_id,
                speaker_user_id=speaker.user_id,
                present_user_ids=present_user_ids,
                speaker_display_name=speaker.display_name,
                present_display_names=present_names_by_id,
            ) or None
        except Exception:
            logger.exception("Failed to render voice memory block for batch")

        if len(batch) == 1:
            message = batch[0].full_transcript
        else:
            lines = [f"{u.display_name}: {u.transcript}" for u in batch]
            message = (
                "Multiple people just spoke. Address each in a single short reply.\n"
                + "\n".join(lines)
            )

        if any(u.expecting_reply for u in batch):
            self.session.expecting_reply_until = 0.0

        try:
            reply = await responses.handle_response(
                message,
                user_id=speaker.user_id,
                voice_mode=True,
                extra_context=None,
                memory_block=memory_block,
                record_history=False,
            )
        except Exception:
            logger.exception("Error generating batched voice reply")
            return

        if not reply or not reply.strip():
            return

        try:
            responses.voice_message_history.add_message('voice_shared', "assistant", reply)
        except Exception:
            logger.exception("Failed to record assistant reply in voice history")

        if reply.strip().endswith("?"):
            self.session.expecting_reply_until = time.time() + 10.0
            logger.info("Bot asked a question, expecting reply for 10s")

        config = get_listen_voice_config(self.session.guild_id)
        try:
            if config.backend == LISTEN_BACKEND_ELEVENLABS:
                from src.tts import eleven

                audio_path = await eleven.synthesize_to_file(
                    text=reply,
                    voice_id=config.eleven_voice_id,
                    model_id=config.eleven_model_id,
                    output_format=eleven.default_output_format(),
                )
            else:
                from src.commands.tts import generate_speech

                audio_path = await generate_speech(reply, config.openai_voice)
        except Exception:
            logger.exception("Failed to generate TTS for batched voice reply")
            return

        try:
            bus = get_guild_bus(self.session.guild_id)
            bus.attach_voice_client(self.session.voice_client)

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
            logger.exception("Failed to enqueue batched voice reply for playback")


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
        # Per-guild memory extraction worker. The worker is a thin wrapper around
        # the module-level voice_memory_store; the store outlives sessions.
        self.memory_worker = ExtractionWorker(
            guild_id=guild_id,
            store=voice_memory_store,
            anthropic_client=responses.anthropic_client,
            model=MEMORY_EXTRACTION_MODEL,
        )
        # Track in-flight utterance-processing futures so /listen disable can
        # wait briefly for transcriptions to land before flushing memory.
        self._processing_futures: Set[concurrent.futures.Future] = set()
        # Per-guild reply scheduler — coalesces near-simultaneous utterances
        # into a single batched reply (so Bonk doesn't talk over itself when
        # two people speak at the same time).
        self.reply_scheduler = ReplyScheduler(self)

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

    def _present_voice_users(self) -> tuple[List[int], Dict[int, str]]:
        """Return (user_ids, {user_id: display_name}) for non-bot members in the channel."""
        ids: List[int] = []
        names: Dict[int, str] = {}
        channel = getattr(self.voice_client, "channel", None)
        bot_user_id = (
            self.voice_client.client.user.id
            if self.voice_client and self.voice_client.client and self.voice_client.client.user
            else None
        )
        if channel is None:
            return ids, names
        try:
            members = list(getattr(channel, "members", []) or [])
        except Exception:
            return ids, names
        for m in members:
            try:
                if m.bot or (bot_user_id is not None and m.id == bot_user_id):
                    continue
                ids.append(m.id)
                names[m.id] = m.display_name or m.name or f"User {m.id}"
            except Exception:
                continue
        return ids, names

    def _bot_user_id(self) -> Optional[int]:
        try:
            user = self.voice_client.client.user
            return user.id if user else None
        except Exception:
            return None

    async def _drain_processing(self, timeout: float = 5.0) -> None:
        """Wait (up to `timeout`) for in-flight utterance-processing tasks to
        finish, so a downstream memory flush sees fully-transcribed history."""
        if not self._processing_futures:
            return
        # Snapshot the current set; new futures arriving after this are not waited on.
        pending = list(self._processing_futures)
        if not pending:
            return
        wrapped = [asyncio.wrap_future(f) for f in pending]
        try:
            await asyncio.wait_for(
                asyncio.gather(*wrapped, return_exceptions=True), timeout=timeout
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Drain timeout: %d transcription task(s) still running on disable",
                sum(1 for w in wrapped if not w.done()),
            )

    def _schedule_extraction(self) -> None:
        """Build the extraction inputs (filtered for opt-out and bot self) and
        kick off a debounced background extraction task. The worker debounces
        internally, so calling this on every utterance is cheap."""
        present_user_ids, present_names_by_id = self._present_voice_users()
        # Drop opted-out users from BOTH the speaker set and the transcript
        # window; their utterances are not used to write any memory anywhere.
        opted_in_ids = [
            uid for uid in present_user_ids
            if not voice_memory_store.is_opted_out(self.guild_id, uid)
        ]
        opted_in_names = {uid: present_names_by_id[uid] for uid in opted_in_ids if uid in present_names_by_id}

        bot_uid = self._bot_user_id()
        user_lines: List[str] = []
        for u in self.last_utterances[-EXTRACTION_TRANSCRIPT_LINES:]:
            if bot_uid is not None and u.user_id == bot_uid:
                continue   # belt-and-suspenders; bot utterances shouldn't be in this list
            if voice_memory_store.is_opted_out(self.guild_id, u.user_id):
                continue
            # Tag each line with the speaker's stable label so the extractor
            # cannot cross-attribute users with identical display names.
            label_name = present_names_by_id.get(u.user_id) or "User"
            user_lines.append(f"[{label_name} (speaker_{u.user_id})] {_strip_name_prefix(u.text, label_name)}")

        if not user_lines or not opted_in_ids:
            return

        task = asyncio.create_task(
            self.memory_worker.maybe_run(
                transcript_user_lines=user_lines,
                present_user_ids=opted_in_ids,
                display_names_by_id=opted_in_names,
            )
        )
        task.add_done_callback(_log_task_exception)

    def enqueue_utterance(
        self, user_id: int, pcm: bytes, started_at: float, ended_at: float
    ) -> None:
        """Schedule processing of a completed utterance."""

        async def _process() -> None:
            await self._handle_utterance(user_id, pcm, started_at, ended_at)

        try:
            future = asyncio.run_coroutine_threadsafe(_process(), self.loop)
        except Exception:
            logger.exception("Failed to schedule utterance processing")
            return

        # Track the future so /listen disable can drain in-flight work.
        self._processing_futures.add(future)
        future.add_done_callback(self._processing_futures.discard)

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

    def start_reply_scheduler(self) -> None:
        """Start the per-guild ReplyScheduler drain loop."""
        self.reply_scheduler.start()

    def stop_reply_scheduler(self) -> None:
        """Signal the ReplyScheduler to stop after its current cycle."""
        self.reply_scheduler.stop()

    async def restart_listening(self) -> None:
        """Restart the listening process to recover from Opus errors."""
        logger.warning("Restarting voice listening due to error...")

        # Stop current listening
        self.active = False
        self.stop_flush_task()
        # Drain and stop the scheduler so it doesn't try to publish to a dead
        # voice client during the transient gap. We re-start it below.
        try:
            await self.reply_scheduler.flush(timeout=2.0)
        except Exception:
            logger.exception("Error flushing reply scheduler during restart")
        self.stop_reply_scheduler()

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
            self.start_reply_scheduler()

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

        # Keep stored display_name in sync so memory blocks always render with
        # the current nickname. Skip for opted-out users — we don't write to
        # their record at all.
        try:
            if not voice_memory_store.is_opted_out(self.guild_id, user_id):
                voice_memory_store.update_display_name(self.guild_id, user_id, username)
        except Exception:
            logger.exception("Failed to update memory display_name")

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

        # Note that we have a new user utterance for the memory worker. Counting
        # all user turns (not just trigger-gated ones) so quiet conversations
        # eventually get extracted too. Skip opted-out speakers entirely so
        # their utterances neither bump the counter nor enter the extraction
        # transcript.
        speaker_opted_out = voice_memory_store.is_opted_out(self.guild_id, user_id)
        if not speaker_opted_out:
            try:
                self.memory_worker.note_new_user_utterance()
            except Exception:
                logger.exception("Failed to record memory pending count")

            # Schedule extraction on EVERY accepted utterance — debounced inside
            # the worker. This is the fix for the bug where quiet conversations
            # (Bonk doesn't reply) never extracted until /listen disable.
            try:
                self._schedule_extraction()
            except Exception:
                logger.exception("Failed to schedule memory extraction")

        # Always record the user side of voice history, regardless of whether
        # this utterance triggers a reply. Single source of truth lives here;
        # the batched reply path passes record_history=False to handle_response.
        responses.voice_message_history.add_message(
            'voice_shared', "user", full_transcript
        )

        now = time.time()
        expecting_reply = now < self.expecting_reply_until
        passes = should_respond(
            transcript, len(self.last_utterances), expecting_reply=expecting_reply
        )

        # Hand the utterance to the per-guild scheduler. It decides whether to
        # merge with concurrent utterances, whether to fire a reply at all
        # (based on the snapshotted flags), and produces a single TTS clip.
        self.reply_scheduler.add(
            BatchedUtterance(
                user_id=user_id,
                display_name=username,
                transcript=transcript,
                full_transcript=full_transcript,
                arrived_at=now,
                started_at=started_at,
                ended_at=ended_at,
                expecting_reply=expecting_reply,
                passes_should_respond=passes,
            )
        )


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

        # Start background flush checking task and reply scheduler.
        session.start_flush_task()
        session.start_reply_scheduler()

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
            session.stop_reply_scheduler()
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

        # Drain in-flight utterance-processing tasks so the memory flush sees
        # a fully-transcribed last_utterances list. Bounded — never wait
        # forever.
        try:
            await session._drain_processing(timeout=5.0)
        except Exception:
            logger.exception("Error draining utterance tasks on /listen disable")

        # Drain the reply scheduler so any pending batched reply lands (or is
        # dropped after a bounded wait). Stop the loop afterwards.
        try:
            await session.reply_scheduler.flush(timeout=4.0)
        except Exception:
            logger.exception("Error flushing reply scheduler on /listen disable")
        session.stop_reply_scheduler()

        # Force a final memory extraction over what was heard, so any pending
        # facts land before the session is torn down.
        try:
            present_ids, present_names_by_id = session._present_voice_users()
            opted_in_ids = [
                uid for uid in present_ids
                if not voice_memory_store.is_opted_out(session.guild_id, uid)
            ]
            opted_in_names = {
                uid: present_names_by_id[uid]
                for uid in opted_in_ids
                if uid in present_names_by_id
            }
            bot_uid = session._bot_user_id()
            user_lines: List[str] = []
            for u in session.last_utterances[-EXTRACTION_TRANSCRIPT_LINES:]:
                if bot_uid is not None and u.user_id == bot_uid:
                    continue
                if voice_memory_store.is_opted_out(session.guild_id, u.user_id):
                    continue
                label_name = present_names_by_id.get(u.user_id) or "User"
                user_lines.append(
                    f"[{label_name} (speaker_{u.user_id})] {_strip_name_prefix(u.text, label_name)}"
                )
            if user_lines and opted_in_ids:
                await session.memory_worker.flush(
                    transcript_user_lines=user_lines,
                    present_user_ids=opted_in_ids,
                    display_names_by_id=opted_in_names,
                )
        except Exception:
            logger.exception("Error flushing memory worker on /listen disable")

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
