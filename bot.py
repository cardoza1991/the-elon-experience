"""The Elon Experience - roast bot + pro moderation. 'AI without AI'.

All the personality lives in brain.py (rules + TF-IDF) and lines.py (line banks).
Moderation logic lives in moderation.py. State (config, warns, memory) is
persisted to data/state.json. No LLMs, no local models, no cloud AI calls.
"""
import asyncio
import datetime as dt
import os
import random
import signal
from typing import Literal, Optional

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

import brain
import moderation as mod
from store import DEFAULT_CONFIG, Store

load_dotenv()
BASE = os.path.dirname(os.path.abspath(__file__))
PREFIX = "!"

store = Store(os.path.join(BASE, "data", "state.json"))
TWEETS = brain.load_tweets(os.path.join(BASE, "elon_tweets_sample.csv"))
print(f"Loaded {len(TWEETS)} Elon quotes from CSV" if TWEETS else "No CSV - using fallback quotes")
BRAIN = brain.Brain(store, TWEETS)
SPAM = mod.SpamDetector()
RAID = mod.RaidDetector()
WARNS = mod.WarnBook(store)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

COLOR = 0x1DA1F2
RED, ORANGE, GREEN = 0xE74C3C, 0xE67E22, 0x2ECC71


class ElonBot(commands.Bot):
    synced = False

    async def setup_hook(self):
        self.loop.create_task(self._autosave())
        try:
            self.loop.add_signal_handler(signal.SIGTERM, lambda: asyncio.ensure_future(self.close()))
        except (NotImplementedError, RuntimeError):
            pass

    async def _autosave(self):
        while not self.is_closed():
            await asyncio.sleep(20)
            try:
                store.save()
            except Exception as e:
                print(f"autosave failed: {e}")

    async def close(self):
        try:
            store.save()
        except Exception as e:
            print(f"final save failed: {e}")
        await super().close()


bot = ElonBot(
    command_prefix=PREFIX, intents=intents, help_command=None, case_insensitive=True,
    allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True, replied_user=False),
)


# ======================================================================== helpers

def cfg(guild):
    return store.cfg(guild.id) if guild else dict(DEFAULT_CONFIG)


def is_immune(member):
    if not isinstance(member, discord.Member):
        return True
    p = member.guild_permissions
    if member == member.guild.owner or p.administrator or p.manage_messages or p.moderate_members:
        return True
    immune = set(cfg(member.guild)["immune_roles"])
    return any(r.id in immune for r in member.roles)


def hierarchy_error(ctx, target, action="do that to"):
    """None if author + bot may act on target, else a human error message."""
    if not isinstance(target, discord.Member):
        return None
    g = ctx.guild
    if target == ctx.author:
        return f"You can't {action} yourself."
    if target.id == bot.user.id:
        return "Nice try."
    if target == g.owner:
        return "That's the server owner. No."
    if ctx.author != g.owner and target.top_role >= ctx.author.top_role:
        return f"{target.display_name}'s top role is higher than or equal to yours."
    if target.top_role >= g.me.top_role:
        return (f"{target.display_name}'s top role is above mine. Drag my role higher in "
                f"Server Settings > Roles and try again.")
    return None


async def say(ctx, content=None, *, embed=None, ephemeral=True, delete_after=None):
    try:
        if ctx.interaction is not None:
            return await ctx.send(content, embed=embed, ephemeral=ephemeral)
        return await ctx.send(content, embed=embed, delete_after=delete_after)
    except discord.HTTPException as e:
        print(f"send failed: {e}")


async def modlog(guild, title, desc, color=ORANGE, fields=()):
    cid = cfg(guild)["modlog"]
    if not cid:
        return
    ch = guild.get_channel(int(cid))
    if not ch:
        return
    e = discord.Embed(title=title, description=desc, color=color, timestamp=dt.datetime.now(dt.timezone.utc))
    for name, value in fields:
        e.add_field(name=name, value=str(value)[:1000] or "-", inline=True)
    try:
        await ch.send(embed=e)
    except discord.HTTPException as e2:
        print(f"modlog send failed in {guild.id}: {e2}")


async def do_timeout(member, seconds, reason):
    until = dt.timedelta(seconds=seconds)
    await member.timeout(until, reason=reason[:500])


async def apply_warn(guild, member, reason, moderator):
    c = cfg(guild)
    count, trigger = WARNS.add(guild.id, member.id, reason, moderator, c["warn_limit"])
    extra = ""
    if trigger:
        secs = mod.parse_duration(c["warn_timeout"])
        me = guild.me
        if (me.guild_permissions.moderate_members and member.top_role < me.top_role
                and not member.guild_permissions.administrator and member != guild.owner):
            try:
                await do_timeout(member, secs, mod.audit_reason(moderator, f"auto-timeout: {count} warns"))
                extra = f" Auto-timeout {mod.fmt_duration(secs)} ({count} warns)."
            except discord.HTTPException as e:
                extra = f" (Auto-timeout failed: {e.text if hasattr(e, 'text') else e})"
        else:
            extra = " (Auto-timeout skipped: I lack Moderate Members or my role is too low.)"
    await modlog(guild, "Warn", f"{member.mention} warned by {moderator}.{extra}", ORANGE,
                 [("Reason", reason or "-"), ("Total warns", count)])
    return count, extra


def extra_block(guild):
    return cfg(guild)["blocklist_extra"] if guild else ()


