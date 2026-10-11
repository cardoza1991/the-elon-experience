# Elon Shit-Talk Bot

Aggressive Discord bot that randomly jumps into conversations with real Elon Musk tweets or custom roasts, plus basic moderation.

## Setup

1. Create a bot at https://discord.com/developers/applications
   - New Application → Bot → Reset Token (copy it)
   - Enable **Message Content Intent** and **Server Members Intent**

2. Invite the bot with: Send Messages, Read Message History, Manage Messages, Kick Members, Ban Members, Embed Links

3. Install:
   ```bash
   pip install -r requirements.txt
   ```

4. Copy `.env.example` to `.env` and add your token:
   ```
   DISCORD_TOKEN=your_actual_token
   ```

5. Download a sample of Elon tweets (or use your own CSV with 'Tweet.content' and 'Language' columns) and name it `elon_tweets_sample.csv` in the same folder.

6. Run:
   ```bash
   python bot.py
   ```

## Commands

- `!kick @user [reason]`
- `!ban @user [reason]`
- `!clear [number]`
- `!roast [@user]` — forces a roast or Elon line
- `!elon` / `!quote` — drops a real Elon tweet
- `!help`

It has a ~12% chance to randomly reply in any channel. Adjust `JUMP_CHANCE` in `bot.py` if needed.

Have fun. Don't get the server banned.
