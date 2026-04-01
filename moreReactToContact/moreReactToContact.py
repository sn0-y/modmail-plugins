import asyncio
import discord
from discord.ext import commands

# Import these from Modmail if available in your repo.
# These names match the upstream code patterns shown in bot.py.
try:
    from core.models import DMDisabled  # type: ignore
except Exception:
    DMDisabled = None  # fallback if your local layout differs


def tryint(x):
    try:
        return int(x)
    except Exception:
        return None


class moreReactToContact(commands.Cog):
    """
    Extends modmail-dev/Modmail by adding "button to contact" support.

    Similar to Bot.handle_react_to_contact in bot.py, but triggers when a user clicks
    a button on one of many configured panel messages.
    """

    def __init__(self, bot):
        self.bot = bot

    # ---- helpers to read/write config safely ----

    def _get_button_message_ids(self):
        """
        Returns a set of message IDs that should behave as contact panels.
        Stored in config key: button_to_contact_messages (list[int]).
        """
        raw = self.bot.config.get("button_to_contact_messages") or []
        ids = set()
        for v in raw:
            i = tryint(v)
            if i:
                ids.add(i)
        return ids

    def _get_button_custom_id(self):
        """
        Stored in config key: button_to_contact_custom_id (str).
        """
        return self.bot.config.get("button_to_contact_custom_id") or "modmail:contact"

    async def _blocked_new_thread_dm(self, guild: discord.Guild, member: discord.Member):
        embed = discord.Embed(
            title=self.bot.config["disabled_new_thread_title"],
            color=self.bot.error_color,
            description=self.bot.config["disabled_new_thread_response"],
        )
        embed.set_footer(
            text=self.bot.config["disabled_new_thread_footer"],
            icon_url=self.bot.get_guild_icon(guild=guild, size=128),
        )
        await member.send(embed=embed)

    async def _invoke_contact(self, message: discord.Message, member: discord.Member):
        # Check if user has a snoozed thread (matches upstream flow)
        existing_thread = await self.bot.threads.find(recipient=member)
        if existing_thread and getattr(existing_thread, "snoozed", False):
            await existing_thread.restore_from_snooze()
            self.bot.threads.cache[existing_thread.id] = existing_thread
            if existing_thread.channel:
                await existing_thread.channel.send(
                    f"ℹ️ {member.mention} clicked the contact button and their snoozed thread has been unsnoozed."
                )
            return "unsnoozed"

        ctx = await self.bot.get_context(message)
        await ctx.invoke(
            self.bot.get_command("contact"),
            users=[member],
            manual_trigger=False,
        )
        return "invoked"

    async def _can_dm_member(self, member: discord.Member) -> bool:
        """
        Best-effort DM check: try to send (and delete) a lightweight DM.
        If it fails with Forbidden, DMs are closed for the bot.
        """
        try:
            dm = member.dm_channel or await member.create_dm()
            probe = await dm.send(
                self.bot.config.get(
                    "button_to_contact_dm_probe_text",
                    "✅ Your ticket is being opened. You can ignore this message.",
                )
            )
            # Avoid cluttering user's DMs if possible.
            # If they have DMs open, deletion should succeed; if it fails, it's not critical.
            try:
                await probe.delete()
            except Exception:
                pass
            return True
        except discord.Forbidden:
            return False
        except Exception:
            # If Discord hiccups, treat as not reliable; return True so we don't block unnecessarily.
            return True

    # ---- main listener ----

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """
        Fires for component interactions (buttons, selects).
        We filter to button clicks that match our custom_id and message_id list.
        """
        try:
            if interaction.type != discord.InteractionType.component:
                return

            data = interaction.data or {}
            custom_id = data.get("custom_id")
            if not custom_id or custom_id != self._get_button_custom_id():
                return

            if not interaction.message or not interaction.guild:
                return

            if interaction.message.id not in self._get_button_message_ids():
                return

            member = interaction.guild.get_member(interaction.user.id)
            if member is None or member.bot:
                return

            # ACK quickly (avoid "This interaction failed")
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True, thinking=True)

            # Mirror the upstream react_to_contact dm_disabled check.
            dm_disabled = self.bot.config.get("dm_disabled")
            if DMDisabled is not None:
                if dm_disabled in (DMDisabled.NEW_THREADS, DMDisabled.ALL_THREADS):
                    # This sends a DM (same as upstream). If their DMs are closed, they'll never see it,
                    # but that's consistent with core behavior.
                    try:
                        await self._blocked_new_thread_dm(interaction.guild, member)
                    except discord.Forbidden:
                        await interaction.followup.send(
                            "Modmail is currently disabled for new threads, and I couldn't DM you. "
                            "Please enable DMs from server members (or add the bot as a friend) and try again.",
                            ephemeral=True,
                        )
                    return

            # Pre-flight: check DMs are open BEFORE we create anything, so we can tell them immediately.
            # (This is the only reliable signal, since Modmail thread creation usually starts by DMing.)
            can_dm = await self._can_dm_member(member)
            if not can_dm:
                return await interaction.followup.send(
                    "I can’t open a Modmail ticket because your DMs are closed.\n\n"
                    "Please enable **Direct Messages** for this server (Server Settings → Privacy Settings → DMs), "
                    "then click the button again.",
                    ephemeral=True,
                )

            # Invoke Modmail's contact flow
            await self._invoke_contact(interaction.message, member)

            # Post-flight: verify a thread exists. If not, something failed in contact creation.
            # We can't perfectly know why, but we can give a useful user-facing message.
            created_thread = await self.bot.threads.find(recipient=member)
            if not created_thread:
                return await interaction.followup.send(
                    "Something went wrong and I couldn’t create your ticket.\n\n"
                    "Please try again in a moment. If it keeps failing, contact staff another way.",
                    ephemeral=True,
                )

            await interaction.followup.send(
                "Ticket opened! Please check your DMs to continue the conversation.",
                ephemeral=True,
            )

        except discord.Forbidden:
            # User DMs closed etc. (should be caught by pre-flight, but keep safe)
            try:
                await interaction.followup.send(
                    "I couldn't DM you. Please enable DMs and try again.",
                    ephemeral=True,
                )
            except Exception:
                pass
            return
        except Exception:
            # If you have Modmail's logger available, log it here
            # self.bot.logger.exception("button_to_contact failed")
            return

    # Optional: commands to manage the panel message list from inside Discord

    @commands.group(name="btncontact", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def btncontact(self, ctx: commands.Context):
        """Manage button-to-contact settings."""
        mids = sorted(self._get_button_message_ids())
        cid = self._get_button_custom_id()
        await ctx.send(
            f"button_to_contact_custom_id: `{cid}`\n"
            f"button_to_contact_messages: `{mids}`"
        )

    @btncontact.command(name="customid")
    @commands.has_permissions(administrator=True)
    async def btncontact_customid(self, ctx: commands.Context, *, custom_id: str):
        self.bot.config["button_to_contact_custom_id"] = custom_id
        await self.bot.config.update()
        await ctx.send(f"Set button_to_contact_custom_id to `{custom_id}`")

    @btncontact.command(name="addmsg")
    @commands.has_permissions(administrator=True)
    async def btncontact_addmsg(self, ctx: commands.Context, message_id: int):
        raw = self.bot.config.get("button_to_contact_messages") or []
        if message_id not in raw:
            raw.append(message_id)
        self.bot.config["button_to_contact_messages"] = raw
        await self.bot.config.update()
        await ctx.send(f"Added `{message_id}` to button_to_contact_messages.")

    @btncontact.command(name="delmsg")
    @commands.has_permissions(administrator=True)
    async def btncontact_delmsg(self, ctx: commands.Context, message_id: int):
        raw = self.bot.config.get("button_to_contact_messages") or []
        raw = [m for m in raw if tryint(m) != message_id]
        self.bot.config["button_to_contact_messages"] = raw
        await self.bot.config.update()
        await ctx.send(f"Removed `{message_id}` from button_to_contact_messages.")


async def setup(bot):
    await bot.add_cog(moreReactToContact(bot))