def name_of(member):
    return getattr(member, "display_name", None) or getattr(member, "name", "bro")


# ======================================================================== events

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")
    # Force online status (from e5fd83e)
    await bot.change_presence(status=discord.Status.online,
                              activity=discord.Game(name="talking shit | !help"))
    print("Status set to online")
    if not bot.synced:
        bot.synced = True
        for g in bot.guilds:
            await sync_guild(g)


async def sync_guild(g):
    try:
        bot.tree.copy_global_to(guild=g)
        cmds = await bot.tree.sync(guild=g)
        print(f"Synced {len(cmds)} slash commands to guild {g.id}")
    except discord.Forbidden:
        print(f"Slash sync forbidden in guild {g.id}: re-invite the bot with the applications.commands scope")
    except Exception as e:
        print(f"Slash sync failed in guild {g.id}: {type(e).__name__}: {e}")


@bot.event
async def on_guild_join(guild):
    await sync_guild(guild)


@bot.event
async def on_member_join(member):
    g = member.guild
    c = cfg(g)
    if not RAID.record_join(g.id, c):
        return
    msg = f"Raid alarm: {c['raid_joins']}+ joins in {c['raid_secs']}s."
    if c["raid_action"] == "lock":
        n = await lockdown(g, True, f"anti-raid: {c['raid_joins']} joins in {c['raid_secs']}s")
        msg += f" Locked {n} channels. Use `/lockdown off` when it's over."
    await modlog(g, "ANTI-RAID", msg, RED, [("Latest join", f"{member} ({member.id})")])
    print(f"anti-raid fired in guild {g.id}")


async def automod(message):
    """Returns True if the message was actioned (and the bot should stay quiet)."""
    g, m = message.guild, message.author
    if is_immune(m):
        return False
    c = cfg(g)
    text = message.content or ""
    reason = SPAM.check(g.id, m.id, text, len(message.raw_mentions) + len(message.raw_role_mentions)
                        + (5 if message.mention_everyone else 0), c)
    perms = message.channel.permissions_for(g.me)
    if reason:
        secs = mod.parse_duration(c["spam_timeout"])
        timed = False
        if perms.manage_messages:
            try:
                await message.delete()
            except discord.HTTPException:
                pass
        if g.me.guild_permissions.moderate_members and m.top_role < g.me.top_role:
            try:
                await do_timeout(m, secs, mod.audit_reason("anti-spam", reason))
                timed = True
            except discord.HTTPException:
                pass
        await message.channel.send(
            f"🧯 {m.mention} " + (f"timed out {mod.fmt_duration(secs)} for {reason}." if timed else f"chill. ({reason})"),
            delete_after=15)
        await modlog(g, "Anti-spam", f"{m.mention} in {message.channel.mention}: {reason}", RED,
                     [("Action", f"timeout {mod.fmt_duration(secs)}" if timed else "warned only (no perms)")])
        return True
    kind = mod.link_violation(text, c["link_filter"])
    if kind:
        if perms.manage_messages:
            try:
                await message.delete()
            except discord.HTTPException:
                pass
        await message.channel.send(f"{m.mention} {'invite links' if kind == 'invite' else 'links'} aren't allowed here.",
                                   delete_after=10)
        await modlog(g, "Link filter", f"Deleted {kind} from {m.mention} in {message.channel.mention}", ORANGE,
                     [("Content", text[:300])])
        return True
    if c["badwords_enabled"]:
        word = mod.badword_hit(text, c["badwords"])
        if word:
            if perms.manage_messages:
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
            count, extra = await apply_warn(g, m, f"bad word filter", "auto-mod")
            await message.channel.send(f"{m.mention} watch it. Warn #{count}.{extra}", delete_after=10)
            return True
    return False


async def replied_to_bot(message):
    ref = message.reference
    if not ref or not ref.message_id:
        return False
    r = ref.resolved if isinstance(ref.resolved, discord.Message) else ref.cached_message
    if r is None:
        try:
            r = await message.channel.fetch_message(ref.message_id)
        except discord.HTTPException:
            return False
    return r.author.id == bot.user.id


@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if message.guild is None:
        await bot.process_commands(message)
        return

    try:
        if await automod(message):
            return
    except Exception as e:
        print(f"automod error: {type(e).__name__}: {e}")

    ctx = await bot.get_context(message)
    if ctx.valid:
        await bot.invoke(ctx)
        return

    g, author = message.guild, message.author
    c = cfg(g)
    text = message.content or ""
    directed = bot.user in message.mentions or await replied_to_bot(message)
    ch = message.channel
    parent = getattr(ch, "parent_id", None)
    reply = None
    try:
        if directed:
            if BRAIN.allow_directed(g.id, author.id, c):
                reply = BRAIN.respond(g.id, author.id, name_of(author), text, directed=True, cfg=c,
                                      extra_block=c["blocklist_extra"])
        elif BRAIN.should_jump(g.id, ch.id, author.id, text, c, parent):
            reply = BRAIN.respond(g.id, author.id, name_of(author), text, directed=False, cfg=c,
                                  extra_block=c["blocklist_extra"])
    finally:
        BRAIN.observe(g.id, author.id, name_of(author), text, brain.detect_topic(brain.clean_text(text)))

    if reply is None:
        return
    perms = ch.permissions_for(g.me)
    if not perms.send_messages:
        return
    try:
        async with ch.typing():
            await asyncio.sleep(min(3.5, 0.6 + len(reply.text) / 70 + random.random() * 0.9))
        if directed or random.random() < 0.7:
            await message.reply(reply.text, mention_author=False)
        else:
            await ch.send(reply.text)
    except discord.HTTPException as e:
        print(f"reply failed: {e}")


