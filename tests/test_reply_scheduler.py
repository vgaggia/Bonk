"""Tests for ReplyScheduler — coalesces near-simultaneous /listen utterances
into one batched reply so Bonk doesn't talk over itself.

Tests run on the real asyncio event loop and use stub callables
(`produce_reply` and `probe_user_streams`) so no Discord client or
ListenSession is needed. Constants are monkey-patched down for tests that
exercise the deadline branches, to keep the suite snappy.
"""

import asyncio
import time
from typing import Any, Dict, List
from unittest.mock import MagicMock

from src import responses
from src.voice_listen import BatchedUtterance, ReplyScheduler


def _make_utterance(
    user_id: int = 1,
    name: str = "Alice",
    transcript: str = "hello",
    arrived_at: float | None = None,
    expecting_reply: bool = False,
    passes: bool = True,
) -> BatchedUtterance:
    if arrived_at is None:
        arrived_at = time.time()
    return BatchedUtterance(
        user_id=user_id,
        display_name=name,
        transcript=transcript,
        full_transcript=f"{name}: {transcript}",
        arrived_at=arrived_at,
        started_at=arrived_at,
        ended_at=arrived_at,
        expecting_reply=expecting_reply,
        passes_should_respond=passes,
    )


class _FakeSession:
    """Minimal stand-in for ListenSession sufficient for the scheduler.

    Only the attributes the scheduler actually touches: `user_streams`,
    `guild_id`, `expecting_reply_until`, `voice_client`, and
    `_present_voice_users`."""

    def __init__(self) -> None:
        self.user_streams: Dict[int, Any] = {}
        self.guild_id = 1
        self.expecting_reply_until = 0.0
        self.voice_client = MagicMock()

    def _present_voice_users(self):
        return [], {}


async def _wait_for(predicate, timeout: float = 2.5) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


# ---------------------------------------------------------------------------
# Latency: single speaker should fire after MERGE_SETTLE only
# ---------------------------------------------------------------------------


def test_single_speaker_fires_after_settle_only():
    calls: List[List[BatchedUtterance]] = []

    async def go():
        async def fake_produce(batch):
            calls.append(batch)

        sched = ReplyScheduler(
            session=_FakeSession(),
            produce_reply=fake_produce,
            probe_user_streams=lambda: set(),
        )
        sched.start()
        try:
            t0 = time.time()
            sched.add(_make_utterance(user_id=1, name="Alice"))
            ok = await _wait_for(lambda: len(calls) >= 1, timeout=1.5)
            elapsed = time.time() - t0
            assert ok, "scheduler never fired"
            assert len(calls) == 1
            assert len(calls[0]) == 1
            # Should be roughly MERGE_SETTLE (200ms), with generous slop both ways.
            assert ReplyScheduler.MERGE_SETTLE - 0.10 <= elapsed < 1.2, (
                f"elapsed={elapsed:.3f}s outside expected window"
            )
        finally:
            sched.stop()

    asyncio.run(go())


# ---------------------------------------------------------------------------
# Two speakers within the merge window combine into a single batch
# ---------------------------------------------------------------------------


def test_two_speakers_within_window_merge_into_one_batch():
    calls: List[List[BatchedUtterance]] = []

    async def go():
        async def fake_produce(batch):
            calls.append(batch)

        sched = ReplyScheduler(
            session=_FakeSession(),
            produce_reply=fake_produce,
            probe_user_streams=lambda: set(),
        )
        sched.start()
        try:
            sched.add(_make_utterance(user_id=1, name="Alice"))
            await asyncio.sleep(0.10)  # well within MERGE_SETTLE
            sched.add(_make_utterance(user_id=2, name="Bob"))
            ok = await _wait_for(lambda: len(calls) >= 1, timeout=2.0)
            assert ok, "scheduler never fired"
            assert len(calls) == 1, f"expected 1 batch, got {len(calls)}"
            users = {u.user_id for u in calls[0]}
            assert users == {1, 2}
        finally:
            sched.stop()

    asyncio.run(go())


# ---------------------------------------------------------------------------
# Utterances arriving while a reply is in flight queue for the next cycle
# ---------------------------------------------------------------------------


def test_utterances_during_reply_are_held_for_next_batch():
    calls: List[List[BatchedUtterance]] = []
    release = asyncio.Event()
    first_started = asyncio.Event()

    async def go():
        async def fake_produce(batch):
            calls.append(batch)
            if len(calls) == 1:
                first_started.set()
                await release.wait()

        sched = ReplyScheduler(
            session=_FakeSession(),
            produce_reply=fake_produce,
            probe_user_streams=lambda: set(),
        )
        sched.start()
        try:
            # Kick off batch 1.
            sched.add(_make_utterance(user_id=1, name="Alice"))
            # Wait until produce_reply is actually awaiting (in_flight=True).
            assert await _wait_for(lambda: first_started.is_set(), timeout=2.0)
            # Three more utterances arrive while we're in flight.
            sched.add(_make_utterance(user_id=2, name="Bob"))
            sched.add(_make_utterance(user_id=3, name="Carol"))
            sched.add(_make_utterance(user_id=4, name="Dave"))
            # They should NOT trigger a separate produce_reply yet.
            await asyncio.sleep(0.30)
            assert len(calls) == 1, f"got {len(calls)} batches before release"
            # Release the first batch; the scheduler should immediately drain
            # the queued three into a SINGLE next batch.
            release.set()
            assert await _wait_for(lambda: len(calls) >= 2, timeout=2.0)
            assert len(calls) == 2, f"expected 2 batches total, got {len(calls)}"
            users = {u.user_id for u in calls[1]}
            assert users == {2, 3, 4}
        finally:
            sched.stop()
            release.set()  # in case test failed before release

    asyncio.run(go())


