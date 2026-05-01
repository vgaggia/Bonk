"""/forget — wipe what Bonk remembers (or, for admins, the whole guild)."""

import discord

from src import log
from src.voice_memory import voice_memory_store

logger = log.setup_logger(__name__)


SCOPE_ME = "me"
SCOPE_ME_AND_OPTOUT = "me_and_optout"
SCOPE_OPTIN = "optin"
SCOPE_ALL = "all"


class ConfirmForgetAllView(discord.ui.View):
    """Confirmation prompt for `/forget all` — admin-only, ephemeral."""

    def __init__(self, guild_id: int, requesting_user_id: int) -> None:
        super().__init__(timeout=30)
        self.guild_id = guild_id
        self.requesting_user_id = requesting_user_id
        self._handled = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requesting_user_id:
            await interaction.response.send_message(
                "This confirmation isn't yours to answer.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Wipe everything", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        if self._handled:
            return
        self._handled = True
        users_removed = voice_memory_store.forget_guild(self.guild_id)
        await voice_memory_store.save()
        await interaction.response.edit_message(
            content=f"🧹 Wiped voice memories for **{users_removed}** user(s) in this server.",
            view=None,
        )
        logger.info(
            "Admin %s wiped all voice memories in guild %s (%d users)",
            self.requesting_user_id,
            self.guild_id,
            users_removed,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        if self._handled:
            return
        self._handled = True
        await interaction.response.edit_message(content="Cancelled.", view=None)


async def handle_forget(interaction: discord.Interaction, scope: str = SCOPE_ME) -> None:
    if not interaction.guild:
        await interaction.followup.send(
            "This command only works in a server.", ephemeral=True
        )
        return

    guild_id = interaction.guild.id
    user_id = interaction.user.id
    scope_value = (scope or SCOPE_ME).strip().lower()

    if scope_value == SCOPE_ALL:
        # Admin-only path.
        member = interaction.guild.get_member(user_id) or interaction.user
        perms = getattr(member, "guild_permissions", None)
        if not perms or not perms.manage_guild:
            await interaction.followup.send(
                "`/forget` with scope *All users* requires the **Manage Server** permission.",
                ephemeral=True,
            )
            return

        view = ConfirmForgetAllView(
            guild_id=guild_id, requesting_user_id=user_id
        )
        await interaction.followup.send(
            "⚠️ This wipes voice memories Bonk has stored for **every user in this server**. "
            "This can't be undone. Confirm?",
            view=view,
            ephemeral=True,
        )
        return

    if scope_value == SCOPE_OPTIN:
        # Re-enable memory for the calling user.
        was_opted_out = voice_memory_store.is_opted_out(guild_id, user_id)
        voice_memory_store.set_opt_out(guild_id, user_id, False)
        await voice_memory_store.save()
        if was_opted_out:
            await interaction.followup.send(
                "✅ Memory re-enabled. I'll start picking up new things from voice "
                "chats again. (Old memories that were wiped don't come back.)",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "Memory was already enabled for you here.",
                ephemeral=True,
            )
        logger.info("User %s re-enabled memory in guild %s", user_id, guild_id)
        return

    # SCOPE_ME or SCOPE_ME_AND_OPTOUT — both wipe.
    facts_removed, had_anything, did_pop = voice_memory_store.forget_user(guild_id, user_id)

    optout_msg = ""
    if scope_value == SCOPE_ME_AND_OPTOUT:
        # Re-create the user record (forget_user popped it) and set opt_out.
        voice_memory_store.set_opt_out(guild_id, user_id, True)
        optout_msg = " You're now opted out — I won't pick up new things about you here. Run `/forget` with scope *Re-enable memory* to undo."

    # Save unconditionally if anything was removed OR opted-out toggled, because
    # in either case state changed in memory.
    needs_save = did_pop or scope_value == SCOPE_ME_AND_OPTOUT
    if needs_save:
        await voice_memory_store.save()

    if not did_pop and scope_value == SCOPE_ME:
        await interaction.followup.send(
            "I had nothing about you in this server to forget.", ephemeral=True
        )
        return

    if not had_anything:
        msg = "🧹 Nothing meaningful was stored, but cleared the placeholder record."
    else:
        msg = f"🧹 Forgotten. Wiped {facts_removed} fact(s) and any vibe/notes I had about you here."
    msg += optout_msg
    await interaction.followup.send(msg, ephemeral=True)
    logger.info(
        "User %s wiped voice memories in guild %s (%d facts, scope=%s)",
        user_id,
        guild_id,
        facts_removed,
        scope_value,
    )
