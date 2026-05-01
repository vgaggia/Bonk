"""Living per-guild × per-user memory for /listen.

A separate Haiku call periodically extracts facts/vibe/notes from recent voice
utterances and merges them into a JSON store on disk. The store is read on
every voice reply and a memory block is appended to the system prompt so Bonk
grows persistent context about who it's talking to.

Architecture:
- Per-guild × per-user keying. Same Discord user_id in two guilds = two
  independent memories (privacy).
- Speakers in extraction are addressed by stable `speaker_<user_id>` labels,
  never by display name, so duplicate nicknames cannot cross-attribute facts.
- Storage is JSON on disk with atomic rename on save (snapshot-then-executor
  pattern; never partial files, never a torn write of a mid-mutation dict).
- Per-fact metadata (`updated_at`, `source`) supports future audit/cleanup.
- Extraction is debounced and runs as a background task with a per-guild
  asyncio.Lock; LLM call is wrapped in `asyncio.wait_for` so failures never
  block the listen disable path.
- Failures (LLM down, JSON parse, schema mismatch) are logged and swallowed.
- A deterministic PII filter is the second line of defense against the model
  ignoring the prompt's "do not extract sensitive data" rule.

Privacy:
- The injection block carries hard "do not recite" rules. If a user asks what
  Bonk knows about anyone, the model is instructed to redirect to /memories.
- Users can opt out (`opted_out=True`); the worker filters them out before
  building extraction transcripts and `format_for_prompt` skips their data.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

USER_FACT_CAP = 30           # Soft cap; over this, extraction is asked to consolidate.
USER_FACT_TARGET = 25        # Consolidation aims for at most this many.
CHANNEL_FACT_CAP = 20
PROMPT_BLOCK_CHAR_CAP = 3000

# Extraction debouncing: only run if both conditions are met.
EXTRACTION_MIN_INTERVAL = 30.0   # seconds since last successful run
EXTRACTION_MIN_PENDING = 3       # new user utterances accumulated

# Cap on how many recent utterances the extractor sees.
EXTRACTION_TRANSCRIPT_LINES = 30

# Max wall time the extraction LLM call is allowed to take.
EXTRACTION_TIMEOUT = 30.0

# Per-fact text length cap (after PII filter).
FACT_MAX_CHARS = 280
VIBE_MAX_CHARS = 300
NOTES_MAX_CHARS = 600

DEFAULT_STORE_PATH = Path("data") / "voice_memories.json"

# Model used for memory extraction. Default to Haiku for cost; override via env.
MEMORY_EXTRACTION_MODEL = os.getenv(
    "MEMORY_EXTRACTION_MODEL", "claude-haiku-4-5-20251001"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Fact:
    """A single piece of remembered information with audit metadata."""

    text: str
    updated_at: str = field(default_factory=_now_iso)
    source: str = "extracted"   # extracted | manual | legacy

    def to_dict(self) -> Dict[str, Any]:
        return {"text": self.text, "updated_at": self.updated_at, "source": self.source}

    @classmethod
    def from_any(cls, data: Any) -> Optional["Fact"]:
        """Build a Fact from a string (legacy) or dict (current). Returns None on garbage."""
        if isinstance(data, str):
            text = data.strip()
            if not text:
                return None
            return cls(text=text, updated_at=_now_iso(), source="legacy")
        if isinstance(data, dict):
            text = str(data.get("text") or "").strip()
            if not text:
                return None
            return cls(
                text=text,
                updated_at=str(data.get("updated_at") or _now_iso()),
                source=str(data.get("source") or "extracted"),
            )
        return None


def _facts_from_any(items: Any) -> List[Fact]:
    if not isinstance(items, list):
        return []
    out: List[Fact] = []
    for item in items:
        f = Fact.from_any(item)
        if f is not None:
            out.append(f)
    return out


def _coerce_facts_in_place(facts: Any) -> List[Fact]:
    """Allow tests to construct UserMemory with raw strings; auto-wrap to Fact."""
    if not isinstance(facts, list):
        return []
    out: List[Fact] = []
    for item in facts:
        if isinstance(item, Fact):
            out.append(item)
        else:
            f = Fact.from_any(item)
            if f is not None:
                out.append(f)
    return out


@dataclass
class UserMemory:
    display_name: str = ""
    facts: List[Fact] = field(default_factory=list)
    vibe: str = ""
    notes: str = ""
    opted_out: bool = False

    def __post_init__(self) -> None:
        # Accept raw strings in tests/legacy callers; coerce to Fact.
        if self.facts:
            self.facts = _coerce_facts_in_place(self.facts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "display_name": self.display_name,
            "facts": [f.to_dict() for f in self.facts],
            "vibe": self.vibe,
            "notes": self.notes,
            "opted_out": bool(self.opted_out),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserMemory":
        return cls(
            display_name=str(data.get("display_name") or ""),
            facts=_facts_from_any(data.get("facts") or []),
            vibe=str(data.get("vibe") or ""),
            notes=str(data.get("notes") or ""),
            opted_out=bool(data.get("opted_out") or False),
        )

    def has_meaningful_content(self) -> bool:
        return bool(self.facts or self.vibe.strip() or self.notes.strip())


@dataclass
class ChannelMemory:
    facts: List[Fact] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.facts:
            self.facts = _coerce_facts_in_place(self.facts)

    def to_dict(self) -> Dict[str, Any]:
        return {"facts": [f.to_dict() for f in self.facts]}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChannelMemory":
        return cls(facts=_facts_from_any(data.get("facts") or []))


# ---------------------------------------------------------------------------
# Deterministic PII filter
# ---------------------------------------------------------------------------

# Compile patterns once. These are intentionally aggressive — we'd rather drop
# a borderline fact and let the model paraphrase it next time than store PII.
_RE_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Phone: at least 10 digits, allowing common separators
_RE_PHONE = re.compile(r"(?:\+?\d[\d\s().\-]{8,}\d)")
_RE_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_RE_CREDITCARD_LIKE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_RE_API_KEY = re.compile(
    r"\b(?:sk-[A-Za-z0-9_\-]{16,}|xox[bp]-[A-Za-z0-9\-]{10,}|gh[pousr]_[A-Za-z0-9]{20,}|[A-Za-z0-9]{32,})\b"
)
_RE_POSTAL_ADDRESS = re.compile(
    r"\b\d{1,6}\s+[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*\s+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Way|Court|Ct|Place|Pl|Terrace|Ter|Highway|Hwy)\b",
    re.IGNORECASE,
)


def _looks_sensitive(text: str) -> bool:
    """Best-effort regex match for common PII / secret shapes."""
    if not text:
        return False
    # Credit-card check: only trip on 13+ digits, optionally separated, AND luhn passes —
    # otherwise common sequences like "1234567890" and unrelated long IDs trip it.
    if _RE_EMAIL.search(text):
        return True
    if _RE_SSN.search(text):
        return True
    if _RE_PHONE.search(text):
        return True
    if _RE_API_KEY.search(text):
        return True
    if _RE_POSTAL_ADDRESS.search(text):
        return True
    cc_match = _RE_CREDITCARD_LIKE.search(text)
    if cc_match and _luhn_ok(re.sub(r"[ \-]", "", cc_match.group(0))):
        return True
    return False


def _luhn_ok(digits: str) -> bool:
    if not digits.isdigit() or not (13 <= len(digits) <= 19):
        return False
    total = 0
    parity = len(digits) % 2
    for i, ch in enumerate(digits):
        n = int(ch)
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


_SPEAKER_LABEL_RE = re.compile(r"^speaker_(\d+)$")


def _speaker_label(user_id: int) -> str:
    return f"speaker_{user_id}"


def _parse_speaker_label(label: str) -> Optional[int]:
    m = _SPEAKER_LABEL_RE.match(label.strip())
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


class VoiceMemoryStore:
    """JSON-backed per-guild × per-user memory."""

    def __init__(self, path: Path = DEFAULT_STORE_PATH) -> None:
        self.path = path
        # guild_id (int) -> {"users": {user_id (int) -> UserMemory}, "channel": ChannelMemory}
        self._data: Dict[int, Dict[str, Any]] = {}
        self._save_lock = asyncio.Lock()
        self._loaded = False

    # ---- Loading / saving ------------------------------------------------

    def load(self) -> None:
        """Synchronously load from disk. Safe to call repeatedly; idempotent."""
        if self._loaded:
            return
        self._loaded = True
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Failed to load voice memories from %s; starting fresh", self.path)
            return
        if not isinstance(raw, dict):
            logger.warning("Voice memory file %s has unexpected shape; starting fresh", self.path)
            return
        for guild_key, guild_blob in raw.items():
            try:
                guild_id = int(guild_key)
            except (TypeError, ValueError):
                continue
            if not isinstance(guild_blob, dict):
                continue
            users_blob = guild_blob.get("users") or {}
            users: Dict[int, UserMemory] = {}
            if isinstance(users_blob, dict):
                for user_key, user_blob in users_blob.items():
                    try:
                        user_id = int(user_key)
                    except (TypeError, ValueError):
                        continue
                    if isinstance(user_blob, dict):
                        users[user_id] = UserMemory.from_dict(user_blob)
            channel_blob = guild_blob.get("channel") or {}
            channel = (
                ChannelMemory.from_dict(channel_blob)
                if isinstance(channel_blob, dict)
                else ChannelMemory()
            )
            self._data[guild_id] = {"users": users, "channel": channel}
        logger.info("Loaded voice memories for %d guild(s) from %s", len(self._data), self.path)

    def _build_snapshot(self) -> Dict[str, Any]:
        """Build a JSON-ready dict from current state. MUST be called on the event loop
        (or otherwise serialized with respect to mutations) — produces an immutable
        snapshot the executor can serialize without races."""
        out: Dict[str, Any] = {}
        for guild_id, blob in self._data.items():
            users_out: Dict[str, Any] = {}
            for user_id, mem in blob["users"].items():
                users_out[str(user_id)] = mem.to_dict()
            out[str(guild_id)] = {
                "users": users_out,
                "channel": blob["channel"].to_dict(),
            }
        return out

    async def save(self) -> None:
        """Atomically persist current state to disk. Snapshot is built synchronously
        on the event loop (no races with sync mutators), then written via the
        executor under a lock that serializes concurrent saves."""
        snapshot = self._build_snapshot()
        async with self._save_lock:
            await asyncio.get_running_loop().run_in_executor(
                None, self._write_snapshot_atomic, snapshot
            )

    def _write_snapshot_atomic(self, snapshot: Dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp_path.write_text(
                json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            os.replace(tmp_path, self.path)
        except Exception:
            logger.exception("Failed to save voice memories to %s", self.path)

    # ---- Accessors -------------------------------------------------------

    def _ensure_guild(self, guild_id: int) -> Dict[str, Any]:
        blob = self._data.get(guild_id)
        if blob is None:
            blob = {"users": {}, "channel": ChannelMemory()}
            self._data[guild_id] = blob
        return blob

    def get_user(self, guild_id: int, user_id: int) -> UserMemory:
        blob = self._ensure_guild(guild_id)
        mem = blob["users"].get(user_id)
        if mem is None:
            mem = UserMemory()
            blob["users"][user_id] = mem
        return mem

    def get_user_optional(self, guild_id: int, user_id: int) -> Optional[UserMemory]:
        blob = self._data.get(guild_id)
        if blob is None:
            return None
        return blob["users"].get(user_id)

    def get_channel(self, guild_id: int) -> ChannelMemory:
        return self._ensure_guild(guild_id)["channel"]

    def update_display_name(self, guild_id: int, user_id: int, display_name: str) -> None:
        if not display_name:
            return
        mem = self.get_user(guild_id, user_id)
        if mem.display_name != display_name:
            mem.display_name = display_name

    def user_facts_count(self, guild_id: int, user_id: int) -> int:
        mem = self.get_user_optional(guild_id, user_id)
        return len(mem.facts) if mem else 0

    def is_opted_out(self, guild_id: int, user_id: int) -> bool:
        mem = self.get_user_optional(guild_id, user_id)
        return bool(mem and mem.opted_out)

    def set_opt_out(self, guild_id: int, user_id: int, value: bool) -> None:
        mem = self.get_user(guild_id, user_id)
        mem.opted_out = bool(value)

    def forget_user(self, guild_id: int, user_id: int) -> Tuple[int, bool, bool]:
        """Wipe one user's memory.

        Returns (facts_removed, had_meaningful_content, did_pop). `did_pop`
        is True if a record was actually present and removed (used by callers
        to decide whether to persist the wipe).
        """
        blob = self._data.get(guild_id)
        if not blob:
            return (0, False, False)
        mem = blob["users"].pop(user_id, None)
        if mem is None:
            return (0, False, False)
        had_any = mem.has_meaningful_content()
        return (len(mem.facts), had_any, True)

    def forget_guild(self, guild_id: int) -> int:
        """Wipe an entire guild. Returns count of users removed."""
        blob = self._data.pop(guild_id, None)
        if blob is None:
            return 0
        return len(blob["users"])

    # ---- Format for system prompt ---------------------------------------

    def format_for_prompt(
        self,
        guild_id: int,
        speaker_user_id: int,
        present_user_ids: List[int],
        speaker_display_name: str = "",
        present_display_names: Optional[Dict[int, str]] = None,
    ) -> str:
        """Render the memory block injected into the system prompt.

        Sections drop in cascade if the rendered block exceeds
        PROMPT_BLOCK_CHAR_CAP. Worst case: returns "" rather than a mid-cut
        rendering. The cascade preserves the speaker's vibe + first 3 facts
        as long as anything fits at all.
        """
        present_display_names = present_display_names or {}
        guild_blob = self._data.get(guild_id)
        if guild_blob is None:
            return ""

        # Skip opted-out speaker entirely.
        if self.is_opted_out(guild_id, speaker_user_id):
            return ""

        speaker_mem = guild_blob["users"].get(speaker_user_id)
        channel = guild_blob["channel"]

        def name_for(uid: int) -> str:
            live = present_display_names.get(uid)
            if live:
                return live
            stored = guild_blob["users"].get(uid)
            if stored and stored.display_name:
                return stored.display_name
            return f"User {uid}"

        # Filter out opted-out users from "others" entirely (they're still
        # present in the channel but we treat them as strangers).
        names_present = [name_for(uid) for uid in present_user_ids]
        speaker_name = speaker_display_name or name_for(speaker_user_id)

        speaker_facts: List[str] = []
        speaker_vibe = ""
        speaker_notes = ""
        if speaker_mem and not speaker_mem.opted_out:
            speaker_facts = [f.text for f in speaker_mem.facts]
            speaker_vibe = (speaker_mem.vibe or "").strip()
            speaker_notes = (speaker_mem.notes or "").strip()

        others: List[Dict[str, Any]] = []
        for uid in present_user_ids:
            if uid == speaker_user_id:
                continue
            if self.is_opted_out(guild_id, uid):
                continue
            mem = guild_blob["users"].get(uid)
            if mem is None or mem.opted_out:
                continue
            if not mem.has_meaningful_content():
                continue
            others.append(
                {
                    "name": name_for(uid),
                    "facts": [f.text for f in mem.facts],
                    "vibe": (mem.vibe or "").strip(),
                    "notes": (mem.notes or "").strip(),
                }
            )

        channel_facts = [f.text for f in channel.facts]

        # Drop cascade. Each level is monotonically more aggressive than the
        # last. Parameters:
        #   speaker_max_facts:  cap on speaker fact lines
        #   speaker_show_notes: include speaker notes line
        #   others_max:         cap on number of other-user blocks
        #   others_max_facts:   cap on facts per other-user block
        #   others_show_notes:  include other-user notes line
        #   channel_max_facts:  cap on channel fact lines
        #   line_char_cap:      truncate each rendered line text to this length
        drop_levels: List[Dict[str, Any]] = [
            # 0: full
            {"speaker_max_facts": 30, "speaker_show_notes": True,
             "others_max": None, "others_max_facts": 5, "others_show_notes": True,
             "channel_max_facts": 5, "line_char_cap": None},
            # 1: drop others' notes
            {"speaker_max_facts": 30, "speaker_show_notes": True,
             "others_max": None, "others_max_facts": 5, "others_show_notes": False,
             "channel_max_facts": 5, "line_char_cap": None},
            # 2: drop others' extra facts past 3
            {"speaker_max_facts": 30, "speaker_show_notes": True,
             "others_max": None, "others_max_facts": 3, "others_show_notes": False,
             "channel_max_facts": 5, "line_char_cap": None},
            # 3: drop channel extra past 3
            {"speaker_max_facts": 30, "speaker_show_notes": True,
             "others_max": None, "others_max_facts": 3, "others_show_notes": False,
             "channel_max_facts": 3, "line_char_cap": None},
            # 4: drop speaker notes
            {"speaker_max_facts": 30, "speaker_show_notes": False,
             "others_max": None, "others_max_facts": 3, "others_show_notes": False,
             "channel_max_facts": 3, "line_char_cap": None},
            # 5: cap each line at 100 chars
            {"speaker_max_facts": 30, "speaker_show_notes": False,
             "others_max": None, "others_max_facts": 3, "others_show_notes": False,
             "channel_max_facts": 3, "line_char_cap": 100},
            # 6: limit others to 2 blocks
            {"speaker_max_facts": 30, "speaker_show_notes": False,
             "others_max": 2, "others_max_facts": 2, "others_show_notes": False,
             "channel_max_facts": 2, "line_char_cap": 100},
            # 7: speaker + at most 1 other, 1 fact each
            {"speaker_max_facts": 5, "speaker_show_notes": False,
             "others_max": 1, "others_max_facts": 1, "others_show_notes": False,
             "channel_max_facts": 1, "line_char_cap": 100},
            # 8: speaker only, 3 facts
            {"speaker_max_facts": 3, "speaker_show_notes": False,
             "others_max": 0, "others_max_facts": 0, "others_show_notes": False,
             "channel_max_facts": 0, "line_char_cap": 100},
            # 9: speaker only, 1 fact
            {"speaker_max_facts": 1, "speaker_show_notes": False,
             "others_max": 0, "others_max_facts": 0, "others_show_notes": False,
             "channel_max_facts": 0, "line_char_cap": 80},
        ]

        for level in drop_levels:
            rendered = self._render_block(
                names_present=names_present,
                speaker_name=speaker_name,
                speaker_facts=speaker_facts,
                speaker_vibe=speaker_vibe,
                speaker_notes=speaker_notes,
                others=others,
                channel_facts=channel_facts,
                **level,
            )
            if len(rendered) <= PROMPT_BLOCK_CHAR_CAP:
                return rendered

        # Last resort: speaker name + vibe only, or empty.
        if speaker_vibe:
            tiny_lines = [
                INJECTION_HEADER.strip(),
                "",
                f"Speaking now: {speaker_name}",
                f"Vibe: {speaker_vibe[:120]}",
                "",
            ]
            tiny = "\n".join(tiny_lines).rstrip() + "\n"
            if len(tiny) <= PROMPT_BLOCK_CHAR_CAP:
                return tiny
        return ""

    def _render_block(
        self,
        names_present: List[str],
        speaker_name: str,
        speaker_facts: List[str],
        speaker_vibe: str,
        speaker_notes: str,
        others: List[Dict[str, Any]],
        channel_facts: List[str],
        speaker_max_facts: int,
        speaker_show_notes: bool,
        others_max: Optional[int],
        others_max_facts: int,
        others_show_notes: bool,
        channel_max_facts: int,
        line_char_cap: Optional[int],
    ) -> str:
        def cap(s: str) -> str:
            if line_char_cap is None or not s:
                return s
            return (s[: line_char_cap - 1] + "…") if len(s) > line_char_cap else s

        speaker_facts_to_show = speaker_facts[:max(0, speaker_max_facts)]
        show_speaker_notes = speaker_show_notes and bool(speaker_notes.strip())
        others_render = others if others_max is None else others[: max(0, others_max)]
        channel_to_show = channel_facts[: max(0, channel_max_facts)]

        anything_for_speaker = bool(
            speaker_facts_to_show or speaker_vibe or show_speaker_notes
        )

        # Produce useful "others" sections only.
        rendered_others: List[List[str]] = []
        for block in others_render:
            block_facts = block["facts"][: max(0, others_max_facts)]
            show_notes = others_show_notes and bool(block["notes"].strip())
            if not (block_facts or block["vibe"] or show_notes):
                continue
            sub: List[str] = [f"About {block['name']}:"]
            for f in block_facts:
                sub.append(f"- {cap(f)}")
            if block["vibe"]:
                sub.append(f"Vibe: {cap(block['vibe'])}")
            if show_notes:
                sub.append(f"Notes: {cap(block['notes'])}")
            rendered_others.append(sub)

        if not (anything_for_speaker or rendered_others or channel_to_show):
            return ""

        lines: List[str] = []
        lines.append(INJECTION_HEADER.strip())
        lines.append("")
        if names_present:
            lines.append(f"Currently in voice chat: {', '.join(names_present)}")
        lines.append(f"Speaking now: {speaker_name}")
        lines.append("")

        if anything_for_speaker:
            lines.append(f"About {speaker_name} (the speaker):")
            for f in speaker_facts_to_show:
                lines.append(f"- {cap(f)}")
            if speaker_vibe:
                lines.append(f"Vibe: {cap(speaker_vibe)}")
            if show_speaker_notes:
                lines.append(f"Notes: {cap(speaker_notes)}")
            lines.append("")

        for sub in rendered_others:
            lines.extend(sub)
            lines.append("")

        if channel_to_show:
            lines.append("Channel context:")
            for f in channel_to_show:
                lines.append(f"- {cap(f)}")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    # ---- Apply extraction result ----------------------------------------

    def apply_extraction(
        self,
        guild_id: int,
        result: Dict[str, Any],
        present_user_ids: Set[int],
    ) -> Dict[str, int]:
        """Merge an extraction JSON result into the store.

        `present_user_ids` is the authoritative set of user_ids the extractor
        was allowed to write about. Result keys must be `speaker_<id>` labels;
        any user_id not in this set is silently dropped (we never invent users
        or attribute facts to absent ones).

        Returns a small dict of stats: {"users_updated", "facts_added", "channel_facts_added"}.
        """
        stats = {"users_updated": 0, "facts_added": 0, "channel_facts_added": 0}
        if not isinstance(result, dict):
            return stats

        users_blob = result.get("users") or {}
        if isinstance(users_blob, dict):
            for raw_label, payload in users_blob.items():
                if not isinstance(payload, dict):
                    continue
                user_id = _parse_speaker_label(str(raw_label))
                if user_id is None:
                    logger.debug(
                        "Skipping extraction with non-id label %r", raw_label
                    )
                    continue
                if user_id not in present_user_ids:
                    logger.debug(
                        "Skipping extraction for user_id %s (not in present users)", user_id
                    )
                    continue
                # Refuse to write facts for opted-out users even if the model output them.
                if self.is_opted_out(guild_id, user_id):
                    logger.debug(
                        "Skipping extraction for opted-out user_id %s", user_id
                    )
                    continue

                mem = self.get_user(guild_id, user_id)
                changed = False

                # PII-filter all incoming text.
                replace_facts = _clean_fact_strings(payload.get("replace_facts"))
                add_facts = _clean_fact_strings(payload.get("add_facts"))

                if replace_facts:
                    new_facts = [
                        Fact(text=t, updated_at=_now_iso(), source="extracted")
                        for t in replace_facts[:USER_FACT_TARGET]
                    ]
                    if [f.text for f in new_facts] != [f.text for f in mem.facts]:
                        mem.facts = new_facts
                        stats["facts_added"] += len(new_facts)
                        changed = True
                elif add_facts:
                    existing_texts = [f.text for f in mem.facts]
                    new_texts = _dedup_against(existing_texts, add_facts)
                    if new_texts:
                        for t in new_texts:
                            mem.facts.append(
                                Fact(text=t, updated_at=_now_iso(), source="extracted")
                            )
                        if len(mem.facts) > USER_FACT_CAP:
                            mem.facts = mem.facts[-USER_FACT_CAP:]
                        stats["facts_added"] += len(new_texts)
                        changed = True

                vibe = payload.get("vibe")
                if isinstance(vibe, str) and vibe.strip():
                    candidate = vibe.strip()[:VIBE_MAX_CHARS]
                    if not _looks_sensitive(candidate):
                        mem.vibe = candidate
                        changed = True

                notes = payload.get("notes")
                if isinstance(notes, str) and notes.strip():
                    candidate = notes.strip()[:NOTES_MAX_CHARS]
                    if not _looks_sensitive(candidate):
                        mem.notes = candidate
                        changed = True

                if changed:
                    stats["users_updated"] += 1

        channel_blob = result.get("channel") or {}
        if isinstance(channel_blob, dict):
            channel = self.get_channel(guild_id)
            replace_facts = _clean_fact_strings(channel_blob.get("replace_facts"))
            add_facts = _clean_fact_strings(channel_blob.get("add_facts"))
            if replace_facts:
                new_facts = [
                    Fact(text=t, updated_at=_now_iso(), source="extracted")
                    for t in replace_facts[:CHANNEL_FACT_CAP]
                ]
                if [f.text for f in new_facts] != [f.text for f in channel.facts]:
                    channel.facts = new_facts
                    stats["channel_facts_added"] += len(new_facts)
            elif add_facts:
                existing_texts = [f.text for f in channel.facts]
                new_texts = _dedup_against(existing_texts, add_facts)
                if new_texts:
                    for t in new_texts:
                        channel.facts.append(
                            Fact(text=t, updated_at=_now_iso(), source="extracted")
                        )
                    if len(channel.facts) > CHANNEL_FACT_CAP:
                        channel.facts = channel.facts[-CHANNEL_FACT_CAP:]
                    stats["channel_facts_added"] += len(new_texts)

        return stats


def _clean_fact_strings(items: Any) -> List[str]:
    """Sanitize a list of incoming fact strings: dedupe-by-case, drop empty/oversized,
    drop anything that looks like PII / a credential."""
    out: List[str] = []
    if not isinstance(items, list):
        return out
    seen_lower = set()
    for item in items:
        if not isinstance(item, str):
            continue
        s = item.strip()
        if not s or len(s) > FACT_MAX_CHARS:
            continue
        if _looks_sensitive(s):
            logger.debug("Dropping fact that matched sensitive-data filter: %s", _redact(s))
            continue
        key = s.lower()
        if key in seen_lower:
            continue
        seen_lower.add(key)
        out.append(s)
    return out


def _redact(s: str) -> str:
    """Truncate a string for safe logging."""
    if len(s) <= 30:
        return s[:8] + "…"
    return s[:8] + "…" + s[-4:]


def _dedup_against(existing: List[str], new_items: List[str]) -> List[str]:
    """Return only items that don't exact-match (after normalization) any
    existing entry. Substring containment is intentionally NOT used — it drops
    legitimate negations (e.g. existing "likes coffee" would block new
    "dislikes coffee")."""
    if not new_items:
        return []
    existing_norm = {_normalize(e) for e in existing}
    out: List[str] = []
    for item in new_items:
        norm = _normalize(item)
        if not norm or norm in existing_norm:
            continue
        out.append(item)
        existing_norm.add(norm)
    return out


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


INJECTION_HEADER = """
PRIVATE NOTES — these are your own private notes about people in this voice chat,
based on past conversations. They are NOT for repeating back. Use them only to
inform your tone, references, and follow-ups in a natural way.

