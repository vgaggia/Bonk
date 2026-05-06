from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src import log

from .schema import (
    SCHEMA_VERSION,
    VALID_CATEGORIES,
    Conjugation,
    Entry,
    ExampleSentence,
    GrammarRule,
    Meta,
    RecentMessage,
    SourceMessage,
    best_confidence,
    now_iso,
)
from .seed import build_full_seed

logger = log.setup_logger(__name__)

# Fields the website is allowed to mutate; bot's save() must not clobber these.
# (force_sync_requested_at is intentionally NOT here — bot owns it: it clears
# the field after processing a forced sync.)
WEBSITE_OWNED_META_FIELDS = ("sync_interval_hours", "auto_sync_enabled")

RECENT_CONTEXT_CAP = 10


def _short_hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:8]


def _new_entry_id(word: str, category: str) -> str:
    return f"e-{word.lower()}-{category}-{_short_hash(word + category + now_iso())}"


def _new_grammar_id(rule: str, detail: str) -> str:
    return f"g-{_short_hash(rule + detail)}-{_short_hash(now_iso())[:4]}"


def _new_example_id(conlang: str) -> str:
    return f"s-{_short_hash(conlang)}-{_short_hash(now_iso())[:4]}"


class ConlangDatabase:
    def __init__(self, path: Path):
        self.path = path
        self.meta = Meta()
        self.entries: List[Entry] = []
        self.grammar_rules: List[GrammarRule] = []
        self.example_sentences: List[ExampleSentence] = []
        self.conjugations: Dict[str, Conjugation] = {}
        self._save_lock = asyncio.Lock()

    # ---- Load ------------------------------------------------------------

    async def load(self) -> None:
        """Load from disk. If the file doesn't exist, write the seed first."""
        if not self.path.exists():
            logger.info("conlang DB not found at %s — writing seed", self.path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            seed = build_full_seed()
            self._populate_from_dict(seed)
            await self.save()
            return

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._populate_from_dict(data)
        except Exception:
            logger.exception("Failed to load conlang DB; refusing to overwrite. Fix the file.")
            raise

    def _populate_from_dict(self, data: Dict[str, Any]) -> None:
        self.meta = Meta.from_dict(data.get("meta") or {})
        self.entries = [Entry.from_dict(e) for e in (data.get("entries") or [])]
        self.grammar_rules = [
            GrammarRule.from_dict(r) for r in (data.get("grammar_rules") or [])
        ]
        self.example_sentences = [
            ExampleSentence.from_dict(s) for s in (data.get("example_sentences") or [])
        ]
        self.conjugations = {
            k: Conjugation.from_dict(v) for k, v in (data.get("conjugations") or {}).items()
        }

    # ---- Save ------------------------------------------------------------

    def _build_snapshot(self) -> Dict[str, Any]:
        # Recompute counts every save so they can never drift.
        self.meta.total_entries = len(self.entries)
        self.meta.confirmed_count = sum(1 for e in self.entries if e.confidence == "confirmed")
        self.meta.schema_version = SCHEMA_VERSION
        return {
            "meta": self.meta.to_dict(),
            "entries": [e.to_dict() for e in self.entries],
            "grammar_rules": [r.to_dict() for r in self.grammar_rules],
            "example_sentences": [s.to_dict() for s in self.example_sentences],
            "conjugations": {k: v.to_dict() for k, v in self.conjugations.items()},
        }

    async def save(self) -> None:
        """Atomically persist current state. The re-read of website-owned meta
        fields and the atomic write both happen inside _write_snapshot_atomic
        (which runs under the save lock in an executor) so there is no TOCTOU
        window where a website PATCH could be clobbered."""
        snapshot = self._build_snapshot()
        async with self._save_lock:
            disk_meta = await asyncio.get_running_loop().run_in_executor(
                None, self._write_snapshot_atomic, snapshot
            )
        if disk_meta is not None:
            for key in WEBSITE_OWNED_META_FIELDS:
                if key in disk_meta:
                    if key == "sync_interval_hours":
                        self.meta.sync_interval_hours = float(disk_meta[key])
                    elif key == "auto_sync_enabled":
                        self.meta.auto_sync_enabled = bool(disk_meta[key])

    def _write_snapshot_atomic(self, snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Re-read website-owned meta from disk, merge into snapshot, and
        atomically write. Returns the disk meta dict (so the caller can
        update in-memory state) or None on error / first write."""
        disk_meta: Optional[Dict[str, Any]] = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                try:
                    on_disk = json.loads(self.path.read_text(encoding="utf-8"))
                    disk_meta = on_disk.get("meta") or {}
                    for key in WEBSITE_OWNED_META_FIELDS:
                        if key in disk_meta:
                            snapshot["meta"][key] = disk_meta[key]
                except Exception:
                    logger.exception(
                        "Could not re-read on-disk meta before save; proceeding with in-memory copy"
                    )
            tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp_path.write_text(
                json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            os.replace(tmp_path, self.path)
        except Exception:
            logger.exception("Failed to save conlang DB to %s", self.path)
        return disk_meta

    # ---- Recent context (for analyzer cross-batch detection) -------------

    def push_recent(self, msgs: List[RecentMessage]) -> None:
        """Append messages to recent_context, capped at RECENT_CONTEXT_CAP (newest kept)."""
        combined = (self.meta.recent_context or []) + msgs
        self.meta.recent_context = combined[-RECENT_CONTEXT_CAP:]

    # ---- Lookups ---------------------------------------------------------

    def find_entry(self, word: str, category: str) -> Optional[Entry]:
        wl = word.casefold()
        cl = category.casefold()
        for e in self.entries:
            if e.word.casefold() == wl and e.category.casefold() == cl:
                return e
        return None

    def find_grammar_rule(self, rule: str, detail: str) -> Optional[GrammarRule]:
        rl = rule.casefold()
        dl = detail.casefold()
        for r in self.grammar_rules:
            if r.rule.casefold() == rl and r.detail.casefold() == dl:
                return r
        return None

    def find_example(self, conlang: str) -> Optional[ExampleSentence]:
        cl = conlang.casefold()
        for s in self.example_sentences:
            if s.conlang.casefold() == cl:
                return s
        return None

    # ---- Merge analyzer output -------------------------------------------

    def merge_analysis(self, result: Dict[str, Any]) -> Tuple[int, int, int, int, int]:
        """Merge a record_findings tool-call payload. Returns counts:
        (entries_added, entries_updated, examples_added, rules_added, entries_corrected)."""
        entries_added = entries_updated = examples_added = rules_added = entries_corrected = 0

        for raw in result.get("new_entries", []) or []:
            if not _valid_new_entry(raw):
                logger.warning("dropping malformed new_entry: %r", raw)
                continue
            word = str(raw["word"]).strip()
            category = str(raw["category"]).strip().lower()
            existing = self.find_entry(word, category)
            if existing is not None:
                if self._apply_update_to_entry(existing, raw):
                    entries_updated += 1
                continue
            new_e = Entry(
                id=_new_entry_id(word, category),
                word=word,
                category=category,
                meaning=str(raw.get("meaning", "")).strip(),
                confidence=_clamp_confidence(raw.get("confidence", "guess")),
                plural=_strip_or_none(raw.get("plural")),
                pronunciation=_strip_or_none(raw.get("pronunciation")),
                notes=str(raw.get("notes", "")).strip(),
                source_messages=_collect_sources(raw.get("source_messages")),
            )
            self.entries.append(new_e)
            entries_added += 1

        for raw in result.get("updated_entries", []) or []:
            word = str(raw.get("word", "")).strip()
            category = str(raw.get("category", "")).strip().lower()
            if not word or category not in VALID_CATEGORIES:
                logger.warning("dropping malformed updated_entry: %r", raw)
                continue
            existing = self.find_entry(word, category)
            if existing is None:
                logger.info("update target not found, skipping: %r %r", word, category)
                continue
            if self._apply_update_to_entry(existing, raw):
                entries_updated += 1

        for raw in result.get("corrections", []) or []:
            word = str(raw.get("word", "")).strip()
            category = str(raw.get("category", "")).strip().lower()
            action = str(raw.get("action", "")).strip().lower()
            if not word or category not in VALID_CATEGORIES or action not in ("delete", "update"):
                logger.warning("dropping malformed correction: %r", raw)
                continue
            sources = _collect_sources(raw.get("source_messages"))
            if not any(sm.author_is_jorn for sm in sources):
                logger.warning(
                    "dropping correction without Jorn source evidence: %r %r", word, category
                )
                continue
            existing = self.find_entry(word, category)
            if existing is None:
                logger.info("correction target not found, skipping: %r %r", word, category)
                continue
            if action == "delete":
                self.entries.remove(existing)
                logger.info(
                    "CORRECTION: deleted entry %r (%s) — %s",
                    word, category, raw.get("reasoning", ""),
                )
                entries_corrected += 1
            elif action == "update":
                changed = False
                if "confidence" in raw:
                    new_conf = _clamp_confidence(raw["confidence"])
                    if new_conf != existing.confidence:
                        existing.confidence = new_conf
                        changed = True
                new_meaning = raw.get("meaning")
                if isinstance(new_meaning, str) and new_meaning.strip():
                    if new_meaning.strip() != existing.meaning.strip():
                        existing.meaning = new_meaning.strip()
                        changed = True
                reasoning = raw.get("reasoning") or ""
                if reasoning.strip():
                    tag = f"[CORRECTED] {reasoning.strip()}"
                    if tag not in existing.notes:
                        existing.notes = (existing.notes + " | " if existing.notes else "") + tag
                        changed = True
                for sm in sources:
                    if not any(em.message_id == sm.message_id for em in existing.source_messages):
                        existing.source_messages.append(sm)
                        changed = True
                if changed:
                    existing.last_updated = now_iso()
                    entries_corrected += 1
                    logger.info(
                        "CORRECTION: updated entry %r (%s) — %s",
                        word, category, raw.get("reasoning", ""),
                    )

        for raw in result.get("new_examples", []) or []:
            conlang = str(raw.get("conlang", "")).strip()
            english = str(raw.get("english", "")).strip()
            if not conlang or not english:
                continue
            existing = self.find_example(conlang)
            if existing is not None:
                # Promote confidence if higher; attach extra source if provided.
                new_conf = _clamp_confidence(raw.get("confidence", existing.confidence))
                existing.confidence = best_confidence(existing.confidence, new_conf)
                continue
            sm_id = raw.get("source_message_id")
            sm = (
                SourceMessage(
                    message_id=str(sm_id),
                    snippet=str(raw.get("source_message_snippet", ""))[:500],
                    author_is_jorn=bool(raw.get("source_author_is_jorn", False)),
                )
                if sm_id
                else None
            )
            self.example_sentences.append(
                ExampleSentence(
                    id=_new_example_id(conlang),
                    conlang=conlang,
                    english=english,
                    confidence=_clamp_confidence(raw.get("confidence", "guess")),
                    source_message=sm,
                )
            )
            examples_added += 1

        for raw in result.get("new_grammar_rules", []) or []:
            rule = str(raw.get("rule", "")).strip()
            detail = str(raw.get("detail", "")).strip()
            if not rule or not detail:
                continue
            existing = self.find_grammar_rule(rule, detail)
            new_conf = _clamp_confidence(raw.get("confidence", "guess"))
            if existing is not None:
                existing.confidence = best_confidence(existing.confidence, new_conf)
                existing.last_updated = now_iso()
                continue
            self.grammar_rules.append(
                GrammarRule(
                    id=_new_grammar_id(rule, detail),
                    rule=rule,
                    detail=detail,
                    confidence=new_conf,
                )
            )
            rules_added += 1

        return entries_added, entries_updated, examples_added, rules_added, entries_corrected

    def _apply_update_to_entry(self, existing: Entry, raw: Dict[str, Any]) -> bool:
        changed = False
        # Confidence: monotonic upgrade only.
        if "confidence" in raw:
            new_conf = _clamp_confidence(raw["confidence"])
            promoted = best_confidence(existing.confidence, new_conf)
            if promoted != existing.confidence:
                existing.confidence = promoted
                changed = True
        # Meaning: only refine when current meaning is empty or analyzer flagged refinement.
        new_meaning = raw.get("meaning")
        if isinstance(new_meaning, str) and new_meaning.strip():
            if not existing.meaning.strip() or raw.get("refine_meaning") is True:
                if new_meaning.strip() != existing.meaning.strip():
                    existing.meaning = new_meaning.strip()
                    changed = True
        # Plural / pronunciation: fill if currently empty.
        for k in ("plural", "pronunciation"):
            v = raw.get(k)
            if isinstance(v, str) and v.strip() and not getattr(existing, k):
                setattr(existing, k, v.strip())
                changed = True
        # Notes: append reasoning if provided and not already present.
        reasoning = raw.get("reasoning") or ""
        if isinstance(reasoning, str) and reasoning.strip() and reasoning.strip() not in existing.notes:
            existing.notes = (existing.notes + " | " if existing.notes else "") + reasoning.strip()
            changed = True
        # Source messages: append novel ones.
        for sm in _collect_sources(raw.get("source_messages")):
            if not any(em.message_id == sm.message_id for em in existing.source_messages):
                existing.source_messages.append(sm)
                changed = True
        if changed:
            existing.last_updated = now_iso()
        return changed


# ---- Helpers ------------------------------------------------------------

def _strip_or_none(v: Any) -> Optional[str]:
    if not isinstance(v, str):
        return None
    s = v.strip()
    return s or None


def _clamp_confidence(c: Any) -> str:
    from .schema import CONFIDENCE_LADDER

    if isinstance(c, str) and c in CONFIDENCE_LADDER:
        return c
    return "guess"


def _collect_sources(raw: Any) -> List[SourceMessage]:
    out: List[SourceMessage] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, str):
            out.append(SourceMessage(message_id=item))
        elif isinstance(item, dict) and item.get("message_id"):
            out.append(
                SourceMessage(
                    message_id=str(item["message_id"]),
                    snippet=str(item.get("snippet", ""))[:500],
                    author_is_jorn=bool(item.get("author_is_jorn", False)),
                )
            )
    return out


def _valid_new_entry(raw: Any) -> bool:
    if not isinstance(raw, dict):
        return False
    word = raw.get("word")
    category = raw.get("category")
    return (
        isinstance(word, str)
        and word.strip()
        and isinstance(category, str)
        and category.strip().lower() in VALID_CATEGORIES
    )
