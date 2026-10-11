import discord
from discord.ext import commands
import random
import asyncio
import os
import csv
from dotenv import load_dotenv

load_dotenv()

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# Load real Elon tweets (optional)
ELON_QUOTES = []
csv_path = 'elon_tweets_sample.csv'
if os.path.exists(csv_path):
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                text = row.get('Tweet.content', '').strip()
                lang = row.get('Language', '')
                if (lang == 'en' and
                    isinstance(text, str) and
                    25 < len(text) < 250 and
                    not text.startswith('http') and
                    not text.startswith('@') and
                    'http' not in text.lower()[:50]):
                    ELON_QUOTES.append(text)
        print(f"Loaded {len(ELON_QUOTES)} Elon quotes from CSV")
    except Exception as e:
        print(f"Error reading CSV: {e}")
else:
    print(f"No {csv_path} found — using fallback quotes only")

if not ELON_QUOTES:
    ELON_QUOTES = [
        "The future is going to be wild.",
        "Let's make life multiplanetary.",
        "Occupy Mars.",
        "The most entertaining outcome is the most likely.",
    ]

# Extra toxic / shit-talk lines mixed in
ROASTS = [
    "That's the dumbest shit I've heard all day.",
    "You type like your brain is buffering.",
    "Bro really thought that was a take. Embarrassing.",
    "I've seen better arguments from a Roomba.",
    "You're speedrunning getting ratio'd.",
    "That was so bad it looped back around to funny.",
    "Imagine being this confidently wrong.",
    "Hold on, let me get the clip of that L.",
    "This energy is why nobody takes you seriously.",
    "You keep talking like the mute button is broken.",
    "Nice try. Still ass.",
    "That's not even wrong, that's just loud.",
    "I'd explain it but I don't have the crayons.",
    "You volunteered for the roast. Respect the commitment.",
    "Okay but counterpoint: no.",
]

JUMP_CHANCE = 0.35  # raised from 0.12

def get_line():
    if random.random() < 0.65 and ELON_QUOTES:
        return random.choice(ELON_QUOTES)
    return random.choice(ROASTS)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')
    await bot.change_presence(activity=discord.Game(name="talking shit | !help"))

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Instant response if mentioned
    if bot.user in message.mentions:
        line = get_line()
        await message.reply(line, mention_author=False)
        return

    # Higher chance random jump-in
    if random.random() < JUMP_CHANCE and len(message.content) > 5:
        line = get_line()
        if random.random() < 0.7:
            await message.reply(line, mention_author=False)
        else:
            await message.channel.send(line)

    await bot.process_commands(message)

# ---- Moderation ----
@bot.command(name='kick')
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason="No reason given"):
    await member.kick(reason=reason)
    await ctx.send(f'Kicked {member.mention}. Reason: {reason}')

@bot.command(name='ban')
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason="No reason given"):
    await member.ban(reason=reason)
    await ctx.send(f'Banned {member.mention}. Reason: {reason}')

@bot.command(name='clear', aliases=['purge', 'delete'])
@commands.has_permissions(manage_messages=True)
async def clear(ctx, amount: int = 5):
    if amount > 100:
        amount = 100
    deleted = await ctx.channel.purge(limit=amount + 1)
    msg = await ctx.send(f'Deleted {len(deleted)-1} messages.')
    await asyncio.sleep(3)
    await msg.delete()

@bot.command(name='roast')
async def roast_cmd(ctx, member: discord.Member = None):
    target = member or ctx.author
    line = get_line()
    await ctx.send(f'{target.mention} {line}')

@bot.command(name='elon', aliases=['quote', 'tweet'])
async def elon_cmd(ctx):
    if not ELON_QUOTES:
        await ctx.send("No quotes loaded.")
        return
    quote = random.choice(ELON_QUOTES)
    await ctx.send(quote)

@bot.command(name='help')
async def help_cmd(ctx):
    embed = discord.Embed(title="Elon Shit-Talk Bot", color=0x1DA1F2)
    embed.add_field(name="!kick @user [reason]", value="Kick a member", inline=False)
    embed.add_field(name="!ban @user [reason]", value="Ban a member", inline=False)
    embed.add_field(name="!clear [number]", value="Delete messages (default 5)", inline=False)
    embed.add_field(name="!roast [@user]", value="Force a roast / Elon line", inline=False)
    embed.add_field(name="!elon / !quote", value="Drop a real Elon tweet", inline=False)
    embed.add_field(name="Auto", value="Replies instantly when mentioned. ~35% chance to jump into other messages.", inline=False)
    await ctx.send(embed=embed)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You don't have permission for that.")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("Couldn't find that user.")
    else:
        print(error)

if __name__ == '__main__':
    token = os.getenv('DISCORD_TOKEN')
    if not token:
        print("Error: DISCORD_TOKEN not found in .env")
        print("Create a .env file with: DISCORD_TOKEN=your_bot_token")
    else:
        print("Starting bot...")
        bot.run(token)