# ---------------------------------------------------------------------------
# Hard deadline caps the merge window when others keep speaking
# ---------------------------------------------------------------------------


def test_hard_deadline_fires_when_others_keep_speaking(monkeypatch):
    """If the probe always reports another active user, MERGE_DEADLINE_HARD
    is the only thing that lets the batch fire. Verify it does."""
    monkeypatch.setattr(ReplyScheduler, "MERGE_DEADLINE_HARD", 0.40)
    monkeypatch.setattr(ReplyScheduler, "MERGE_DEADLINE_SHORT", 0.40)
    monkeypatch.setattr(ReplyScheduler, "MERGE_SETTLE", 0.10)
    monkeypatch.setattr(ReplyScheduler, "POLL_INTERVAL", 0.02)

    calls: List[List[BatchedUtterance]] = []

    async def go():
        async def fake_produce(batch):
            calls.append(batch)

        sched = ReplyScheduler(
            session=_FakeSession(),
            produce_reply=fake_produce,
            # phantom user 99 — never in the batch, so probe-set minus
            # batched-set is always non-empty.
            probe_user_streams=lambda: {99},
        )
        sched.start()
        try:
            t0 = time.time()
            sched.add(_make_utterance(user_id=1, name="Alice"))
            assert await _wait_for(lambda: len(calls) >= 1, timeout=2.0)
            elapsed = time.time() - t0
            # Must have waited up to (close to) HARD before firing.
            assert elapsed >= 0.30, f"fired too early: {elapsed:.3f}s"
            assert elapsed < 1.0, f"fired too late: {elapsed:.3f}s"
        finally:
            sched.stop()

    asyncio.run(go())


# ---------------------------------------------------------------------------
# _default_produce_reply: batch with no respondable utterances skips the LLM
# ---------------------------------------------------------------------------


def test_default_produce_reply_skips_llm_when_nothing_respondable(monkeypatch):
    """Filler-only batch (no should_respond=True, no expecting_reply=True)
    must NOT call responses.handle_response. User-side history was already
    written by _handle_utterance, so we don't double-record here either."""

    handle_response_called: List[bool] = []

    async def fake_handle_response(*args, **kwargs):
        handle_response_called.append(True)
        return "should not be called"

    monkeypatch.setattr(responses, "handle_response", fake_handle_response)

    sched = ReplyScheduler(
        session=_FakeSession(),
        probe_user_streams=lambda: set(),
    )
    batch = [
        _make_utterance(user_id=1, name="Alice", passes=False, expecting_reply=False),
        _make_utterance(user_id=2, name="Bob", passes=False, expecting_reply=False),
    ]

    asyncio.run(sched._default_produce_reply(batch))

    assert handle_response_called == []


# ---------------------------------------------------------------------------
# _default_produce_reply: respondable batch records exactly ONE assistant
# message in voice_message_history (user-side is recorded by _handle_utterance)
# ---------------------------------------------------------------------------


def test_default_produce_reply_records_single_assistant_entry(monkeypatch):
    # Stub format_for_prompt to None so we don't depend on the memory store.
    from src import voice_memory

    monkeypatch.setattr(
        voice_memory.voice_memory_store,
        "format_for_prompt",
        lambda *_args, **_kwargs: "",
    )

    async def fake_handle_response(*args, **kwargs):
        # Honour record_history=False — that's the contract under test.
        assert kwargs.get("record_history") is False
        return "Hi everyone."

    monkeypatch.setattr(responses, "handle_response", fake_handle_response)

    # Stub TTS + bus so the test doesn't need real audio.
    from src.commands import tts as tts_mod

    async def fake_generate_speech(*_args, **_kwargs):
        return "/tmp/nonexistent.mp3"

    monkeypatch.setattr(tts_mod, "generate_speech", fake_generate_speech)

    from src import audio_bus

    fake_bus = MagicMock()
    monkeypatch.setattr(audio_bus, "get_guild_bus", lambda _gid: fake_bus)
    # Also patch the import the scheduler reaches via voice_listen module.
    import src.voice_listen as vl

    monkeypatch.setattr(vl, "get_guild_bus", lambda _gid: fake_bus)
    monkeypatch.setattr(vl, "FFmpegPCMAudio", MagicMock(), raising=False)
    # Patch discord.FFmpegPCMAudio to a MagicMock so it doesn't actually try
    # to spawn ffmpeg.
    import discord as _discord

    monkeypatch.setattr(_discord, "FFmpegPCMAudio", MagicMock())

    # Capture history writes.
    history_writes: List[tuple] = []
    original_add = responses.voice_message_history.add_message

    def spy_add(key, role, content):
        history_writes.append((key, role, content))
        return original_add(key, role, content)

    monkeypatch.setattr(
        responses.voice_message_history, "add_message", spy_add
    )

    sched = ReplyScheduler(
        session=_FakeSession(),
        probe_user_streams=lambda: set(),
    )
    batch = [
        _make_utterance(user_id=1, name="Alice", passes=True),
        _make_utterance(user_id=2, name="Bob", passes=True),
    ]

    asyncio.run(sched._default_produce_reply(batch))

    # Exactly one assistant entry recorded (and no user entries — those would
    # have come from _handle_utterance, which we don't exercise in this test).
    assistant_writes = [w for w in history_writes if w[1] == "assistant"]
    user_writes = [w for w in history_writes if w[1] == "user"]
    assert len(assistant_writes) == 1, f"got {history_writes}"
    assert assistant_writes[0][2] == "Hi everyone."
    assert user_writes == [], f"unexpected user writes: {user_writes}"