# ======================================================================== errors

def _unwrap(error):
    seen = 0
    while seen < 5 and hasattr(error, "original") and error.original is not None:
        error = error.original
        seen += 1
    return error


@bot.event
async def on_command_error(ctx, error):
    err = _unwrap(error)
    if isinstance(err, commands.CommandNotFound):
        return
    if isinstance(err, (commands.MissingPermissions, app_commands.MissingPermissions)):
        msg = "You need: " + ", ".join(p.replace("_", " ") for p in err.missing_permissions)
    elif isinstance(err, (commands.BotMissingPermissions, app_commands.BotMissingPermissions)):
        msg = ("I'm missing: " + ", ".join(p.replace("_", " ") for p in err.missing_permissions)
               + ". Give my role those permissions.")
    elif isinstance(err, commands.NoPrivateMessage):
        msg = "Server only."
    elif isinstance(err, (commands.MemberNotFound, commands.UserNotFound)):
        msg = "Couldn't find that user."
    elif isinstance(err, commands.ChannelNotFound):
        msg = "Couldn't find that channel."
    elif isinstance(err, commands.RoleNotFound):
        msg = "Couldn't find that role."
    elif isinstance(err, commands.MissingRequiredArgument):
        msg = f"Missing `{err.param.name}`. Usage: `{PREFIX}{ctx.command.qualified_name} {ctx.command.signature}`"
    elif isinstance(err, (commands.BadArgument, commands.BadLiteralArgument, commands.RangeError, ValueError)):
        msg = f"Bad input: {err}"
        if ctx.command:
            msg += f"\nUsage: `{PREFIX}{ctx.command.qualified_name} {ctx.command.signature}`"
    elif isinstance(err, commands.CommandOnCooldown):
        msg = f"Slow down. Try again in {err.retry_after:.0f}s."
    elif isinstance(err, (commands.CheckFailure, app_commands.CheckFailure)):
        msg = "You can't use that here."
    elif isinstance(err, discord.Forbidden):
        msg = "Discord said no: I'm missing a permission or my role is too low for that target."
    elif isinstance(err, discord.HTTPException):
        msg = f"Discord error: {err.text or err.status}"
    else:
        print(f"command error in {getattr(ctx.command, 'qualified_name', '?')}: {type(err).__name__}: {err}")
        msg = "Something broke. It's been logged."
    await say(ctx, f"⚠️ {msg}"[:1900], delete_after=20)


# ======================================================================== fun commands

@bot.hybrid_command(name="help", description="Show every command")
async def help_cmd(ctx):
    e = discord.Embed(title="The Elon Experience", color=COLOR,
                      description="Roasts on mention/reply, ~8% random jump-ins, real Elon tweets when you talk Tesla/SpaceX/X/AI/money. "
                                  "Every command works as `!cmd` or `/cmd`.")
    e.add_field(name="Fun", inline=False, value=(
        "`elon [topic]` real Elon tweet (aliases quote, tweet)\n"
        "`roast [@user]` targeted roast\n"
        "`profile [@user]` what the bot remembers about you"))
    e.add_field(name="Moderation", inline=False, value=(
        "`timeout @user <10m|1h|2d> [reason]` (mute)\n"
        "`untimeout @user [reason]` (unmute)\n"
        "`warn @user [reason]` - auto-timeout every N warns\n"
        "`warnings [@user]` · `clearwarns @user` · `unwarn @user`\n"
        "`kick @user [reason]` · `ban @user [delete_days] [reason]` · `unban <user_id> [reason]`\n"
        "`purge <n> [user: @x] [bots: yes] [contains: text]` (clear)\n"
        "`slowmode <seconds> [#channel]`\n"
        "`lock [#channel] [reason]` · `unlock [#channel] [reason]`\n"
        "`lockdown on|off [reason]` lock/unlock every channel"))
    e.add_field(name="Config (Manage Server)", inline=False, value=(
        "`config show`\n"
        "`config jumprate <percent>` · `config topicrate <percent>`\n"
        "`config cooldowns <channel_s> <user_s>`\n"
        "`config modlog <#channel|off>`\n"
        "`config immune add|remove <@role>`\n"
        "`config ignore add|remove [#channel]` (never jump in there)\n"
        "`config roastchannels add|remove|clear [#channel]` (only jump in there)\n"
        "`config warnlimit <n> <timeout>`\n"
        "`config spam on|off [flood_msgs] [flood_secs] [dup_count] [mention_limit] [timeout]`\n"
        "`config raid on|off|lock|alert [joins] [secs]`\n"
        "`config links off|invites|all`\n"
        "`config badwords on|off|add|remove|list [word]`\n"
        "`config blocklist add|remove|list [word]` (words the bot never repeats)\n"
        "`config tweetthreshold <0.05-0.6>`"))
    e.set_footer(text="No AI was harmed in the making of this bot. It's all if-statements.")
    await say(ctx, embed=e, ephemeral=False)