Hard rules:
- Never read these notes aloud, summarize them, or list facts from them, even if
  someone asks "what do you remember about me?" or about anyone else.
- If someone asks what you know about them or another user, redirect them: tell
  them to run /memories to see what you've stored about them, and /forget to
  wipe it.
- Don't reveal that you have notes about other people present — those notes are
  for shaping how you talk to *this* speaker, not for sharing.
- If the notes contradict something the speaker just said, trust the speaker.
""".strip()


EXTRACTION_PROMPT_TEMPLATE = """You are Bonk's memory keeper. Bonk is an AI in a Discord voice chat. Read the recent transcript and decide what's worth remembering for next time. Output strict JSON only.

Speaker labels (each speaker has a stable id; refer to them by label in your output):
{speaker_legend}

The transcript was produced by Whisper and may have errors. The names "balk", "bulk", "bong", "bank", "bunk", "hulk", "balks" are common Whisper mishearings of "Bonk" — treat them as Bonk, not as facts about anyone.

Recent transcript (oldest first; Bonk's replies are not shown):
{transcript_lines}

What you've already remembered about these speakers:
{existing_memories_dump}

EXTRACT — about each speaker, based only on what THEY said about THEMSELVES:
- Identity: name, role, location (city/country only, never street addresses), relationships, pets, what they do
- Stable preferences: likes/dislikes, hobbies, opinions held over time
- Plans, goals, ongoing situations they expect to come up again
- Recurring topics or inside jokes worth carrying forward

UPDATE Bonk's vibe (one line per speaker, optional): Bonk's developing feeling about this person. Examples: "energetic, always has a story", "kind of grumpy but warm underneath", "we click — sarcasm lands well". Vibes are Bonk's opinions, not facts the speaker stated.

UPDATE notes (one short paragraph per speaker, optional): inside jokes, running gags, context that doesn't fit as a discrete fact.

CHANNEL FACTS — group-level things that apply to the whole conversation, not one person.

DO NOT EXTRACT:
- Anything Bonk itself said or any "facts" about Bonk
- Greetings, filler, momentary reactions ("haha", "nice", "yeah okay")
- Things only true in this exact moment ("they just laughed", "they're typing")
- Facts about person A that person B reported second-hand
- Anything that contradicts an existing fact unless the speaker explicitly retracted it
- Information from low-confidence garbled transcript fragments — when in doubt, skip
- SENSITIVE personal data (full street addresses, phone numbers, email addresses, credit cards, SSNs, passwords, API keys) — skip even if shared. A general city/country IS fine ("lives in Sydney"); a street address is NOT.

DEDUP: skip facts that already appear in the existing memories. If two facts conflict, prefer the newer one and skip the old.

OVERFLOW: for any speaker label listed in {over_cap_labels}, do NOT use "add_facts" — instead use "replace_facts" with a consolidated list of at most {target_count} facts that preserves the most useful information. Drop trivia first.

OUTPUT FORMAT — strict JSON, no prose, no markdown fences. Schema:
{{
  "users": {{
    "speaker_<id>": {{
      "add_facts": ["short, one-line, present tense"],
      "replace_facts": ["..."],
      "vibe": "<new vibe line, omit field if unchanged>",
      "notes": "<replacement notes, omit field if unchanged>"
    }}
  }},
  "channel": {{
    "add_facts": ["..."]
  }}
}}

EXAMPLES (these are illustrative — do NOT include their speakers in your real output):

Example 1 (only filler / greetings):
Transcript:
[Alice (speaker_111111)] hi
[Bob (speaker_222222)] hey
Output:
{{"users": {{}}, "channel": {{"add_facts": []}}}}

Example 2 (a stable first-person fact):
Transcript:
[Alice (speaker_111111)] my dog is named Pickle and he's a corgi
Output:
{{"users": {{"speaker_111111": {{"add_facts": ["has a corgi named Pickle"]}}}}, "channel": {{"add_facts": []}}}}

Example 3 (second-hand attribution is rejected):
Transcript:
[Alice (speaker_111111)] Bob hates pickles
[Bob (speaker_222222)] no I don't
Output:
{{"users": {{}}, "channel": {{"add_facts": []}}}}

Example 4 (sensitive data is dropped):
Transcript:
[Alice (speaker_111111)] my email is alice@example.com and my number is 555-867-5309
Output:
{{"users": {{}}, "channel": {{"add_facts": []}}}}

If nothing in the real transcript is worth saving, output: {{"users": {{}}, "channel": {{"add_facts": []}}}}"""


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def _build_speaker_legend(present_user_ids: List[int], display_names_by_id: Dict[int, str]) -> str:
    if not present_user_ids:
        return "(no speakers listed)"
    lines: List[str] = []
    for uid in present_user_ids:
        name = display_names_by_id.get(uid, f"User {uid}")
        lines.append(f"- {_speaker_label(uid)} = {name}")
    return "\n".join(lines)


def _build_existing_dump(
    store: VoiceMemoryStore, guild_id: int, present_user_ids: List[int],
    display_names_by_id: Dict[int, str],
) -> str:
    blob = store._data.get(guild_id)
    if blob is None:
        return "(no existing memories)"
    parts: List[str] = []
    for uid in present_user_ids:
        mem = blob["users"].get(uid)
        if not mem or mem.opted_out:
            continue
        if not mem.has_meaningful_content():
            continue
        name = display_names_by_id.get(uid) or mem.display_name or f"User {uid}"
        sub: List[str] = [f"{_speaker_label(uid)} ({name}):"]
        for f in mem.facts:
            sub.append(f"  - {f.text}")
        if mem.vibe:
            sub.append(f"  vibe: {mem.vibe}")
        if mem.notes:
            sub.append(f"  notes: {mem.notes}")
        parts.append("\n".join(sub))
    channel = blob["channel"]
    if channel.facts:
        sub = ["channel:"]
        for f in channel.facts:
            sub.append(f"  - {f.text}")
        parts.append("\n".join(sub))
    if not parts:
        return "(no existing memories yet for these speakers)"
    return "\n\n".join(parts)


def _parse_extraction_json(text: str) -> Dict[str, Any]:
    """Parse the model's JSON output, tolerantly. Returns {} on failure."""
    if not text:
        return {}
    s = text.strip()
    # Strip common code-fence wrappers if the model ignored instructions.
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s)
    # If there's any leading/trailing prose, try to extract the first balanced {...} block.
    if not s.startswith("{"):
        match = re.search(r"\{.*\}", s, re.DOTALL)
        if match:
            s = match.group(0)
    try:
        result = json.loads(s)
    except Exception:
        logger.warning("Memory extraction returned invalid JSON; ignoring batch")
        return {}
    if not isinstance(result, dict):
        return {}
    return result


