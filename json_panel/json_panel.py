import json
import re
import discord
from discord.ext import commands

def _as_embed(d: dict) -> discord.Embed:
    kwargs = {}

    if isinstance(d.get("title"), str):
        kwargs["title"] = d["title"]
    if isinstance(d.get("description"), str):
        kwargs["description"] = d["description"]
    if isinstance(d.get("url"), str):
        kwargs["url"] = d["url"]
    if isinstance(d.get("color"), int):
        kwargs["color"] = discord.Color(d["color"])

    embed = discord.Embed(**kwargs)

    author = d.get("author")
    if isinstance(author, dict):
        a_name = author.get("name")
        a_url = author.get("url")
        a_icon = author.get("icon_url")
        if a_name or a_url or a_icon:
            embed.set_author(
                name=a_name or "",
                url=a_url,
                icon_url=a_icon,
            )

    footer = d.get("footer")
    if isinstance(footer, dict):
        f_text = footer.get("text")
        f_icon = footer.get("icon_url")
        if f_text or f_icon:
            embed.set_footer(
                text=f_text or "",
                icon_url=f_icon,
            )

    thumb = d.get("thumbnail")
    if isinstance(thumb, dict) and isinstance(thumb.get("url"), str):
        embed.set_thumbnail(url=thumb["url"])

    image = d.get("image")
    if isinstance(image, dict) and isinstance(image.get("url"), str):
        embed.set_image(url=image["url"])

    for f in d.get("fields", []) or []:
        if not isinstance(f, dict):
            continue
        name = f.get("name") or "\u200b"
        value = f.get("value") or "\u200b"
        inline = bool(f.get("inline", False))
        embed.add_field(name=name, value=value, inline=inline)

    return embed


def _parse_emoji(obj) -> discord.PartialEmoji | str | None:
    """
    Discohook-ish emoji object:
      { "name": "✅" }
      { "name": "blobdance", "id": "123", "animated": true }
    discord.ui.Button accepts emoji as:
      - str (unicode) OR
      - discord.PartialEmoji / discord.Emoji
    """
    if not obj:
        return None

    # If they already passed a string, treat it as unicode emoji
    if isinstance(obj, str):
        return obj

    if not isinstance(obj, dict):
        return None

    name = obj.get("name")
    emoji_id = obj.get("id")
    animated = bool(obj.get("animated", False))

    if emoji_id:
        try:
            emoji_id = int(emoji_id)
        except Exception:
            return None

        # Custom emoji
        return discord.PartialEmoji(name=name, id=emoji_id, animated=animated)

    # Unicode emoji
    if isinstance(name, str) and name:
        return name

    return None


def _build_view(components: list) -> discord.ui.View | None:
    if not components:
        return None

    view = discord.ui.View(timeout=None)
    row_index = 0

    for row in components:
        if not isinstance(row, dict) or row.get("type") != 1:
            continue

        col_index = 0
        for comp in (row.get("components") or []):
            if not isinstance(comp, dict) or comp.get("type") != 2:
                continue

            style = comp.get("style")
            label = comp.get("label")
            disabled = bool(comp.get("disabled", False))
            emoji = _parse_emoji(comp.get("emoji"))

            # Link button
            if style == 5:
                url = comp.get("url")
                if not url:
                    continue
                view.add_item(
                    discord.ui.Button(
                        style=discord.ButtonStyle.link,
                        label=label,
                        url=url,
                        emoji=emoji,
                        disabled=disabled,
                        row=row_index,
                    )
                )
                col_index += 1
                if col_index >= 5:
                    break
                continue

            # Custom-id button
            custom_id = comp.get("custom_id")
            if not custom_id:
                continue

            style_map = {
                1: discord.ButtonStyle.primary,
                2: discord.ButtonStyle.secondary,
                3: discord.ButtonStyle.success,
                4: discord.ButtonStyle.danger,
            }
            btn_style = style_map.get(style, discord.ButtonStyle.secondary)

            view.add_item(
                discord.ui.Button(
                    style=btn_style,
                    label=label,
                    custom_id=custom_id,
                    emoji=emoji,
                    disabled=disabled,
                    row=row_index,
                )
            )

            col_index += 1
            if col_index >= 5:
                break

        row_index += 1
        if row_index >= 5:
            break

    return view if view.children else None


class PanelJSON(commands.Cog):
    """
    Modmail plugin: send a message from Discohook-style JSON.

    Commands:
      ?paneljson send #channel <json...>
      ?paneljson sendfile #channel   (attach .json)
    """

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="paneljson", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def paneljson(self, ctx: commands.Context):
        await ctx.send(
            "Usage:\n"
            f"`{ctx.prefix}paneljson send #channel <json>`\n"
            f"`{ctx.prefix}paneljson sendfile #channel` (attach a .json file)"
        )

    @paneljson.command(name="send")
    @commands.has_permissions(manage_guild=True)
    async def send(self, ctx: commands.Context, channel: discord.TextChannel, *, payload: str):
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as e:
            return await ctx.send(f"Invalid JSON: `{e}`")

        await self._send_from_data(ctx, channel, data)

    @paneljson.command(name="sendfile")
    @commands.has_permissions(manage_guild=True)
    async def sendfile(self, ctx: commands.Context, channel: discord.TextChannel):
        if not ctx.message.attachments:
            return await ctx.send("Attach a `.json` file containing the message payload.")

        att = ctx.message.attachments[0]
        try:
            raw = await att.read()
            data = json.loads(raw.decode("utf-8"))
        except Exception as e:
            return await ctx.send(f"Failed to read/parse attachment: `{e}`")

        await self._send_from_data(ctx, channel, data)

    async def _send_from_data(self, ctx: commands.Context, channel: discord.TextChannel, data: dict):
        content = data.get("content")
        if content is not None and not isinstance(content, str):
            content = str(content)

        embeds = []
        for e in (data.get("embeds") or []):
            if isinstance(e, dict):
                embeds.append(_as_embed(e))

        view = _build_view(data.get("components") or [])

        msg = await channel.send(
            content=content,
            embeds=embeds[:10],
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )

        await ctx.send(f"Sent message `{msg.id}` to {channel.mention}.")


async def setup(bot):
    await bot.add_cog(PanelJSON(bot))