@bot.hybrid_command(name="elon", aliases=["quote", "tweet"], description="Drop a real Elon tweet, optionally about a topic")
@app_commands.describe(topic="e.g. mars, tesla, ai, money")
async def elon_cmd(ctx, *, topic: Optional[str] = None):
    gid = ctx.guild.id if ctx.guild else 0
    tweet, matched = BRAIN.elon(topic, gid, ctx.author.id)
    if BRAIN.guard.hit(tweet, extra_block(ctx.guild)):
        tweet, matched = BRAIN.elon(None, gid, ctx.author.id)
    prefix = "" if matched or not topic else f"Nothing on '{brain.snippet_of(topic, 30)}', but: "
    await say(ctx, f"{prefix}{tweet}", ephemeral=False)


@bot.hybrid_command(name="roast", description="Roast someone (or yourself)")
@commands.guild_only()
async def roast_cmd(ctx, member: Optional[discord.Member] = None):
    target = member or ctx.author
    if target.id == bot.user.id:
        target = ctx.author
    line = BRAIN.roast(ctx.guild.id, target.id, name_of(target), extra_block(ctx.guild))
    await say(ctx, f"{target.mention} {line}", ephemeral=False)


@bot.hybrid_command(name="profile", description="What the bot remembers about you")
@commands.guild_only()
async def profile_cmd(ctx, member: Optional[discord.Member] = None):
    target = member or ctx.author
    u = store.user(ctx.guild.id, target.id)
    fav = BRAIN.fav_word(u) or "-"
    topics = ", ".join(f"{k} ({v})" for k, v in sorted(u.get("topics", {}).items(), key=lambda kv: -kv[1])[:3]) or "-"
    e = discord.Embed(title=f"File on {name_of(target)}", color=COLOR)
    e.add_field(name="Times roasted", value=str(u.get("roasts", 0)))
    e.add_field(name="Favorite word", value=fav)
    e.add_field(name="Pet topics", value=topics, inline=False)
    e.add_field(name="Warns", value=str(len(WARNS.list(ctx.guild.id, target.id))))
    await say(ctx, embed=e, ephemeral=False)


# ======================================================================== moderation

@bot.hybrid_command(name="timeout", aliases=["mute"], description="Timeout a member")
@app_commands.describe(member="Who", duration="e.g. 10m, 1h, 1h30m, 2d (max 28d)", reason="Why")
@app_commands.default_permissions(moderate_members=True)
@commands.guild_only()
@commands.has_permissions(moderate_members=True)
@commands.bot_has_permissions(moderate_members=True)
async def timeout_cmd(ctx, member: discord.Member, duration: str, *, reason: Optional[str] = None):
    err = hierarchy_error(ctx, member, "time out")
    if not err and member.guild_permissions.administrator:
        err = "Discord doesn't allow timing out administrators."
    if err:
        return await say(ctx, f"⚠️ {err}")
    secs = mod.parse_duration(duration)
    await do_timeout(member, secs, mod.audit_reason(ctx.author, reason))
    await say(ctx, f"🔇 {member.mention} timed out for {mod.fmt_duration(secs)}." + (f" Reason: {reason}" if reason else ""),
              ephemeral=False)
    await modlog(ctx.guild, "Timeout", f"{member.mention} by {ctx.author.mention}", ORANGE,
                 [("Duration", mod.fmt_duration(secs)), ("Reason", reason or "-")])


@bot.hybrid_command(name="untimeout", aliases=["unmute"], description="Remove a timeout")
@app_commands.default_permissions(moderate_members=True)
@commands.guild_only()
@commands.has_permissions(moderate_members=True)
@commands.bot_has_permissions(moderate_members=True)
async def untimeout_cmd(ctx, member: discord.Member, *, reason: Optional[str] = None):
    err = hierarchy_error(ctx, member, "untimeout")
    if err:
        return await say(ctx, f"⚠️ {err}")
    if not member.is_timed_out():
        return await say(ctx, f"{member.display_name} isn't timed out.")
    await member.timeout(None, reason=mod.audit_reason(ctx.author, reason))
    await say(ctx, f"🔊 {member.mention} is free.", ephemeral=False)
    await modlog(ctx.guild, "Untimeout", f"{member.mention} by {ctx.author.mention}", GREEN, [("Reason", reason or "-")])


@bot.hybrid_command(name="warn", description="Warn a member (auto-timeout every N warns)")
@app_commands.default_permissions(moderate_members=True)
@commands.guild_only()
@commands.has_permissions(moderate_members=True)
async def warn_cmd(ctx, member: discord.Member, *, reason: Optional[str] = None):
    err = hierarchy_error(ctx, member, "warn")
    if err:
        return await say(ctx, f"⚠️ {err}")
    count, extra = await apply_warn(ctx.guild, member, reason or "no reason", ctx.author)
    await say(ctx, f"⚠️ {member.mention} warned (#{count})." + (f" Reason: {reason}" if reason else "") + extra,
              ephemeral=False)


@bot.hybrid_command(name="warnings", aliases=["warns"], description="List warnings for a member (or the top offenders)")
@app_commands.default_permissions(moderate_members=True)
@commands.guild_only()
@commands.has_permissions(moderate_members=True)
async def warnings_cmd(ctx, member: Optional[discord.Member] = None):
    if member is None:
        top = WARNS.top(ctx.guild.id)
        if not top:
            return await say(ctx, "Nobody has warns. Suspicious.")
        lines = [f"<@{uid}> - {n}" for n, uid in top]
        return await say(ctx, embed=discord.Embed(title="Most warned", description="\n".join(lines), color=ORANGE))
    w = WARNS.list(ctx.guild.id, member.id)
    if not w:
        return await say(ctx, f"{member.display_name} has no warns.")
    lines = [f"**{i}.** <t:{x['ts']}:R> by {x['mod']}: {x['reason']}" for i, x in enumerate(w[-15:], max(1, len(w) - 14))]
    e = discord.Embed(title=f"Warns for {member.display_name} ({len(w)})", description="\n".join(lines)[:4000], color=ORANGE)
    await say(ctx, embed=e)


