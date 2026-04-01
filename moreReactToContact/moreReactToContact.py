import discord
from discord.ext import commands

try:
    from core.models import DMDisabled  # Modmail core
except Exception:
    DMDisabled = None

try:
    from core.bot import ModmailBot  # type: ignore
except Exception:
    ModmailBot = object  # fallback for type checking

try:
    from discord.utils import MISSING
except Exception:
    MISSING = None


def tryint(x):
    try:
        return int(x)
    except Exception:
        return None


class MoreButtonToContact(commands.Cog):
    """
    Modmail plugin: open a Modmail thread when a user clicks a button on a panel message.

    Storage:
      Uses plugin_db partition (NOT bot.config).
    """

    def __init__(self, bot: ModmailBot):
        self.bot: ModmailBot = bot
        self.db = self.bot.plugin_db.get_partition(self)

        self.default_config = {
            "panel_message_ids": [],          # list[int]
            "contact_custom_id": "modmail:contact",
            "dm_probe_enabled": True,
            "dm_probe_text": "✅ Your ticket is being opened. You can ignore this message.",
        }
        self.config: dict = {}

    async def cog_load(self):
        # Load config from plugin partition, merge defaults
        data = await self.db.find_one({"_id": "config"}) or {}
        stored = data.get("data") or {}
        if not isinstance(stored, dict):
            stored = {}

        self.config = {**self.default_config, **stored}
        await self._save_config()  # ensures defaults persist

    async def _save_config(self):
        await self.db.update_one(
            {"_id": "config"},
            {"$set": {"data": self.config}},
            upsert=True,
        )

    def _panel_ids(self) -> set[int]:
        ids = set()
        for v in self.config.get("panel_message_ids") or []:
            i = tryint(v)
            if i:
                ids.add(i)
        return ids

    def _custom_id(self) -> str:
        return str(self.config.get("contact_custom_id") or "modmail:contact")

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

    async def _can_dm_member(self, member: discord.Member) -> bool:
        if not self.config.get("dm_probe_enabled", True):
            return True

        try:
            dm = member.dm_channel or await member.create_dm()
            probe = await dm.send(self.config.get("dm_probe_text") or "✅ Opening ticket…")
            try:
                await probe.delete()
            except Exception:
                pass
            return True
        except discord.Forbidden:
            return False
        except Exception:
            # network hiccup etc: don't block thread creation
            return True

    async def _invoke_contact(self, message: discord.Message, member: discord.Member):
        existing_thread = await self.bot.threads.find(recipient=member)
        if existing_thread and getattr(existing_thread, "snoozed", False):
            await existing_thread.restore_from_snooze()
            self.bot.threads.cache[existing_thread.id] = existing_thread
            if existing_thread.channel:
                await existing_thread.channel.send(
                    f"ℹ️ {member.mention} clicked the contact button and their snoozed thread has been unsnoozed."
                )
            return

        ctx = await self.bot.get_context(message)
        await ctx.invoke(self.bot.get_command("contact"), users=[member], manual_trigger=False)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        try:
            if interaction.type != discord.InteractionType.component:
                return

            data = interaction.data or {}
            custom_id = data.get("custom_id")
            if not custom_id or custom_id != self._custom_id():
                return

            if not interaction.message or not interaction.guild:
                return

            if interaction.message.id not in self._panel_ids():
                return

            member = interaction.guild.get_member(interaction.user.id)
            if member is None or member.bot:
                return

            # ACK quickly
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True, thinking=True)

            # Mirror upstream dm_disabled check
            dm_disabled = self.bot.config.get("dm_disabled")
            if DMDisabled is not None and dm_disabled in (DMDisabled.NEW_THREADS, DMDisabled.ALL_THREADS):
                try:
                    await self._blocked_new_thread_dm(interaction.guild, member)
                except discord.Forbidden:
                    await interaction.followup.send(
                        "Modmail is disabled for new threads, and I couldn’t DM you. "
                        "Please enable DMs and try again.",
                        ephemeral=True,
                    )
                return

            # Preflight DM check
            can_dm = await self._can_dm_member(member)
            if not can_dm:
                return await interaction.followup.send(
                    "I can’t open a ticket because your DMs are closed.\n\n"
                    "Enable **Direct Messages** for this server (Server Settings → Privacy Settings → DMs), "
                    "then click the button again.",
                    ephemeral=True,
                )

            await self._invoke_contact(interaction.message, member)

            # Post-flight check: thread exists?
            created_thread = await self.bot.threads.find(recipient=member)
            if not created_thread:
                return await interaction.followup.send(
                    "Something went wrong and I couldn’t create your ticket. Please try again.",
                    ephemeral=True,
                )

            await interaction.followup.send(
                "Ticket opened! Please check your DMs to continue.",
                ephemeral=True,
            )

        except discord.Forbidden:
            try:
                await interaction.followup.send("I couldn't DM you. Please enable DMs and try again.", ephemeral=True)
            except Exception:
                pass
        except Exception:
            # If you have Modmail logger:
            # self.bot.logger.exception("MoreButtonToContact error")
            return

    # ---- management commands ----

    @commands.group(name="btncontact", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def btncontact(self, ctx: commands.Context):
        mids = sorted(self._panel_ids())
        await ctx.send(
            f"contact_custom_id: `{self._custom_id()}`\n"
            f"panel_message_ids: `{mids}`\n"
            f"dm_probe_enabled: `{self.config.get('dm_probe_enabled', True)}`"
        )

    @btncontact.command(name="customid")
    @commands.has_permissions(administrator=True)
    async def btncontact_customid(self, ctx: commands.Context, *, custom_id: str):
        self.config["contact_custom_id"] = custom_id
        await self._save_config()
        await ctx.send(f"Set contact_custom_id to `{custom_id}`")

    @btncontact.command(name="addmsg")
    @commands.has_permissions(administrator=True)
    async def btncontact_addmsg(self, ctx: commands.Context, message_id: int):
        raw = self.config.get("panel_message_ids") or []
        if message_id not in raw:
            raw.append(message_id)
        self.config["panel_message_ids"] = raw
        await self._save_config()
        await ctx.send(f"Added `{message_id}` to panel_message_ids.")

    @btncontact.command(name="delmsg")
    @commands.has_permissions(administrator=True)
    async def btncontact_delmsg(self, ctx: commands.Context, message_id: int):
        raw = self.config.get("panel_message_ids") or []
        raw = [m for m in raw if tryint(m) != message_id]
        self.config["panel_message_ids"] = raw
        await self._save_config()
        await ctx.send(f"Removed `{message_id}` from panel_message_ids.")

    @btncontact.command(name="probe")
    @commands.has_permissions(administrator=True)
    async def btncontact_probe(self, ctx: commands.Context, enabled: bool):
        self.config["dm_probe_enabled"] = enabled
        await self._save_config()
        await ctx.send(f"dm_probe_enabled set to `{enabled}`")


async def setup(bot: ModmailBot):
    await bot.add_cog(MoreButtonToContact(bot))
