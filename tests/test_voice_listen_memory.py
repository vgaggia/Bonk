"""Integration-ish tests for the memory wiring.

Tests the end-to-end paths that don't strictly need a Discord client:
- `responses.handle_response` correctly forwards `memory_block` into the
  system prompt for both the Anthropic and OpenAI-like backends.
- `responses.openai_like_api` uses the memory-augmented system prompt for
  the local-model `system_prompt` payload key (regression for C2).
- `voice_memory.ExtractionWorker` debounces correctly so calling
  `maybe_run` on every utterance is cheap.
- Two users with identical display names cannot cross-attribute facts
  through `apply_extraction` (regression for B2).
"""

import asyncio
from pathlib import Path
from typing import Any, Dict

import pytest

from src import responses
from src.voice_memory import (
    UserMemory,
    VoiceMemoryStore,
    _speaker_label,
)

# ---------------------------------------------------------------------------
# Memory block forwarding into the response backends
# ---------------------------------------------------------------------------


class _StubAnthropicResponse:
    def __init__(self, text: str):
        self.content = [type("B", (), {"text": text})]


class _StubAnthropicClient:
    """Captures the system prompt and messages sent to messages.create."""

    def __init__(self, reply_text: str = "ok"):
        self.reply_text = reply_text
        self.captured: Dict[str, Any] = {}
        self.messages = self

    def create(self, **kwargs):
        self.captured = kwargs
        return _StubAnthropicResponse(self.reply_text)


def test_anthropic_api_appends_memory_block_to_system_prompt(monkeypatch: pytest.MonkeyPatch):
    stub = _StubAnthropicClient(reply_text="hi back")
    # Voice mode + memory_block path.
    text = asyncio.run(
        responses.ModelAPIs.anthropic_api(
            client=stub,
            message="Alice: hello",
            model="claude-haiku-4-5",
            user_id=None,
            voice_mode=True,
            extra_context=None,
            memory_block="PRIVATE NOTES — speaker is Alice. Vibe: warm.",
        )
    )
    assert text == "hi back"
    sent_system = stub.captured["system"]
    # Original voice instruction is preserved.
    assert "Discord voice chat" in sent_system
    # Memory block is appended.
    assert "PRIVATE NOTES" in sent_system
    assert "Vibe: warm" in sent_system


def test_anthropic_api_omits_memory_when_none(monkeypatch: pytest.MonkeyPatch):
    stub = _StubAnthropicClient(reply_text="ok")
    asyncio.run(
        responses.ModelAPIs.anthropic_api(
            client=stub,
            message="Alice: hello",
            model="claude-haiku-4-5",
            user_id=None,
            voice_mode=True,
        )
    )
    sent_system = stub.captured["system"]
    assert "PRIVATE NOTES" not in sent_system


# ---- openai_like_api: capture HTTP request via monkeypatched requests.post


class _StubResponse:
    def __init__(self, text: str):
        self.text = text
        self._json = {
            "choices": [{"message": {"content": text}}]
        }
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._json


@pytest.fixture
def captured_post(monkeypatch: pytest.MonkeyPatch):
    """Patch requests.post inside responses to capture payloads."""
    captured: Dict[str, Any] = {}

    def fake_post(url, headers=None, json=None, timeout=None, **_kw):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _StubResponse(text="hi back")

    monkeypatch.setattr(responses.requests, "post", fake_post)
    return captured


def test_openai_like_api_appends_memory_block_to_system_message(captured_post):
    asyncio.run(
        responses.ModelAPIs.openai_like_api(
            base_url="https://api.openai.example",
            api_key="sk-test",
            message="Alice: hello",
            model="gpt-4o",
            user_id=None,
            voice_mode=True,
            memory_block="PRIVATE NOTES — vibe: warm",
        )
    )
    payload = captured_post["json"]
    msgs = payload["messages"]
    system_msg = next(m for m in msgs if m["role"] == "system")
    assert "Discord voice chat" in system_msg["content"]
    assert "PRIVATE NOTES" in system_msg["content"]