@bot.hybrid_command(name="clearwarns", description="Clear all warnings for a member")
@app_commands.default_permissions(moderate_members=True)
@commands.guild_only()
@commands.has_permissions(moderate_members=True)
async def clearwarns_cmd(ctx, member: discord.Member):
    n = WARNS.clear(ctx.guild.id, member.id)
    await say(ctx, f"Cleared {n} warns for {member.display_name}.", ephemeral=False)
    await modlog(ctx.guild, "Warns cleared", f"{member.mention} ({n}) by {ctx.author.mention}", GREEN)


@bot.hybrid_command(name="unwarn", description="Remove the most recent warning")
@app_commands.default_permissions(moderate_members=True)
@commands.guild_only()
@commands.has_permissions(moderate_members=True)
async def unwarn_cmd(ctx, member: discord.Member):
    n = WARNS.remove_last(ctx.guild.id, member.id)
    await say(ctx, f"Removed the last warn. {member.display_name} now has {n}.", ephemeral=False)
    await modlog(ctx.guild, "Unwarn", f"{member.mention} by {ctx.author.mention} (now {n})", GREEN)


@bot.hybrid_command(name="kick", description="Kick a member")
@app_commands.default_permissions(kick_members=True)
@commands.guild_only()
@commands.has_permissions(kick_members=True)
@commands.bot_has_permissions(kick_members=True)
async def kick_cmd(ctx, member: discord.Member, *, reason: Optional[str] = None):
    err = hierarchy_error(ctx, member, "kick")
    if err:
        return await say(ctx, f"⚠️ {err}")
    await member.kick(reason=mod.audit_reason(ctx.author, reason))
    await say(ctx, f"👢 Kicked {member} ." + (f" Reason: {reason}" if reason else ""), ephemeral=False)
    await modlog(ctx.guild, "Kick", f"{member} ({member.id}) by {ctx.author.mention}", RED, [("Reason", reason or "-")])


@bot.hybrid_command(name="ban", description="Ban a user (works for people not in the server too)")
@app_commands.describe(delete_days="Delete their messages from the last 0-7 days")
@app_commands.default_permissions(ban_members=True)
@commands.guild_only()
@commands.has_permissions(ban_members=True)
@commands.bot_has_permissions(ban_members=True)
async def ban_cmd(ctx, user: discord.User, delete_days: Optional[int] = 0, *, reason: Optional[str] = None):
    member = ctx.guild.get_member(user.id)
    if member:
        err = hierarchy_error(ctx, member, "ban")
        if err:
            return await say(ctx, f"⚠️ {err}")
    days = max(0, min(7, delete_days or 0))
    await ctx.guild.ban(user, reason=mod.audit_reason(ctx.author, reason), delete_message_seconds=days * 86400)
    await say(ctx, f"🔨 Banned {user}." + (f" Reason: {reason}" if reason else ""), ephemeral=False)
    await modlog(ctx.guild, "Ban", f"{user} ({user.id}) by {ctx.author.mention}", RED,
                 [("Reason", reason or "-"), ("Deleted days", days)])


@bot.hybrid_command(name="unban", description="Unban a user by ID")
@app_commands.default_permissions(ban_members=True)
@commands.guild_only()
@commands.has_permissions(ban_members=True)
@commands.bot_has_permissions(ban_members=True)
async def unban_cmd(ctx, user_id: str, *, reason: Optional[str] = None):
    try:
        uid = int(user_id.strip("<@!>"))
    except ValueError:
        return await say(ctx, "⚠️ Give me the numeric user ID (right-click > Copy User ID).")
    try:
        await ctx.guild.unban(discord.Object(id=uid), reason=mod.audit_reason(ctx.author, reason))
    except discord.NotFound:
        return await say(ctx, "⚠️ That user isn't banned.")
    await say(ctx, f"✅ Unbanned <@{uid}>.", ephemeral=False)
    await modlog(ctx.guild, "Unban", f"<@{uid}> ({uid}) by {ctx.author.mention}", GREEN, [("Reason", reason or "-")])


class PurgeFlags(commands.FlagConverter, delimiter=":", prefix=""):
    user: Optional[discord.Member] = commands.flag(default=None, description="Only this user's messages")
    bots: bool = commands.flag(default=False, description="Only bot messages")
    contains: Optional[str] = commands.flag(default=None, description="Only messages containing this text")


