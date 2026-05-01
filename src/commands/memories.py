"""/memories — show the calling user what Bonk has remembered about them in this guild."""

import io

import discord

from src import log
from src.voice_memory import voice_memory_store

logger = log.setup_logger(__name__)

_PREVIEW_FACT_LIMIT = 30
_DISCORD_MESSAGE_CAP = 1900   # leave headroom under the 2000-char limit


async def handle_memories(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        await interaction.followup.send(
            "This command only works in a server.", ephemeral=True
        )
        return

    user_id = interaction.user.id
    guild_id = interaction.guild.id
    mem = voice_memory_store.get_user_optional(guild_id, user_id)
    opted_out = voice_memory_store.is_opted_out(guild_id, user_id)

    if mem is None or (
        not (mem.facts or mem.vibe.strip() or mem.notes.strip()) and not opted_out
    ):
        await interaction.followup.send(
            "I haven't remembered anything about you in this server yet. "
            "I learn from `/listen` voice chats — what I pick up gets stored here so "
            "I can be a better conversation partner over time. You can wipe everything "
            "with `/forget` whenever you want, or run `/forget` with the *Wipe & opt out* "
            "scope to stop me from remembering you here altogether.",
            ephemeral=True,
        )
        return

    lines = [f"**What I remember about you in {interaction.guild.name}:**", ""]
    if opted_out:
        lines.append(
            "🚫 **You're opted out** — I'm not storing anything new about you here. "
            "Use `/forget` with scope *Re-enable memory* to undo."
        )
        lines.append("")
    if mem and mem.facts:
        lines.append("__Facts:__")
        for f in mem.facts[:_PREVIEW_FACT_LIMIT]:
            lines.append(f"• {f.text}")
        if len(mem.facts) > _PREVIEW_FACT_LIMIT:
            lines.append(f"…and {len(mem.facts) - _PREVIEW_FACT_LIMIT} more")
        lines.append("")
    if mem and mem.vibe.strip():
        lines.append(f"__Vibe:__ {mem.vibe.strip()}")
        lines.append("")
    if mem and mem.notes.strip():
        lines.append("__Notes:__")
        lines.append(mem.notes.strip())
        lines.append("")
    lines.append("Use `/forget` to wipe everything I've stored about you here.")

    body = "\n".join(lines)

    if len(body) <= _DISCORD_MESSAGE_CAP:
        await interaction.followup.send(body, ephemeral=True)
    else:
        # Too long for an inline message — attach as a private text file.
        buf = io.BytesIO(body.encode("utf-8"))
        buf.seek(0)
        await interaction.followup.send(
            "Your memories are too long to fit in a message — see attached.",
            file=discord.File(buf, filename="my_memories.txt"),
            ephemeral=True,
        )

    logger.info(
        "Showed voice memories to user %s in guild %s (%d facts, opted_out=%s)",
        user_id,
        guild_id,
        len(mem.facts) if mem else 0,
        opted_out,
    )
