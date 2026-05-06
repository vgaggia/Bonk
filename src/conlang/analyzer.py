from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List

from src import log

from .database import ConlangDatabase
from .schema import VALID_CATEGORIES

logger = log.setup_logger(__name__)

DEFAULT_MODEL = "claude-opus-4-6"
ANALYZER_TIMEOUT_SECONDS = 120
MAX_OUTPUT_TOKENS = 8000

TOOL_DEFINITION = {
    "name": "record_findings",
    "description": (
        "Record extracted vocabulary, refinements, examples, and grammar rules from "
        "the new messages. Only include items with direct evidence in the messages."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "new_entries": {
                "type": "array",
                "description": "Words/phrases not yet in the dictionary that now have evidence.",
                "items": {
                    "type": "object",
                    "properties": {
                        "word": {"type": "string"},
                        "category": {"type": "string", "enum": list(VALID_CATEGORIES)},
                        "meaning": {"type": "string"},
                        "confidence": {
                            "type": "string",
                            "enum": ["unknown", "guess", "uncertain", "medium", "high", "confirmed"],
                        },
                        "plural": {"type": "string"},
                        "pronunciation": {"type": "string"},
                        "notes": {"type": "string"},
                        "source_messages": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "message_id": {"type": "string"},
                                    "snippet": {"type": "string"},
                                    "author_is_jorn": {"type": "boolean"},
                                },
                                "required": ["message_id"],
                            },
                        },
                    },
                    "required": ["word", "category", "meaning", "confidence", "source_messages"],
                },
            },
            "updated_entries": {
                "type": "array",
                "description": (
                    "Existing entries whose confidence should be UPGRADED, or whose "
                    "meaning/plural/pronunciation can be filled in. Confidence "
                    "downgrades are ignored. Reference by (word, category)."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "word": {"type": "string"},
                        "category": {"type": "string", "enum": list(VALID_CATEGORIES)},
                        "confidence": {
                            "type": "string",
                            "enum": ["unknown", "guess", "uncertain", "medium", "high", "confirmed"],
                        },
                        "meaning": {"type": "string"},
                        "refine_meaning": {
                            "type": "boolean",
                            "description": "Set true if the meaning should replace the existing one.",
                        },
                        "plural": {"type": "string"},
                        "pronunciation": {"type": "string"},
                        "reasoning": {"type": "string"},
                        "source_messages": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "message_id": {"type": "string"},
                                    "snippet": {"type": "string"},
                                    "author_is_jorn": {"type": "boolean"},
                                },
                                "required": ["message_id"],
                            },
                        },
                    },
                    "required": ["word", "category"],
                },
            },
            "new_examples": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "conlang": {"type": "string"},
                        "english": {"type": "string"},
                        "confidence": {
                            "type": "string",
                            "enum": ["unknown", "guess", "uncertain", "medium", "high", "confirmed"],
                        },
                        "source_message_id": {"type": "string"},
                        "source_message_snippet": {"type": "string"},
                        "source_author_is_jorn": {"type": "boolean"},
                    },
                    "required": ["conlang", "english", "confidence"],
                },
            },
            "corrections": {
                "type": "array",
                "description": (
                    "Jorn-only corrections. Use ONLY when Jorn (is_jorn=true) explicitly "
                    "contradicts, corrects, or rejects an existing entry. Never use for "
                    "non-Jorn speakers."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "word": {"type": "string"},
                        "category": {"type": "string", "enum": list(VALID_CATEGORIES)},
                        "action": {
                            "type": "string",
                            "enum": ["delete", "update"],
                            "description": (
                                "'delete' removes the entry (Jorn says it doesn't exist). "
                                "'update' downgrades confidence or replaces meaning."
                            ),
                        },
                        "confidence": {
                            "type": "string",
                            "enum": ["unknown", "guess", "uncertain", "medium", "high", "confirmed"],
                        },
                        "meaning": {"type": "string"},
                        "reasoning": {"type": "string"},
                        "source_messages": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "message_id": {"type": "string"},
                                    "snippet": {"type": "string"},
                                    "author_is_jorn": {"type": "boolean"},
                                },
                                "required": ["message_id", "author_is_jorn"],
                            },
                            "minItems": 1,
                        },
                    },
                    "required": ["word", "category", "action", "source_messages"],
                },
            },
            "new_grammar_rules": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "rule": {"type": "string"},
                        "detail": {"type": "string"},
                        "confidence": {
                            "type": "string",
                            "enum": ["unknown", "guess", "uncertain", "medium", "high", "confirmed"],
                        },
                    },
                    "required": ["rule", "detail", "confidence"],
                },
            },
            "reasoning": {
                "type": "string",
                "description": "Brief overall summary of the evidence behind these findings.",
            },
        },
        "required": [
            "new_entries",
            "updated_entries",
            "corrections",
            "new_examples",
            "new_grammar_rules",
            "reasoning",
        ],
    },
}