@bot.hybrid_command(name="purge", aliases=["clear"], description="Bulk delete messages (scans the last N)")
@app_commands.describe(amount="How many recent messages to scan (1-500)")
@app_commands.default_permissions(manage_messages=True)
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
@commands.bot_has_permissions(manage_messages=True, read_message_history=True)
async def purge_cmd(ctx, amount: commands.Range[int, 1, 500], *, flags: PurgeFlags):
    if ctx.interaction is None:
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
    else:
        await ctx.defer(ephemeral=True)
    needle = (flags.contains or "").lower()

    def check(m):
        if flags.user and m.author.id != flags.user.id:
            return False
        if flags.bots and not m.author.bot:
            return False
        if needle and needle not in (m.content or "").lower():
            return False
        return True

    deleted = await ctx.channel.purge(limit=amount, check=check,
                                      reason=mod.audit_reason(ctx.author, f"purge {amount}"))
    await say(ctx, f"🧹 Deleted {len(deleted)} messages.", delete_after=5)
    desc = f"{len(deleted)} in {ctx.channel.mention} by {ctx.author.mention}"
    filt = ", ".join(x for x in [f"user={flags.user}" if flags.user else "", "bots" if flags.bots else "",
                                  f"contains={flags.contains!r}" if flags.contains else ""] if x)
    await modlog(ctx.guild, "Purge", desc, ORANGE, [("Filters", filt or "none")])


@bot.hybrid_command(name="slowmode", description="Set channel slowmode (0 to disable)")
@app_commands.default_permissions(manage_channels=True)
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
@commands.bot_has_permissions(manage_channels=True)
async def slowmode_cmd(ctx, seconds: commands.Range[int, 0, 21600], channel: Optional[discord.TextChannel] = None):
    ch = channel or ctx.channel
    await ch.edit(slowmode_delay=seconds, reason=mod.audit_reason(ctx.author, f"slowmode {seconds}s"))
    await say(ctx, f"🐢 Slowmode in {ch.mention}: {seconds}s." if seconds else f"Slowmode off in {ch.mention}.", ephemeral=False)
    await modlog(ctx.guild, "Slowmode", f"{ch.mention} -> {seconds}s by {ctx.author.mention}")


async def set_lock(ch, locked, reason):
    role = ch.guild.default_role
    ow = ch.overwrites_for(role)
    ow.send_messages = False if locked else None
    ow.send_messages_in_threads = False if locked else None
    ow.add_reactions = False if locked else None
    await ch.set_permissions(role, overwrite=ow, reason=reason)


@bot.hybrid_command(name="lock", description="Lock a channel (@everyone can't talk)")
@app_commands.default_permissions(manage_channels=True)
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
@commands.bot_has_permissions(manage_channels=True, manage_roles=True)
async def lock_cmd(ctx, channel: Optional[discord.TextChannel] = None, *, reason: Optional[str] = None):
    ch = channel or ctx.channel
    await set_lock(ch, True, mod.audit_reason(ctx.author, reason or "lock"))
    await say(ctx, f"🔒 {ch.mention} locked." + (f" Reason: {reason}" if reason else ""), ephemeral=False)
    await modlog(ctx.guild, "Lock", f"{ch.mention} by {ctx.author.mention}", ORANGE, [("Reason", reason or "-")])


@bot.hybrid_command(name="unlock", description="Unlock a channel")
@app_commands.default_permissions(manage_channels=True)
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
@commands.bot_has_permissions(manage_channels=True, manage_roles=True)
async def unlock_cmd(ctx, channel: Optional[discord.TextChannel] = None, *, reason: Optional[str] = None):
    ch = channel or ctx.channel
    await set_lock(ch, False, mod.audit_reason(ctx.author, reason or "unlock"))
    await say(ctx, f"🔓 {ch.mention} unlocked.", ephemeral=False)
    await modlog(ctx.guild, "Unlock", f"{ch.mention} by {ctx.author.mention}", GREEN, [("Reason", reason or "-")])


async def lockdown(guild, on, reason):
    """Lock every text channel @everyone can currently talk in; remember them for unlock."""
    n = 0
    if on:
        locked = []
        for ch in guild.text_channels:
            if not ch.permissions_for(guild.me).manage_roles:
                continue
            if ch.permissions_for(guild.default_role).send_messages:
                try:
                    await set_lock(ch, True, reason)
                    locked.append(ch.id)
                    n += 1
                except discord.HTTPException:
                    pass
        prev = store.guild_state(guild.id, "lockdown", []) or []
        store.set_guild_state(guild.id, "lockdown", sorted(set(prev) | set(locked)))
    else:
        for cid in store.guild_state(guild.id, "lockdown", []) or []:
            ch = guild.get_channel(cid)
            if ch:
                try:
                    await set_lock(ch, False, reason)
                    n += 1
                except discord.HTTPException:
                    pass
        store.set_guild_state(guild.id, "lockdown", [])
    return n


@bot.hybrid_command(name="lockdown", description="Lock or unlock every channel (raid mode)")
@app_commands.default_permissions(manage_channels=True)
@commands.guild_only()
@commands.has_permissions(manage_channels=True)
@commands.bot_has_permissions(manage_channels=True, manage_roles=True)
async def lockdown_cmd(ctx, mode: Literal["on", "off"], *, reason: Optional[str] = None):
    if ctx.interaction is not None:
        await ctx.defer()
    n = await lockdown(ctx.guild, mode == "on", mod.audit_reason(ctx.author, reason or f"lockdown {mode}"))
    await say(ctx, f"🚨 Lockdown ON: locked {n} channels." if mode == "on" else f"✅ Lockdown OFF: unlocked {n} channels.",
              ephemeral=False)
    await modlog(ctx.guild, f"Lockdown {mode.upper()}", f"{n} channels by {ctx.author.mention}", RED if mode == "on" else GREEN,
                 [("Reason", reason or "-")])


# ======================================================================== config

def _pct(x):
    return f"{x * 100:.0f}%"


