from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import discord

from src import log

from . import analyzer
from .database import ConlangDatabase
from .schema import RecentMessage, now_iso, parse_iso

logger = log.setup_logger(__name__)

POLL_INTERVAL_SECONDS = 60       # how often the loop wakes to check timers
MAX_BACKFILL_MESSAGES = 200      # first-run cap
ANALYZE_CHUNK_SIZE = 50          # chunk a large batch into this many at a time
ERROR_BACKOFF_SECONDS = 300      # sleep after a fatal-iteration error


def _env_int(name: str, default: Optional[int] = None) -> Optional[int]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("env var %s is not an int: %r — using default %r", name, raw, default)
        return default


def _config() -> Dict[str, Any]:
    channel_id = _env_int("CONLANG_CHANNEL_ID")
    jorn_id = _env_int("JORN_USER_ID")
    db_path = Path(os.getenv("CONLANG_DB_PATH", "./data/conlang_dictionary.json"))
    model = analyzer.get_model_from_env()
    return {"channel_id": channel_id, "jorn_id": jorn_id, "db_path": db_path, "model": model}


def _format_reactions(message: discord.Message) -> str:
    parts = []
    for r in message.reactions:
        emoji = str(r.emoji)
        parts.append(f"{emoji}x{r.count}")
    return ", ".join(parts)


def _msg_to_dict(message: discord.Message, jorn_id: Optional[int]) -> Dict[str, Any]:
    return {
        "message_id": str(message.id),
        "author_id": str(message.author.id),
        "author_name": message.author.display_name,
        "is_jorn": jorn_id is not None and message.author.id == jorn_id,
        "content": message.content or "",
        "timestamp": message.created_at.isoformat(),
        "reactions": _format_reactions(message),
    }


def _msg_to_recent(message: discord.Message, jorn_id: Optional[int]) -> RecentMessage:
    return RecentMessage(
        message_id=str(message.id),
        author_is_jorn=jorn_id is not None and message.author.id == jorn_id,
        author_name=message.author.display_name,
        content=(message.content or "")[:500],
        timestamp=message.created_at.isoformat(),
    )


async def _fetch_new_messages(
    channel: discord.abc.Messageable,
    last_message_id: Optional[str],
) -> List[discord.Message]:
    if last_message_id is None:
        # First run: backfill recent history.
        msgs: List[discord.Message] = []
        async for m in channel.history(limit=MAX_BACKFILL_MESSAGES, oldest_first=False):  # type: ignore[attr-defined]
            msgs.append(m)
        msgs.reverse()  # chronological
        return msgs
    after = discord.Object(id=int(last_message_id))
    msgs = []
    async for m in channel.history(limit=None, after=after, oldest_first=True):  # type: ignore[attr-defined]
        msgs.append(m)
    return msgs


async def _run_sync_cycle(
    client: discord.Client,
    anthropic_client,
    db: ConlangDatabase,
    cfg: Dict[str, Any],
) -> None:
    channel = client.get_channel(cfg["channel_id"])
    if channel is None:
        logger.warning(
            "Conlang channel %s not visible to bot; skipping sync", cfg["channel_id"]
        )
        return

    db.meta.channel_id = str(cfg["channel_id"])
    if getattr(channel, "guild", None) is not None:
        db.meta.guild_id = str(channel.guild.id)

    new_messages = await _fetch_new_messages(channel, db.meta.last_message_id)
    if not new_messages:
        logger.info("Conlang sync: no new messages since last_message_id=%s", db.meta.last_message_id)
        # Mark cycle complete so a stale force_sync_requested_at doesn't keep firing.
        db.meta.last_updated = now_iso()
        db.meta.force_sync_requested_at = None
        await db.save()
        return

    logger.info("Conlang sync: %d new messages", len(new_messages))

    # Process in chunks so a 200-message backfill doesn't blow the input window.
    for i in range(0, len(new_messages), ANALYZE_CHUNK_SIZE):
        chunk = new_messages[i : i + ANALYZE_CHUNK_SIZE]
        message_dicts = [_msg_to_dict(m, cfg["jorn_id"]) for m in chunk]
        result = await analyzer.analyze_batch(
            anthropic_client, db, message_dicts, model=cfg["model"]
        )
        added, updated, examples_added, rules_added, corrected = db.merge_analysis(result)
        logger.info(
            "Conlang merge: +%d entries, ~%d updated, -%d corrected, +%d examples, +%d rules. Reason: %s",
            added,
            updated,
            corrected,
            examples_added,
            rules_added,
            (result.get("reasoning") or "")[:200],
        )
        # Push chunk into recent context for next batch's cross-batch detection.
        db.push_recent([_msg_to_recent(m, cfg["jorn_id"]) for m in chunk])
        # Advance last_message_id to the last message of this chunk.
        db.meta.last_message_id = str(chunk[-1].id)
        db.meta.last_updated = now_iso()
        await db.save()

    db.meta.force_sync_requested_at = None
    await db.save()


async def _should_sync(db: ConlangDatabase) -> bool:
    now = datetime.now(timezone.utc)

    force_at = parse_iso(db.meta.force_sync_requested_at)
    last_updated = parse_iso(db.meta.last_updated)
    forced = force_at is not None and (last_updated is None or force_at > last_updated)
    if forced:
        return True

    if not db.meta.auto_sync_enabled:
        return False
    if last_updated is None:
        return True
    elapsed = (now - last_updated).total_seconds()
    return elapsed >= db.meta.sync_interval_hours * 3600


async def start(client: discord.Client, anthropic_client) -> None:
    """Entry point — call from on_ready. Runs forever until cancelled."""
    cfg = _config()
    if cfg["channel_id"] is None:
        logger.warning("CONLANG_CHANNEL_ID not set; conlang tracker is inactive")
        return
    if cfg["jorn_id"] is None:
        logger.warning(
            "JORN_USER_ID not set; conlang tracker will run but cannot identify Jorn — "
            "all evidence will be treated as non-authoritative"
        )

    db = ConlangDatabase(cfg["db_path"])
    await db.load()

    logger.info(
        "Conlang tracker started — channel=%s db=%s model=%s interval=%.1fh auto=%s",
        cfg["channel_id"],
        cfg["db_path"],
        cfg["model"],
        db.meta.sync_interval_hours,
        db.meta.auto_sync_enabled,
    )

    while True:
        try:
            # Reload meta from disk every poll so website changes are picked up.
            await db.load()
            if await _should_sync(db):
                await _run_sync_cycle(client, anthropic_client, db, cfg)
        except asyncio.CancelledError:
            logger.info("Conlang tracker cancelled")
            raise
        except Exception:
            logger.exception("Conlang sync iteration failed; backing off %ds", ERROR_BACKOFF_SECONDS)
            await asyncio.sleep(ERROR_BACKOFF_SECONDS)
            continue

        await asyncio.sleep(POLL_INTERVAL_SECONDS)