SYSTEM_PROMPT = """You are a linguist embedded in a Discord channel where a small group is collaboratively designing a constructed language ("the conlang"). Your job: read the new messages, compare against the existing dictionary, and report what should be added or refined.

# Authoritative source
The user with `is_jorn=true` is the conlang's creator. Treat ONLY Jorn's confirmations, definitions, or corrections as evidence strong enough to assign `confirmed` confidence. When other users guess or use words correctly, treat as evidence — but cap their entries at `high` confidence at most. When Jorn says "we" or corrects another user, that's authoritative.

# Trolling
Jorn occasionally trolls before revealing the real meaning — denying first, confirming later. Use the `recent_context` window to spot deny-then-confirm patterns; weight the most recent Jorn statement highest, but flag uncertain when his stance shifted within the window.

# Dual-role words
Some tokens belong to multiple categories (e.g. `Ha` = "yes" particle AND a noun; `Wa` = "what" question word AND a noun). Always treat (word, category) as the unit. Two entries for the same word in different categories is correct, not a bug.

# Confidence ladder (from least to most certain)
unknown < guess < uncertain < medium < high < confirmed

Rules:
- Only Jorn-direct confirmations move anything to `confirmed`.
- Other speakers' usage caps at `high`.
- When in doubt, prefer `guess` or `uncertain` over silence — the merge layer only upgrades via `updated_entries`. Downgrades require a Jorn correction in the `corrections` array.

# Jorn corrections
When Jorn (is_jorn=true) explicitly says a word is wrong, doesn't exist, has a different meaning, or was a troll/joke, report it in the `corrections` array — NOT in `updated_entries`.

Corrections can:
- `action: "delete"` — Jorn says this word/meaning doesn't exist at all. The entry will be removed.
- `action: "update"` — Jorn says the meaning is different, or the confidence was too high. Set the corrected `confidence` and/or `meaning`. Confidence CAN go down.

Rules for corrections:
- ONLY use corrections when Jorn explicitly contradicts or corrects something. Other speakers disagreeing is NOT a correction.
- Every correction MUST have `source_messages` with at least one message where `author_is_jorn: true`.
- If Jorn trolled earlier and is now revealing the real meaning, that's an `action: "update"` with the real meaning.
- If Jorn says a word was completely made up or doesn't exist, that's an `action: "delete"`.

# What to return
Use the `record_findings` tool. Include:
- `new_entries`: words/phrases not yet in the dictionary, with direct evidence in the new messages.
- `updated_entries`: existing entries (keyed by `word` + `category`) whose confidence should be upgraded, or whose `meaning`/`plural`/`pronunciation` can be filled. Set `refine_meaning: true` if the meaning should be REPLACED. Confidence downgrades are ignored — don't bother sending them.
- `corrections`: Jorn-only corrections — confidence downgrades, meaning replacements, or entry deletions. ONLY when Jorn explicitly contradicts existing entries.
- `new_examples`: decoded conlang sentences with English translations.
- `new_grammar_rules`: structural observations (word order, conjugation patterns, particles, etc.).
- `reasoning`: brief overall summary.

# Content
The conlang has a lot of sexual / crude humor — that's normal and expected. Do not filter or sanitize. Translate accurately.

# Source messages
For every entry/example, include `source_messages` with the message IDs (and short `snippet` text + `author_is_jorn` flag) that support the finding. The website uses these to render evidence.
"""