@bot.hybrid_group(name="config", fallback="show", invoke_without_command=True, description="Bot settings")
@app_commands.default_permissions(manage_guild=True)
@commands.guild_only()
@commands.has_permissions(manage_guild=True)
async def config_grp(ctx):
    c = cfg(ctx.guild)
    def chans(ids):
        return ", ".join(f"<#{i}>" for i in ids) or "-"
    e = discord.Embed(title="Config", color=COLOR)
    e.add_field(name="Jump-in", value=f"{_pct(c['jump_rate'])} random, {_pct(c['topic_jump_rate'])} on Elon topics\n"
                                      f"cooldown {c['channel_cooldown']}s/channel, {c['user_cooldown']}s/user\n"
                                      f"tweet threshold {c['tweet_threshold']}", inline=False)
    e.add_field(name="Ignore channels", value=chans(c["ignore_channels"]))
    e.add_field(name="Roast-only channels", value=chans(c["roast_channels"]) if c["roast_channels"] else "all")
    e.add_field(name="Mod-log", value=f"<#{c['modlog']}>" if c["modlog"] else "off (set with config modlog)")
    e.add_field(name="Immune roles", value=", ".join(f"<@&{r}>" for r in c["immune_roles"]) or "-")
    e.add_field(name="Warns", value=f"auto-timeout {c['warn_timeout']} every {c['warn_limit']} warns")
    e.add_field(name="Anti-spam", value=("on" if c["spam_enabled"] else "off") +
                f": {c['flood_msgs']} msgs/{c['flood_secs']}s, {c['dup_count']} dupes/{c['dup_secs']}s, "
                f"{c['mention_limit']} mentions -> {c['spam_timeout']}", inline=False)
    e.add_field(name="Anti-raid", value=("on" if c["raid_enabled"] else "off") +
                f": {c['raid_joins']} joins/{c['raid_secs']}s -> {c['raid_action']}")
    e.add_field(name="Link filter", value=c["link_filter"])
    e.add_field(name="Bad-word filter", value=("on" if c["badwords_enabled"] else "off") + f" ({len(c['badwords'])} words)")
    e.add_field(name="Extra blocklist", value=f"{len(c['blocklist_extra'])} words")
    await say(ctx, embed=e)


def _cfgcmd(name, description):
    def deco(fn):
        fn = commands.has_permissions(manage_guild=True)(fn)
        fn = commands.guild_only()(fn)
        return config_grp.command(name=name, description=description)(fn)
    return deco


async def _saved(ctx, what):
    await say(ctx, f"✅ {what}", ephemeral=False)
    await modlog(ctx.guild, "Config", f"{ctx.author.mention}: {what}", COLOR)


@_cfgcmd("jumprate", "Random jump-in chance in percent (default 8)")
async def cfg_jumprate(ctx, percent: commands.Range[float, 0, 100]):
    store.set_cfg(ctx.guild.id, "jump_rate", round(percent / 100, 4))
    await _saved(ctx, f"Jump-in rate {percent:g}%")


@_cfgcmd("topicrate", "Jump-in chance when someone talks Elon topics, in percent (default 25)")
async def cfg_topicrate(ctx, percent: commands.Range[float, 0, 100]):
    store.set_cfg(ctx.guild.id, "topic_jump_rate", round(percent / 100, 4))
    await _saved(ctx, f"Topic jump-in rate {percent:g}%")


@_cfgcmd("cooldowns", "Jump-in cooldowns in seconds: per channel and per user")
async def cfg_cooldowns(ctx, channel_seconds: commands.Range[int, 0, 86400], user_seconds: commands.Range[int, 0, 86400]):
    store.set_cfg(ctx.guild.id, "channel_cooldown", channel_seconds)
    store.set_cfg(ctx.guild.id, "user_cooldown", user_seconds)
    await _saved(ctx, f"Cooldowns: {channel_seconds}s/channel, {user_seconds}s/user")


@_cfgcmd("modlog", "Set the mod-log channel (omit to turn off)")
async def cfg_modlog(ctx, channel: Optional[discord.TextChannel] = None):
    if channel and not channel.permissions_for(ctx.guild.me).send_messages:
        return await say(ctx, f"⚠️ I can't send messages in {channel.mention}.")
    store.set_cfg(ctx.guild.id, "modlog", channel.id if channel else None)
    await _saved(ctx, f"Mod-log -> {channel.mention}" if channel else "Mod-log off")


def _toggle_list(gid, key, value, action):
    items = list(store.cfg(gid)[key])
    if action == "add" and value not in items:
        items.append(value)
    elif action == "remove" and value in items:
        items.remove(value)
    elif action == "clear":
        items = []
    store.set_cfg(gid, key, items)
    return items


@_cfgcmd("immune", "Roles immune to auto-mod")
async def cfg_immune(ctx, action: Literal["add", "remove"], role: discord.Role):
    _toggle_list(ctx.guild.id, "immune_roles", role.id, action)
    await _saved(ctx, f"Immune roles: {action} {role.name}")


@_cfgcmd("ignore", "Channels where the bot never jumps in (mentions still answered)")
async def cfg_ignore(ctx, action: Literal["add", "remove"], channel: Optional[discord.TextChannel] = None):
    ch = channel or ctx.channel
    _toggle_list(ctx.guild.id, "ignore_channels", ch.id, action)
    await _saved(ctx, f"Ignore list: {action} {ch.mention}")