def extract_memories(
    anthropic_client: Any,
    model: str,
    transcript_user_lines: List[str],
    present_user_ids: List[int],
    display_names_by_id: Dict[int, str],
    existing_dump: str,
    over_cap_user_ids: List[int],
) -> Dict[str, Any]:
    """Single Haiku call. Returns parsed extraction JSON, or {} on any failure."""
    if not transcript_user_lines:
        return {}
    if anthropic_client is None:
        return {}

    speaker_legend = _build_speaker_legend(present_user_ids, display_names_by_id)
    over_cap_repr = ", ".join(_speaker_label(u) for u in over_cap_user_ids) if over_cap_user_ids else "(none)"
    transcript_block = "\n".join(transcript_user_lines)
    prompt = EXTRACTION_PROMPT_TEMPLATE.format(
        speaker_legend=speaker_legend,
        transcript_lines=transcript_block,
        existing_memories_dump=existing_dump or "(no existing memories)",
        over_cap_labels=over_cap_repr,
        target_count=USER_FACT_TARGET,
    )

    try:
        response = anthropic_client.messages.create(
            model=model,
            max_tokens=1500,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception:
        logger.exception("Memory extraction call failed")
        return {}

    try:
        text = response.content[0].text
    except Exception:
        logger.warning("Memory extraction response had unexpected shape")
        return {}

    return _parse_extraction_json(text)


# ---------------------------------------------------------------------------
# Per-guild background extraction worker
# ---------------------------------------------------------------------------


class ExtractionWorker:
    """Debounced background extractor bound to one guild's listen session."""

    def __init__(
        self,
        guild_id: int,
        store: VoiceMemoryStore,
        anthropic_client: Any,
        model: str,
        timeout: float = EXTRACTION_TIMEOUT,
    ) -> None:
        self.guild_id = guild_id
        self.store = store
        self.anthropic_client = anthropic_client
        self.model = model
        self.timeout = timeout
        self._lock = asyncio.Lock()
        self._pending_count = 0
        self._last_run_at = 0.0

    def note_new_user_utterance(self) -> None:
        self._pending_count += 1

    async def maybe_run(
        self,
        transcript_user_lines: List[str],
        present_user_ids: List[int],
        display_names_by_id: Dict[int, str],
    ) -> None:
        """Run extraction if debounce thresholds are satisfied."""
        now = time.time()
        if self._pending_count < EXTRACTION_MIN_PENDING:
            return
        if now - self._last_run_at < EXTRACTION_MIN_INTERVAL:
            return
        if self._lock.locked():
            return  # one in flight; the next utterance will retry
        await self._run(transcript_user_lines, present_user_ids, display_names_by_id)

    async def flush(
        self,
        transcript_user_lines: List[str],
        present_user_ids: List[int],
        display_names_by_id: Dict[int, str],
    ) -> None:
        """Force a final run regardless of throttle (called on /listen disable)."""
        if not transcript_user_lines or self._pending_count == 0:
            return
        await self._run(transcript_user_lines, present_user_ids, display_names_by_id)

    async def _run(
        self,
        transcript_user_lines: List[str],
        present_user_ids: List[int],
        display_names_by_id: Dict[int, str],
    ) -> None:
        if self._lock.locked():
            return
        async with self._lock:
            try:
                # Snapshot inputs and reset pending counter immediately.
                lines = list(transcript_user_lines[-EXTRACTION_TRANSCRIPT_LINES:])
                uids = list(present_user_ids)
                names = dict(display_names_by_id)
                self._pending_count = 0
                self._last_run_at = time.time()

                over_cap_user_ids: List[int] = [
                    uid for uid in uids
                    if self.store.user_facts_count(self.guild_id, uid) >= USER_FACT_CAP
                ]

                existing_dump = _build_existing_dump(self.store, self.guild_id, uids, names)

                loop = asyncio.get_running_loop()
                try:
                    result = await asyncio.wait_for(
                        loop.run_in_executor(
                            None,
                            extract_memories,
                            self.anthropic_client,
                            self.model,
                            lines,
                            uids,
                            names,
                            existing_dump,
                            over_cap_user_ids,
                        ),
                        timeout=self.timeout,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "Voice memory extraction timed out after %.1fs (guild %s)",
                        self.timeout,
                        self.guild_id,
                    )
                    return
                if not result:
                    return
                stats = self.store.apply_extraction(self.guild_id, result, set(uids))
                if stats["users_updated"] or stats["facts_added"] or stats["channel_facts_added"]:
                    logger.info(
                        "Voice memory extraction (guild %s): %s",
                        self.guild_id,
                        stats,
                    )
                    await self.store.save()
            except Exception:
                logger.exception("Voice memory extraction worker crashed (guild %s)", self.guild_id)


# ---------------------------------------------------------------------------
# Module singleton
# ---------------------------------------------------------------------------


voice_memory_store = VoiceMemoryStore()
voice_memory_store.load()


__all__ = [
    "Fact",
    "UserMemory",
    "ChannelMemory",
    "VoiceMemoryStore",
    "ExtractionWorker",
    "INJECTION_HEADER",
    "EXTRACTION_PROMPT_TEMPLATE",
    "PROMPT_BLOCK_CHAR_CAP",
    "USER_FACT_CAP",
    "USER_FACT_TARGET",
    "CHANNEL_FACT_CAP",
    "EXTRACTION_TIMEOUT",
    "EXTRACTION_MIN_PENDING",
    "EXTRACTION_MIN_INTERVAL",
    "EXTRACTION_TRANSCRIPT_LINES",
    "MEMORY_EXTRACTION_MODEL",
    "extract_memories",
    "voice_memory_store",
]
