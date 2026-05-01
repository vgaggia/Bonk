"""Unit tests for src/voice_memory.py.

Covers: atomic save round-trip and partial-write recovery, schema migration,
prompt formatting (ordering and char cap), id-keyed extraction merging
(dedup + replace branch + duplicate names + opt-out), tolerant JSON parsing,
deterministic PII filter, bounded extraction-call timeout, and the no-recital
injection prompt regression.
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

from src import voice_memory
from src.voice_memory import (
    INJECTION_HEADER,
    PROMPT_BLOCK_CHAR_CAP,
    USER_FACT_TARGET,
    ChannelMemory,
    ExtractionWorker,
    Fact,
    UserMemory,
    VoiceMemoryStore,
    _dedup_against,
    _looks_sensitive,
    _parse_extraction_json,
    _speaker_label,
    extract_memories,
)


@pytest.fixture
def tmp_store(tmp_path: Path) -> VoiceMemoryStore:
    return VoiceMemoryStore(path=tmp_path / "memories.json")


def _seed(
    store: VoiceMemoryStore,
    guild_id: int,
    users: Dict[int, UserMemory],
    channel_facts=None,
):
    blob = store._ensure_guild(guild_id)
    for uid, mem in users.items():
        blob["users"][uid] = mem
    if channel_facts:
        blob["channel"] = ChannelMemory(facts=list(channel_facts))


# ---------------------------------------------------------------------------
# Atomic save / load round-trip
# ---------------------------------------------------------------------------


def test_atomic_save_round_trip(tmp_store: VoiceMemoryStore):
    _seed(
        tmp_store,
        guild_id=42,
        users={
            1: UserMemory(
                display_name="Alice",
                facts=["plays guitar", "lives in Sydney"],
                vibe="energetic",
            )
        },
        channel_facts=["Tuesday is music night"],
    )
    asyncio.run(tmp_store.save())

    assert tmp_store.path.exists()
    assert not tmp_store.path.with_suffix(".json.tmp").exists()
    raw = json.loads(tmp_store.path.read_text(encoding="utf-8"))
    assert raw["42"]["users"]["1"]["display_name"] == "Alice"
    # Fact strings auto-coerced to objects with metadata.
    assert raw["42"]["users"]["1"]["facts"][0]["text"] == "plays guitar"
    assert "updated_at" in raw["42"]["users"]["1"]["facts"][0]
    assert raw["42"]["channel"]["facts"][0]["text"] == "Tuesday is music night"

    fresh = VoiceMemoryStore(path=tmp_store.path)
    fresh.load()
    facts = fresh.get_user(42, 1).facts
    assert [f.text for f in facts] == ["plays guitar", "lives in Sydney"]


def test_load_handles_missing_file(tmp_path: Path):
    store = VoiceMemoryStore(path=tmp_path / "nope.json")
    store.load()
    assert store.get_user_optional(1, 1) is None


def test_load_handles_corrupt_json(tmp_path: Path):
    p = tmp_path / "corrupt.json"
    p.write_text("{not valid json", encoding="utf-8")
    store = VoiceMemoryStore(path=p)
    store.load()
    assert store.get_user_optional(1, 1) is None


def test_load_ignores_stale_tmp_file(tmp_path: Path):
    """If a crash leaves a partial .tmp behind, load reads the committed JSON
    only, never the tmp."""
    committed = tmp_path / "memories.json"
    tmp = tmp_path / "memories.json.tmp"
    committed.write_text(
        json.dumps(
            {
                "1": {
                    "users": {
                        "7": {
                            "display_name": "Alice",
                            "facts": [{"text": "committed", "updated_at": "2026-01-01T00:00:00+00:00", "source": "extracted"}],
                            "vibe": "",
                            "notes": "",
                            "opted_out": False,
                        }
                    },
                    "channel": {"facts": []},
                }
            }
        ),
        encoding="utf-8",
    )
    tmp.write_text("{\"this\": \"is partial garbage", encoding="utf-8")

    store = VoiceMemoryStore(path=committed)
    store.load()
    facts = store.get_user(1, 7).facts
    assert [f.text for f in facts] == ["committed"], "tmp must be ignored, only committed JSON loads"


def test_save_does_not_leave_partial_file(tmp_store: VoiceMemoryStore):
    _seed(tmp_store, 1, {1: UserMemory(display_name="x", facts=["fact"])})
    asyncio.run(tmp_store.save())
    tmp_for_save = tmp_store.path.with_suffix(tmp_store.path.suffix + ".tmp")
    assert not tmp_for_save.exists()


def test_legacy_string_facts_migrate_with_source_tag(tmp_path: Path):
    """Old-format JSON (facts: ["a", "b"]) loads cleanly into the new
    Fact-based store, with each Fact tagged source='legacy'."""
    p = tmp_path / "legacy.json"
    p.write_text(
        json.dumps(
            {
                "1": {
                    "users": {
                        "7": {
                            "display_name": "Old Alice",
                            "facts": ["plays guitar", "lives in Sydney"],
                            "vibe": "warm",
                            "notes": "",
                        }
                    },
                    "channel": {"facts": ["Tuesday is movie night"]},
                }
            }
        ),
        encoding="utf-8",
    )
    store = VoiceMemoryStore(path=p)
    store.load()
    mem = store.get_user(1, 7)
    assert [f.text for f in mem.facts] == ["plays guitar", "lives in Sydney"]
    assert all(f.source == "legacy" for f in mem.facts)
    assert all(f.updated_at for f in mem.facts), "updated_at must be populated"
    channel = store.get_channel(1)
    assert [f.text for f in channel.facts] == ["Tuesday is movie night"]
    assert channel.facts[0].source == "legacy"


# ---------------------------------------------------------------------------
# format_for_prompt: speaker first, others below, channel below, char cap
# ---------------------------------------------------------------------------


def test_format_for_prompt_orders_speaker_first(tmp_store: VoiceMemoryStore):
    _seed(
        tmp_store,
        1,
        {
            10: UserMemory(display_name="Alice", facts=["plays guitar"], vibe="energetic"),
            20: UserMemory(display_name="Bob", facts=["is a chef"], vibe="grumpy but warm"),
        },
        channel_facts=["Tuesday is movie night"],
    )

    out = tmp_store.format_for_prompt(
        guild_id=1,
        speaker_user_id=20,
        present_user_ids=[10, 20],
        speaker_display_name="Bob",
        present_display_names={10: "Alice", 20: "Bob"},
    )
    assert "Speaking now: Bob" in out
    bob_idx = out.index("About Bob (the speaker):")
    alice_idx = out.index("About Alice:")
    channel_idx = out.index("Channel context:")
    assert bob_idx < alice_idx < channel_idx


def test_format_for_prompt_omits_users_with_no_memories(tmp_store: VoiceMemoryStore):
    _seed(
        tmp_store,
        1,
        {10: UserMemory(display_name="Alice", facts=["plays guitar"])},
    )
    out = tmp_store.format_for_prompt(
        guild_id=1,
        speaker_user_id=20,
        present_user_ids=[10, 20],
        speaker_display_name="Bob",
        present_display_names={10: "Alice", 20: "Bob"},
    )
    assert "About Bob (the speaker):" not in out
    assert "About Alice:" in out
    assert "Currently in voice chat: Alice, Bob" in out


def test_format_for_prompt_returns_empty_when_nothing_known(tmp_store: VoiceMemoryStore):
    out = tmp_store.format_for_prompt(
        guild_id=99,
        speaker_user_id=1,
        present_user_ids=[1],
        speaker_display_name="Solo",
        present_display_names={1: "Solo"},
    )
    assert out == ""


def test_format_for_prompt_respects_char_cap(tmp_store: VoiceMemoryStore):
    long_fact = "fact " + ("x" * 80)
    _seed(
        tmp_store,
        1,
        {
            10: UserMemory(
                display_name="Alice",
                facts=[long_fact + str(i) for i in range(30)],
                vibe="vibe " + "y" * 100,
                notes="z" * 600,
            ),
            20: UserMemory(
                display_name="Bob",
                facts=[long_fact + str(i) for i in range(30)],
                vibe="bob vibe " + "y" * 100,
                notes="bobnotes " * 80,
            ),
            30: UserMemory(
                display_name="Charlie",
                facts=[long_fact + str(i) for i in range(30)],
                notes="charnotes " * 80,
            ),
        },
        channel_facts=[f"channel fact {i}" for i in range(20)],
    )

    out = tmp_store.format_for_prompt(
        guild_id=1,
        speaker_user_id=10,
        present_user_ids=[10, 20, 30],
        speaker_display_name="Alice",
        present_display_names={10: "Alice", 20: "Bob", 30: "Charlie"},
    )
    assert len(out) <= PROMPT_BLOCK_CHAR_CAP
    # Speaker section always present when speaker has memory.
    assert "About Alice (the speaker):" in out
    # No mid-section truncation: the rendered block ends with a newline (from
    # the final rstrip + "\n"), never a half-line.
    assert out.endswith("\n")


def test_format_for_prompt_skips_opted_out_speaker(tmp_store: VoiceMemoryStore):
    _seed(
        tmp_store,
        1,
        {
            10: UserMemory(display_name="Alice", facts=["plays guitar"], opted_out=True),
        },
    )
    out = tmp_store.format_for_prompt(
        guild_id=1,
        speaker_user_id=10,
        present_user_ids=[10],
        speaker_display_name="Alice",
        present_display_names={10: "Alice"},
    )
    assert out == "", "opted-out speaker should produce empty memory block"


def test_format_for_prompt_skips_opted_out_others(tmp_store: VoiceMemoryStore):
    _seed(
        tmp_store,
        1,
        {
            10: UserMemory(display_name="Alice", facts=["plays guitar"]),
            20: UserMemory(display_name="Bob", facts=["secret hobby"], opted_out=True),
        },
    )
    out = tmp_store.format_for_prompt(
        guild_id=1,
        speaker_user_id=10,
        present_user_ids=[10, 20],
        speaker_display_name="Alice",
        present_display_names={10: "Alice", 20: "Bob"},
    )
    assert "About Alice (the speaker):" in out
    assert "About Bob:" not in out
    assert "secret hobby" not in out


# ---------------------------------------------------------------------------
# Injection prompt regression — no-recital wording must be present
# ---------------------------------------------------------------------------


def test_injection_header_forbids_recital():
    text = INJECTION_HEADER.lower()
    assert "never read these notes aloud" in text or "do not recite" in text or "redirect" in text
    assert "/memories" in INJECTION_HEADER


def test_render_includes_injection_header(tmp_store: VoiceMemoryStore):
    _seed(
        tmp_store,
        1,
        {10: UserMemory(display_name="Alice", facts=["plays guitar"])},
    )
    out = tmp_store.format_for_prompt(
        guild_id=1,
        speaker_user_id=10,
        present_user_ids=[10],
        speaker_display_name="Alice",
        present_display_names={10: "Alice"},
    )
    assert INJECTION_HEADER.strip().splitlines()[0] in out


# ---------------------------------------------------------------------------
# apply_extraction: id-keyed, dedup, replace branch, opt-out
# ---------------------------------------------------------------------------


def test_apply_extraction_adds_and_dedupes_exact(tmp_store: VoiceMemoryStore):
    _seed(
        tmp_store,
        1,
        {7: UserMemory(display_name="Alice", facts=["plays guitar in a punk band"])},
    )

    result = {
        "users": {
            _speaker_label(7): {
                "add_facts": [
                    "plays guitar in a punk band",   # exact dup of existing
                    "lives in Sydney",                # new
                    "PLAYS GUITAR IN A PUNK BAND",    # case-only dup
                ]
            }
        },
        "channel": {"add_facts": []},
    }
    stats = tmp_store.apply_extraction(1, result, present_user_ids={7})
    assert stats["facts_added"] == 1
    assert [f.text for f in tmp_store.get_user(1, 7).facts] == [
        "plays guitar in a punk band",
        "lives in Sydney",
    ]


def test_apply_extraction_keeps_legitimate_negation(tmp_store: VoiceMemoryStore):
    """The old substring-containment dedup wrongly dropped 'dislikes coffee'
    when 'likes coffee' was already stored. With exact-match dedup, the
    distinct negation is now kept."""
    _seed(
        tmp_store,
        1,
        {7: UserMemory(display_name="Alice", facts=["likes coffee"])},
    )
    result = {
        "users": {_speaker_label(7): {"add_facts": ["dislikes coffee"]}},
        "channel": {"add_facts": []},
    }
    tmp_store.apply_extraction(1, result, present_user_ids={7})
    facts = [f.text for f in tmp_store.get_user(1, 7).facts]
    assert "likes coffee" in facts
    assert "dislikes coffee" in facts


def test_apply_extraction_skips_unknown_speaker(tmp_store: VoiceMemoryStore):
    result = {
        "users": {_speaker_label(99): {"add_facts": ["evil"]}},
        "channel": {"add_facts": []},
    }
    stats = tmp_store.apply_extraction(1, result, present_user_ids={7})
    assert stats["facts_added"] == 0
    assert tmp_store.get_user_optional(1, 99) is None


def test_apply_extraction_skips_non_label_keys(tmp_store: VoiceMemoryStore):
    """Defense: if the model outputs a display name instead of speaker_<id>,
    we drop it rather than guess which user it meant."""
    result = {
        "users": {"Alice": {"add_facts": ["this would be a bug to apply"]}},
        "channel": {"add_facts": []},
    }
    stats = tmp_store.apply_extraction(1, result, present_user_ids={7})
    assert stats["facts_added"] == 0


def test_apply_extraction_replace_branch_overrides_add(tmp_store: VoiceMemoryStore):
    existing = [f"old fact {i}" for i in range(USER_FACT_TARGET + 5)]
    _seed(tmp_store, 1, {7: UserMemory(display_name="Alice", facts=list(existing))})

    result = {
        "users": {
            _speaker_label(7): {
                "add_facts": ["this should be ignored when replace_facts is present"],
                "replace_facts": [f"consolidated {i}" for i in range(USER_FACT_TARGET)],
            }
        },
        "channel": {"add_facts": []},
    }
    tmp_store.apply_extraction(1, result, present_user_ids={7})
    facts = [f.text for f in tmp_store.get_user(1, 7).facts]
    assert all(f.startswith("consolidated") for f in facts)
    assert len(facts) <= USER_FACT_TARGET


def test_apply_extraction_updates_vibe_and_notes(tmp_store: VoiceMemoryStore):
    _seed(tmp_store, 1, {7: UserMemory(display_name="Alice")})
    result = {
        "users": {
            _speaker_label(7): {"vibe": "warm and curious", "notes": "loves obscure jazz"}
        },
        "channel": {"add_facts": []},
    }
    tmp_store.apply_extraction(1, result, present_user_ids={7})
    mem = tmp_store.get_user(1, 7)
    assert mem.vibe == "warm and curious"
    assert mem.notes == "loves obscure jazz"


def test_apply_extraction_channel_facts(tmp_store: VoiceMemoryStore):
    result: Dict[str, Any] = {
        "users": {},
        "channel": {"add_facts": ["Tuesday is movie night", "they all work together"]},
    }
    tmp_store.apply_extraction(1, result, present_user_ids=set())
    facts = [f.text for f in tmp_store.get_channel(1).facts]
    assert facts == ["Tuesday is movie night", "they all work together"]


def test_apply_extraction_handles_garbage_input(tmp_store: VoiceMemoryStore):
    tmp_store.apply_extraction(1, "not a dict", present_user_ids={7})  # type: ignore[arg-type]
    tmp_store.apply_extraction(1, {"users": "wrong type"}, present_user_ids={7})  # type: ignore[arg-type]
    tmp_store.apply_extraction(
        1, {"users": {_speaker_label(7): "wrong inner type"}}, present_user_ids={7}
    )
    assert tmp_store.get_user_optional(1, 7) is None


def test_apply_extraction_skips_opted_out_users(tmp_store: VoiceMemoryStore):
    _seed(
        tmp_store,
        1,
        {7: UserMemory(display_name="Alice", opted_out=True)},
    )
    result = {
        "users": {_speaker_label(7): {"add_facts": ["ignored fact"]}},
        "channel": {"add_facts": []},
    }
    stats = tmp_store.apply_extraction(1, result, present_user_ids={7})
    assert stats["facts_added"] == 0
    assert not tmp_store.get_user(1, 7).facts


def test_apply_extraction_handles_duplicate_display_names(tmp_store: VoiceMemoryStore):
    """Two different user_ids share the same display_name. Each should write
    only to their own record because we key by id, not name."""
    _seed(
        tmp_store,
        1,
        {
            10: UserMemory(display_name="Alice"),
            20: UserMemory(display_name="Alice"),  # collision
        },
    )
    result = {
        "users": {
            _speaker_label(10): {"add_facts": ["plays guitar"]},
            _speaker_label(20): {"add_facts": ["is a chef"]},
        },
        "channel": {"add_facts": []},
    }
    tmp_store.apply_extraction(1, result, present_user_ids={10, 20})
    assert [f.text for f in tmp_store.get_user(1, 10).facts] == ["plays guitar"]
    assert [f.text for f in tmp_store.get_user(1, 20).facts] == ["is a chef"]


# ---------------------------------------------------------------------------
# PII filter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fact",
    [
        "alice@example.com",
        "my email is alice@example.com",
        "my phone is 555-867-5309",
        "my SSN is 123-45-6789",
        "lives at 123 Main Street",
        "API key sk-aB3xQ9z2KpL5mN8vR1tY4wU7eS6dF0gH",
        "card 4111 1111 1111 1111",  # valid Luhn
    ],
)
def test_looks_sensitive_catches_common_pii(fact):
    assert _looks_sensitive(fact), f"should flag: {fact}"


@pytest.mark.parametrize(
    "fact",
    [
        "plays guitar in a punk band",
        "lives in Sydney",
        "doesn't drink caffeine after 5pm",
        "owns 3 cats",
        "has 1234 records",  # short digit run, not card-shaped
    ],
)
def test_looks_sensitive_does_not_catch_normal_facts(fact):
    assert not _looks_sensitive(fact), f"should NOT flag: {fact}"


def test_apply_extraction_drops_sensitive_facts(tmp_store: VoiceMemoryStore):
    _seed(tmp_store, 1, {7: UserMemory(display_name="Alice")})
    result = {
        "users": {
            _speaker_label(7): {
                "add_facts": [
                    "plays guitar",
                    "email is alice@example.com",
                    "lives in Sydney",
                ]
            }
        },
        "channel": {"add_facts": []},
    }
    tmp_store.apply_extraction(1, result, present_user_ids={7})
    facts = [f.text for f in tmp_store.get_user(1, 7).facts]
    assert "plays guitar" in facts
    assert "lives in Sydney" in facts
    assert all("@" not in f for f in facts)


def test_apply_extraction_drops_sensitive_vibe_and_notes(tmp_store: VoiceMemoryStore):
    _seed(tmp_store, 1, {7: UserMemory(display_name="Alice", vibe="old", notes="old notes")})
    result = {
        "users": {
            _speaker_label(7): {
                "vibe": "lives at 123 Main Street, friendly though",
                "notes": "phone is 555-867-5309",
            }
        },
        "channel": {"add_facts": []},
    }
    tmp_store.apply_extraction(1, result, present_user_ids={7})
    mem = tmp_store.get_user(1, 7)
    # Sensitive content is rejected; old values stay.
    assert mem.vibe == "old"
    assert mem.notes == "old notes"


# ---------------------------------------------------------------------------
# Dedup helper
# ---------------------------------------------------------------------------


def test_dedup_against_uses_exact_match():
    out = _dedup_against(
        existing=["likes coffee"],
        new_items=["likes coffee", "dislikes coffee", "Likes Coffee"],
    )
    # Exact normalized match drops "Likes Coffee", keeps "dislikes coffee".
    assert out == ["dislikes coffee"]


# ---------------------------------------------------------------------------
# JSON parsing tolerance
# ---------------------------------------------------------------------------


def test_parse_extraction_json_strips_code_fences():
    text = '```json\n{"users": {"speaker_111": {"add_facts": ["fact"]}}, "channel": {"add_facts": []}}\n```'
    out = _parse_extraction_json(text)
    assert out["users"]["speaker_111"]["add_facts"] == ["fact"]


def test_parse_extraction_json_extracts_first_object_from_prose():
    text = 'Sure, here is the JSON: {"users": {}, "channel": {"add_facts": []}} Hope this helps!'
    out = _parse_extraction_json(text)
    assert out == {"users": {}, "channel": {"add_facts": []}}


def test_parse_extraction_json_returns_empty_on_garbage():
    assert _parse_extraction_json("") == {}
    assert _parse_extraction_json("definitely not JSON") == {}
    assert _parse_extraction_json("{ broken") == {}


# ---------------------------------------------------------------------------
# extract_memories — failure modes (no anthropic call needed)
# ---------------------------------------------------------------------------


class _FakeAnthropicMessages:
    def __init__(self, text_or_exc):
        self.text_or_exc = text_or_exc

    def create(self, **_kwargs):
        if isinstance(self.text_or_exc, Exception):
            raise self.text_or_exc

        class _Block:
            def __init__(self, text):
                self.text = text

        class _Response:
            def __init__(self, text):
                self.content = [_Block(text)]

        return _Response(self.text_or_exc)


class _FakeAnthropicClient:
    def __init__(self, text_or_exc):
        self.messages = _FakeAnthropicMessages(text_or_exc)


def test_extract_memories_returns_empty_on_malformed_json():
    client = _FakeAnthropicClient("not JSON at all")
    out = extract_memories(
        client,
        "claude-haiku",
        ["[Alice (speaker_7)] hello"],
        present_user_ids=[7],
        display_names_by_id={7: "Alice"},
        existing_dump="(none)",
        over_cap_user_ids=[],
    )
    assert out == {}


def test_extract_memories_returns_empty_on_api_exception():
    client = _FakeAnthropicClient(RuntimeError("boom"))
    out = extract_memories(
        client,
        "claude-haiku",
        ["[Alice (speaker_7)] hello"],
        present_user_ids=[7],
        display_names_by_id={7: "Alice"},
        existing_dump="(none)",
        over_cap_user_ids=[],
    )
    assert out == {}


def test_extract_memories_empty_transcript_is_short_circuited():
    out = extract_memories(
        None,
        "claude-haiku",
        [],
        present_user_ids=[7],
        display_names_by_id={7: "Alice"},
        existing_dump="(none)",
        over_cap_user_ids=[],
    )
    assert out == {}


def test_extract_memories_parses_valid_response():
    client = _FakeAnthropicClient(
        '{"users": {"speaker_7": {"add_facts": ["plays guitar"]}}, "channel": {"add_facts": []}}'
    )
    out = extract_memories(
        client,
        "claude-haiku",
        ["[Alice (speaker_7)] I play guitar"],
        present_user_ids=[7],
        display_names_by_id={7: "Alice"},
        existing_dump="(none)",
        over_cap_user_ids=[],
    )
    assert out["users"]["speaker_7"]["add_facts"] == ["plays guitar"]


# ---------------------------------------------------------------------------
# ExtractionWorker debounce + bounded flush
# ---------------------------------------------------------------------------


def test_extraction_worker_respects_min_pending(tmp_store: VoiceMemoryStore):
    calls: List[bool] = []

    class TrackingClient:
        def __init__(self):
            self.messages = self

        def create(self, **_kwargs):
            calls.append(True)

            class _R:
                content = [type("B", (), {"text": '{"users": {}, "channel": {"add_facts": []}}'})]

            return _R()

    worker = ExtractionWorker(
        guild_id=1, store=tmp_store, anthropic_client=TrackingClient(), model="haiku"
    )

    async def go():
        worker.note_new_user_utterance()
        await worker.maybe_run(["[Alice (speaker_7)] hi"], [7], {7: "Alice"})

    asyncio.run(go())
    assert calls == []  # below threshold


def test_extraction_worker_flush_is_bounded(tmp_store: VoiceMemoryStore):
    """A hung anthropic client must NOT freeze flush(). The release_event is
    used to free the executor thread after the assertion so the test exits
    promptly (cancelling asyncio.wait_for does not kill the executor thread)."""
    import threading

    release = threading.Event()

    class HangingClient:
        def __init__(self):
            self.messages = self

        def create(self, **_kwargs):
            release.wait(timeout=10.0)  # released by the test below; cap at 10s defensively

            class _R:
                content = [type("B", (), {"text": "{}"})]

            return _R()

    worker = ExtractionWorker(
        guild_id=1,
        store=tmp_store,
        anthropic_client=HangingClient(),
        model="haiku",
        timeout=0.3,
    )

    async def go():
        for _ in range(5):
            worker.note_new_user_utterance()
        start = time.time()
        await worker.flush(["[Alice (speaker_7)] something"], [7], {7: "Alice"})
        elapsed = time.time() - start
        # Release the hung thread BEFORE asyncio.run shuts down the executor,
        # otherwise loop shutdown will wait for the thread to drain.
        release.set()
        return elapsed

    elapsed = asyncio.run(go())
    assert elapsed < 2.0, f"flush took {elapsed:.1f}s — bounded timeout did not trip"


# ---------------------------------------------------------------------------
# forget + opt-out
# ---------------------------------------------------------------------------


def test_forget_user_round_trip(tmp_store: VoiceMemoryStore):
    _seed(
        tmp_store,
        1,
        {7: UserMemory(display_name="Alice", facts=["a", "b", "c"], vibe="warm")},
    )
    facts_removed, had_any, did_pop = tmp_store.forget_user(1, 7)
    assert had_any is True
    assert did_pop is True
    assert facts_removed == 3
    assert tmp_store.get_user_optional(1, 7) is None


def test_forget_empty_record_still_pops(tmp_store: VoiceMemoryStore):
    """A user record that exists but has no meaningful content (e.g. just a
    display_name from an earlier sync) is still popped, and `did_pop` reflects
    that so callers know to persist the change."""
    _seed(tmp_store, 1, {7: UserMemory(display_name="Alice")})
    facts_removed, had_any, did_pop = tmp_store.forget_user(1, 7)
    assert had_any is False
    assert did_pop is True
    assert facts_removed == 0


def test_forget_user_returns_did_pop_false_when_absent(tmp_store: VoiceMemoryStore):
    facts_removed, had_any, did_pop = tmp_store.forget_user(1, 7)
    assert did_pop is False
    assert had_any is False
    assert facts_removed == 0


def test_forget_guild_returns_user_count(tmp_store: VoiceMemoryStore):
    _seed(
        tmp_store,
        1,
        {
            7: UserMemory(display_name="Alice", facts=["a"]),
            8: UserMemory(display_name="Bob", facts=["b"]),
        },
    )
    removed = tmp_store.forget_guild(1)
    assert removed == 2
    assert tmp_store.get_user_optional(1, 7) is None


def test_set_opt_out_blocks_extraction(tmp_store: VoiceMemoryStore):
    tmp_store.set_opt_out(1, 7, True)
    assert tmp_store.is_opted_out(1, 7) is True
    result = {
        "users": {_speaker_label(7): {"add_facts": ["nope"]}},
        "channel": {"add_facts": []},
    }
    tmp_store.apply_extraction(1, result, present_user_ids={7})
    assert not tmp_store.get_user(1, 7).facts


def test_set_opt_out_can_be_undone(tmp_store: VoiceMemoryStore):
    tmp_store.set_opt_out(1, 7, True)
    tmp_store.set_opt_out(1, 7, False)
    assert tmp_store.is_opted_out(1, 7) is False


def test_module_singleton_is_present():
    assert isinstance(voice_memory.voice_memory_store, VoiceMemoryStore)
    assert voice_memory.voice_memory_store.path.name == "voice_memories.json"


def test_fact_dataclass_round_trip():
    f = Fact(text="plays guitar", source="extracted")
    d = f.to_dict()
    assert d["text"] == "plays guitar"
    assert "updated_at" in d
    assert d["source"] == "extracted"
    f2 = Fact.from_any(d)
    assert f2 is not None and f2.text == "plays guitar" and f2.source == "extracted"


def test_fact_from_any_handles_garbage():
    assert Fact.from_any(None) is None
    assert Fact.from_any("") is None
    assert Fact.from_any({}) is None
    assert Fact.from_any({"text": ""}) is None
