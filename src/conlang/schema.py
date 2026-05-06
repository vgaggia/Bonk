from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1

CONFIDENCE_LADDER = ("unknown", "guess", "uncertain", "medium", "high", "confirmed")
CONFIDENCE_RANK = {c: i for i, c in enumerate(CONFIDENCE_LADDER)}

VALID_CATEGORIES = (
    "noun",
    "verb",
    "adjective",
    "adverb",
    "pronoun",
    "particle",
    "emotion",
    "slang",
    "interjection",
    "other",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class SourceMessage:
    message_id: str
    snippet: str = ""
    author_is_jorn: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "snippet": self.snippet,
            "author_is_jorn": self.author_is_jorn,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SourceMessage":
        return cls(
            message_id=str(d.get("message_id", "")),
            snippet=str(d.get("snippet", "")),
            author_is_jorn=bool(d.get("author_is_jorn", False)),
        )


@dataclass
class Entry:
    id: str
    word: str
    category: str
    meaning: str
    confidence: str = "unknown"
    plural: Optional[str] = None
    pronunciation: Optional[str] = None
    notes: str = ""
    source_messages: List[SourceMessage] = field(default_factory=list)
    first_seen: str = field(default_factory=now_iso)
    last_updated: str = field(default_factory=now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "word": self.word,
            "category": self.category,
            "meaning": self.meaning,
            "confidence": self.confidence,
            "plural": self.plural,
            "pronunciation": self.pronunciation,
            "notes": self.notes,
            "source_messages": [s.to_dict() for s in self.source_messages],
            "first_seen": self.first_seen,
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Entry":
        return cls(
            id=str(d["id"]),
            word=str(d["word"]),
            category=str(d.get("category", "other")),
            meaning=str(d.get("meaning", "")),
            confidence=str(d.get("confidence", "unknown")),
            plural=d.get("plural"),
            pronunciation=d.get("pronunciation"),
            notes=str(d.get("notes", "")),
            source_messages=[SourceMessage.from_dict(s) for s in d.get("source_messages", [])],
            first_seen=str(d.get("first_seen", now_iso())),
            last_updated=str(d.get("last_updated", now_iso())),
        )


@dataclass
class GrammarRule:
    id: str
    rule: str
    detail: str
    confidence: str = "unknown"
    first_seen: str = field(default_factory=now_iso)
    last_updated: str = field(default_factory=now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "rule": self.rule,
            "detail": self.detail,
            "confidence": self.confidence,
            "first_seen": self.first_seen,
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "GrammarRule":
        return cls(
            id=str(d["id"]),
            rule=str(d.get("rule", "")),
            detail=str(d.get("detail", "")),
            confidence=str(d.get("confidence", "unknown")),
            first_seen=str(d.get("first_seen", now_iso())),
            last_updated=str(d.get("last_updated", now_iso())),
        )


@dataclass
class ExampleSentence:
    id: str
    conlang: str
    english: str
    confidence: str = "unknown"
    source_message: Optional[SourceMessage] = None
    timestamp: str = field(default_factory=now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "conlang": self.conlang,
            "english": self.english,
            "confidence": self.confidence,
            "source_message": self.source_message.to_dict() if self.source_message else None,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ExampleSentence":
        sm = d.get("source_message")
        return cls(
            id=str(d["id"]),
            conlang=str(d.get("conlang", "")),
            english=str(d.get("english", "")),
            confidence=str(d.get("confidence", "unknown")),
            source_message=SourceMessage.from_dict(sm) if isinstance(sm, dict) else None,
            timestamp=str(d.get("timestamp", now_iso())),
        )


@dataclass
class Conjugation:
    infinitive: str
    forms: Dict[str, str] = field(default_factory=dict)
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"infinitive": self.infinitive, "forms": self.forms, "notes": self.notes}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Conjugation":
        return cls(
            infinitive=str(d.get("infinitive", "")),
            forms={k: str(v) for k, v in (d.get("forms") or {}).items()},
            notes=str(d.get("notes", "")),
        )


@dataclass
class RecentMessage:
    """A recent message kept in meta.recent_context (capped to ~10) so the
    analyzer can spot cross-batch patterns like 'Jorn denies in N, confirms in N+1'."""

    message_id: str
    author_is_jorn: bool
    author_name: str
    content: str
    timestamp: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "author_is_jorn": self.author_is_jorn,
            "author_name": self.author_name,
            "content": self.content,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RecentMessage":
        return cls(
            message_id=str(d.get("message_id", "")),
            author_is_jorn=bool(d.get("author_is_jorn", False)),
            author_name=str(d.get("author_name", "")),
            content=str(d.get("content", "")),
            timestamp=str(d.get("timestamp", "")),
        )


@dataclass
class Meta:
    schema_version: int = SCHEMA_VERSION
    last_updated: Optional[str] = None
    last_message_id: Optional[str] = None
    sync_interval_hours: float = 6.0
    auto_sync_enabled: bool = True
    force_sync_requested_at: Optional[str] = None
    guild_id: Optional[str] = None
    channel_id: Optional[str] = None
    total_entries: int = 0
    confirmed_count: int = 0
    recent_context: List[RecentMessage] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "last_updated": self.last_updated,
            "last_message_id": self.last_message_id,
            "sync_interval_hours": self.sync_interval_hours,
            "auto_sync_enabled": self.auto_sync_enabled,
            "force_sync_requested_at": self.force_sync_requested_at,
            "guild_id": self.guild_id,
            "channel_id": self.channel_id,
            "total_entries": self.total_entries,
            "confirmed_count": self.confirmed_count,
            "recent_context": [m.to_dict() for m in self.recent_context],
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Meta":
        return cls(
            schema_version=int(d.get("schema_version", SCHEMA_VERSION)),
            last_updated=d.get("last_updated"),
            last_message_id=d.get("last_message_id"),
            sync_interval_hours=float(d.get("sync_interval_hours", 6.0)),
            auto_sync_enabled=bool(d.get("auto_sync_enabled", True)),
            force_sync_requested_at=d.get("force_sync_requested_at"),
            guild_id=d.get("guild_id"),
            channel_id=d.get("channel_id"),
            total_entries=int(d.get("total_entries", 0)),
            confirmed_count=int(d.get("confirmed_count", 0)),
            recent_context=[
                RecentMessage.from_dict(m) for m in (d.get("recent_context") or [])
            ],
        )


def confidence_at_least(a: str, floor: str) -> bool:
    return CONFIDENCE_RANK.get(a, -1) >= CONFIDENCE_RANK.get(floor, 99)


def best_confidence(a: str, b: str) -> str:
    if CONFIDENCE_RANK.get(a, -1) >= CONFIDENCE_RANK.get(b, -1):
        return a
    return b