def _format_dict_snapshot(db: ConlangDatabase) -> str:
    """Compact representation of the existing dictionary for the prompt."""
    lines: List[str] = ["## Existing dictionary (compact)"]
    if db.entries:
        lines.append("\n### Entries")
        for e in db.entries:
            extras = []
            if e.plural:
                extras.append(f"pl={e.plural}")
            if e.pronunciation:
                extras.append(f"pron={e.pronunciation}")
            extra_str = f" [{', '.join(extras)}]" if extras else ""
            lines.append(
                f"- {e.word} ({e.category}, {e.confidence}){extra_str}: {e.meaning}"
            )
    if db.grammar_rules:
        lines.append("\n### Grammar rules")
        for r in db.grammar_rules:
            lines.append(f"- {r.rule} ({r.confidence}): {r.detail}")
    if db.example_sentences:
        lines.append("\n### Example sentences")
        for s in db.example_sentences[-30:]:
            lines.append(f'- "{s.conlang}" = "{s.english}" ({s.confidence})')
    if db.conjugations:
        lines.append("\n### Conjugations")
        for inf, c in db.conjugations.items():
            forms = ", ".join(f"{k}={v}" for k, v in c.forms.items())
            lines.append(f"- {inf}: {forms}")
    return "\n".join(lines)


def _format_recent_context(db: ConlangDatabase) -> str:
    if not db.meta.recent_context:
        return ""
    lines = ["## Recent context (already analyzed in prior batches; do NOT re-extract — for cross-batch pattern detection only)"]
    for m in db.meta.recent_context:
        tag = "JORN" if m.author_is_jorn else f"other:{m.author_name}"
        lines.append(f"[{tag}] [{m.message_id}] {m.content}")
    return "\n".join(lines)


def _format_new_messages(messages: List[Dict[str, Any]]) -> str:
    lines = ["## New messages to analyze (chronological)"]
    for m in messages:
        tag = "JORN" if m.get("is_jorn") else f"other:{m.get('author_name', '?')}"
        reactions = m.get("reactions") or ""
        rx = f" [reactions: {reactions}]" if reactions else ""
        lines.append(f"[{tag}] [{m['message_id']}] {m['content']}{rx}")
    return "\n".join(lines)


async def analyze_batch(
    anthropic_client,
    db: ConlangDatabase,
    messages: List[Dict[str, Any]],
    model: str = DEFAULT_MODEL,
) -> Dict[str, Any]:
    """Run one Claude extraction pass over `messages`. Returns the tool-input dict
    or an empty findings dict on failure."""
    if not messages:
        return _empty_result("no messages")

    user_prompt = "\n\n".join(
        s
        for s in (
            _format_dict_snapshot(db),
            _format_recent_context(db),
            _format_new_messages(messages),
        )
        if s
    )

    def _call() -> Dict[str, Any]:
        response = anthropic_client.messages.create(
            model=model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=SYSTEM_PROMPT,
            tools=[TOOL_DEFINITION],
            tool_choice={"type": "tool", "name": "record_findings"},
            messages=[{"role": "user", "content": user_prompt}],
        )
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "record_findings":
                return dict(block.input)
        logger.warning("analyzer response had no record_findings tool_use block")
        return _empty_result("no tool_use block in response")

    try:
        return await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(None, _call),
            timeout=ANALYZER_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.error("Analyzer call timed out after %ds", ANALYZER_TIMEOUT_SECONDS)
        return _empty_result("timeout")
    except Exception:
        logger.exception("Analyzer call failed")
        return _empty_result("exception")


def _empty_result(reason: str) -> Dict[str, Any]:
    return {
        "new_entries": [],
        "updated_entries": [],
        "corrections": [],
        "new_examples": [],
        "new_grammar_rules": [],
        "reasoning": f"(no findings: {reason})",
    }


def get_model_from_env() -> str:
    return os.getenv("CONLANG_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