def test_openai_like_api_local_model_uses_augmented_system_prompt(captured_post):
    """Regression for Codex C2: when memory_block is set and model is
    local-model, the payload's `system_prompt` key must include the memory
    block, not the bare LOCAL_SYSTEM_PROMPT."""
    asyncio.run(
        responses.ModelAPIs.openai_like_api(
            base_url="http://127.0.0.1:5000/v1",
            api_key=None,
            message="hello there",
            model="local-model",
            user_id=None,
            voice_mode=False,
            memory_block="PRIVATE NOTES — vibe: warm",
        )
    )
    payload = captured_post["json"]
    # Both the system message AND the local-model `system_prompt` key carry the memory.
    system_msg = next(m for m in payload["messages"] if m["role"] == "system")
    assert "PRIVATE NOTES" in system_msg["content"]
    assert "PRIVATE NOTES" in payload["system_prompt"]


# ---------------------------------------------------------------------------
# ExtractionWorker debouncing — every-utterance scheduling stays cheap
# ---------------------------------------------------------------------------


def test_extraction_worker_debounces_back_to_back_calls(tmp_path: Path):
    """Even if maybe_run is called on every utterance, the LLM is invoked at
    most once per debounce interval."""
    store = VoiceMemoryStore(path=tmp_path / "memories.json")
    call_count = {"n": 0}

    class CountingClient:
        def __init__(self):
            self.messages = self

        def create(self, **_kwargs):
            call_count["n"] += 1

            class _R:
                content = [type("B", (), {"text": '{"users": {}, "channel": {"add_facts": []}}'})]

            return _R()

    from src.voice_memory import ExtractionWorker

    worker = ExtractionWorker(
        guild_id=1, store=store, anthropic_client=CountingClient(), model="haiku"
    )

    async def go():
        # Bump pending well past threshold (>= 3).
        for _ in range(20):
            worker.note_new_user_utterance()
        # Call maybe_run many times in quick succession — only one should land.
        for _ in range(20):
            await worker.maybe_run(
                ["[Alice (speaker_7)] hi"], [7], {7: "Alice"}
            )

    asyncio.run(go())
    assert call_count["n"] == 1, f"expected exactly 1 LLM call, got {call_count['n']}"


# ---------------------------------------------------------------------------
# Duplicate display names: id-keying prevents cross-attribution
# ---------------------------------------------------------------------------


def test_apply_extraction_does_not_cross_attribute_duplicate_names(tmp_path: Path):
    """Two users named 'Alice' in the same guild — facts must go only to the
    correct user_id."""
    store = VoiceMemoryStore(path=tmp_path / "memories.json")
    blob = store._ensure_guild(1)
    blob["users"][111] = UserMemory(display_name="Alice")
    blob["users"][222] = UserMemory(display_name="Alice")  # same display name

    result = {
        "users": {
            _speaker_label(111): {"add_facts": ["plays guitar"]},
            _speaker_label(222): {"add_facts": ["is a chef"]},
        },
        "channel": {"add_facts": []},
    }
    store.apply_extraction(1, result, present_user_ids={111, 222})

    facts_111 = [f.text for f in store.get_user(1, 111).facts]
    facts_222 = [f.text for f in store.get_user(1, 222).facts]
    assert facts_111 == ["plays guitar"]
    assert facts_222 == ["is a chef"]


# ---------------------------------------------------------------------------
# Bot self-utterance filtering (defensive)
# ---------------------------------------------------------------------------


def test_extraction_worker_unknown_speaker_label_is_dropped(tmp_path: Path):
    """If somehow the bot's user id appeared as a speaker_<id> label that
    isn't in present_user_ids, apply_extraction must drop it silently."""
    store = VoiceMemoryStore(path=tmp_path / "memories.json")
    bot_uid = 999999
    result = {
        "users": {
            _speaker_label(bot_uid): {"add_facts": ["I am Bonk and I love memes"]}
        },
        "channel": {"add_facts": []},
    }
    stats = store.apply_extraction(1, result, present_user_ids={111, 222})
    assert stats["facts_added"] == 0
    assert store.get_user_optional(1, bot_uid) is None


# ---------------------------------------------------------------------------
# voice_listen helpers we can exercise without Discord
# ---------------------------------------------------------------------------


def test_strip_name_prefix():
    from src.voice_listen import _strip_name_prefix

    assert _strip_name_prefix("Alice: I play guitar", "Alice") == "I play guitar"
    assert _strip_name_prefix("Bob: hi", "Alice") == "Bob: hi"
    assert _strip_name_prefix("hi", "Alice") == "hi"