@_cfgcmd("roastchannels", "Limit random jump-ins to these channels (clear = everywhere)")
async def cfg_roastchannels(ctx, action: Literal["add", "remove", "clear"], channel: Optional[discord.TextChannel] = None):
    ch = channel or ctx.channel
    items = _toggle_list(ctx.guild.id, "roast_channels", ch.id, action)
    await _saved(ctx, f"Roast channels: {', '.join(f'<#{i}>' for i in items) or 'everywhere'}")


@_cfgcmd("warnlimit", "Auto-timeout every N warns, for how long")
async def cfg_warnlimit(ctx, count: commands.Range[int, 0, 50], timeout: str = "1h"):
    secs = mod.parse_duration(timeout)
    store.set_cfg(ctx.guild.id, "warn_limit", count)
    store.set_cfg(ctx.guild.id, "warn_timeout", timeout)
    await _saved(ctx, f"Auto-timeout {mod.fmt_duration(secs)} every {count} warns" if count else "Auto-timeout on warns off")


@_cfgcmd("spam", "Anti-spam on/off and thresholds")
async def cfg_spam(ctx, mode: Literal["on", "off"], flood_msgs: Optional[int] = None, flood_secs: Optional[int] = None,
                   dup_count: Optional[int] = None, mention_limit: Optional[int] = None, timeout: Optional[str] = None):
    g = ctx.guild.id
    store.set_cfg(g, "spam_enabled", mode == "on")
    for key, val, lo, hi in (("flood_msgs", flood_msgs, 3, 50), ("flood_secs", flood_secs, 2, 120),
                             ("dup_count", dup_count, 2, 20), ("mention_limit", mention_limit, 2, 50)):
        if val is not None:
            if not lo <= val <= hi:
                return await say(ctx, f"⚠️ {key} must be {lo}-{hi}.")
            store.set_cfg(g, key, val)
    if timeout:
        mod.parse_duration(timeout)
        store.set_cfg(g, "spam_timeout", timeout)
    c = cfg(ctx.guild)
    await _saved(ctx, f"Anti-spam {mode}: {c['flood_msgs']} msgs/{c['flood_secs']}s, {c['dup_count']} dupes, "
                      f"{c['mention_limit']} mentions -> {c['spam_timeout']}")


@_cfgcmd("raid", "Anti-raid: on/off, or lock/alert as the action")
async def cfg_raid(ctx, mode: Literal["on", "off", "lock", "alert"], joins: Optional[int] = None, seconds: Optional[int] = None):
    g = ctx.guild.id
    if mode in ("on", "off"):
        store.set_cfg(g, "raid_enabled", mode == "on")
    else:
        store.set_cfg(g, "raid_enabled", True)
        store.set_cfg(g, "raid_action", mode)
    if joins is not None:
        store.set_cfg(g, "raid_joins", max(2, min(200, joins)))
    if seconds is not None:
        store.set_cfg(g, "raid_secs", max(5, min(3600, seconds)))
    c = cfg(ctx.guild)
    await _saved(ctx, f"Anti-raid {'on' if c['raid_enabled'] else 'off'}: {c['raid_joins']} joins/{c['raid_secs']}s -> {c['raid_action']}")


@_cfgcmd("links", "Link filter: off, invites (Discord invites only) or all")
async def cfg_links(ctx, mode: Literal["off", "invites", "all"]):
    store.set_cfg(ctx.guild.id, "link_filter", mode)
    await _saved(ctx, f"Link filter: {mode}")


@_cfgcmd("badwords", "Bad-word filter: on/off/add/remove/list")
async def cfg_badwords(ctx, action: Literal["on", "off", "add", "remove", "list"], *, word: Optional[str] = None):
    g = ctx.guild.id
    if action in ("on", "off"):
        store.set_cfg(g, "badwords_enabled", action == "on")
        return await _saved(ctx, f"Bad-word filter {action}")
    if action == "list":
        words = cfg(ctx.guild)["badwords"]
        return await say(ctx, ("Bad words: ||" + ", ".join(words) + "||") if words else "No bad words set.")
    if not word:
        return await say(ctx, "⚠️ Which word?")
    _toggle_list(g, "badwords", word.lower().strip(), action)
    if ctx.interaction is None:
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
    await say(ctx, f"✅ Bad word {action}ed.")


@_cfgcmd("blocklist", "Extra words the bot must never repeat back")
async def cfg_blocklist(ctx, action: Literal["add", "remove", "list"], *, word: Optional[str] = None):
    g = ctx.guild.id
    if action == "list":
        words = cfg(ctx.guild)["blocklist_extra"]
        return await say(ctx, ("Extra blocklist: ||" + ", ".join(words) + "||") if words else "No extra words (built-in slur list always on).")
    if not word:
        return await say(ctx, "⚠️ Which word?")
    _toggle_list(g, "blocklist_extra", word.lower().strip(), action)
    if ctx.interaction is None:
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
    await say(ctx, f"✅ Blocklist {action}ed.")


@_cfgcmd("tweetthreshold", "How relevant a tweet must be to auto-post (0.05 loose - 0.6 strict, default 0.15)")
async def cfg_tweetthreshold(ctx, value: commands.Range[float, 0.05, 0.6]):
    store.set_cfg(ctx.guild.id, "tweet_threshold", round(value, 3))
    await _saved(ctx, f"Tweet threshold {value:.2f}")


# ======================================================================== main

if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("Error: DISCORD_TOKEN not found in .env")
    else:
        print("Starting bot...")
        try:
            bot.run(token)
        finally:
            store.save()
