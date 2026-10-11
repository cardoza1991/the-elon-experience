# The Elon Experience

Roast bot + pro moderation for Discord. "AI without AI": no LLMs, no models,
no cloud calls. Just an intent router, a pure-python TF-IDF index over real
Elon tweets, ~450 hand-written lines, and a memory of what you said.

## Files
- `bot.py` - Discord glue: events, hybrid commands (every command works as `!cmd` and `/cmd`)
- `brain.py` - intent router, TF-IDF tweet matcher, per-user memory, no-repeat picking, slur guard, jump-in policy
- `lines.py` - the line banks (edit freely; placeholders `{name} {topic} {snippet} {callback} {fav} {count}`)
- `moderation.py` - durations, warns, anti-spam, anti-raid, link/bad-word filters
- `store.py` - JSON persistence (`data/state.json`, gitignored) + default config
- `tests/` - `.venv/bin/python -m unittest discover -s tests -v`

## How it decides to talk
- @mention or reply to the bot: always answers (3s per-user anti-abuse throttle).
- Otherwise: 8% random jump-in (25% if you're talking Tesla/SpaceX/Mars/X/AI/money...),
  max once per 45s per channel and once per 120s per person. Typing indicator + human-ish delay.
- Insults at the bot -> comeback. "test"/keyboard smash -> mockery. Questions -> dodge.
  lol/lmao -> reply. Greetings/goodbyes -> reply. Elon topics -> a matching real tweet
  only if TF-IDF relevance >= 0.15, otherwise a topic roast. Everything else -> a roast,
  sometimes a callback to something you said earlier.
- Never repeats a line to the same person within their last 30. Never echoes slurs.

## Commands
Run `!help` or `/help` in Discord for the full list.

## Bot permissions needed
Moderate Members, Kick Members, Ban Members, Manage Messages, Manage Channels,
Manage Roles (for lock/unlock), Read Message History, Send Messages, Embed Links.
The bot's role must be ABOVE the roles of anyone it should timeout/kick/ban.
Invite with scopes `bot` + `applications.commands`. Enable Message Content and
Server Members intents in the Developer Portal.

## Common issues
- **"session has been invalidated"**: Reset the bot token in the Developer Portal and make sure Message Content + Server Members intents are enabled. Kill any other running copies of the bot.
- CSV not found: normal if you didn't add `elon_tweets_sample.csv` (it's gitignored). The bot still works with a small fallback set of quotes.

## Run
systemd user service `elon-bot` (`systemctl --user restart elon-bot`,
`journalctl --user -u elon-bot -f`). Token lives in `.env` (never commit it).
