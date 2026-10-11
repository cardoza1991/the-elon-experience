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

## Conversation mode (convo.py, convo_lines.py)
- Mention or reply to the bot (or say "elon"/"bot") and a session opens for that person in that channel
  (default 240s, refreshed every exchange). Follow-ups get answered without another mention, as long as
  nobody else interjected; otherwise they need to address the bot (you/ur/elon/bot/a question).
- Up to 4 partners per channel; max 8 conversational replies/min per channel in group chats (a quiet 1:1
  is never capped); when 6+ people talk in a minute it only answers when addressed.
- Dialogue state per session: subject, last bot line, what the bot asked (yes/no vs open), pronoun
  resolution ("it"/"that" -> the subject), "what about you".
- ELIZA-style patterns with slot capture + reflection (I am / I think / I want / I like / I hate / my X /
  you are / are you / do you / can you / why / because / should I / what is / opinions), small talk,
  jokes, who-are-you, facts it remembers (job, place, nickname, likes) for callbacks.
- Fallback chain: pattern -> topic/tweet (only above the TF-IDF threshold) -> contextual roast quoting
  their words -> generic. bye/gtg/gn ends politely with a parting shot (90s re-open cooldown).
- Identity statements (sexuality, religion, race, disability...) are acknowledged, never roasted or
  stored. Self-harm statements get a short no-roast pointer to 988 / findahelpline.com.
- `!chat on|off|status [#channel]`, `!config convo <timeout> [partners] [per_min] [busy]`.

## Abuse retaliation
Insults aimed at the bot (profanity/insults + you/bot/elon, or inside a session; "stfu", "trash bot"...)
always get fired back at, bypassing jump-in odds and group caps. Per-user abuse level rises +1 per hit and
halves every 180s: tier 1 snarky (<1.8), tier 2 savage (<3.5), tier 3 scorched earth. Rate limit: one
fire-back per 4s and 6/min per user. stfu/shut up fire back instead of ending the session. Same slur
guard; no protected-trait attacks. `!config abuse on|off [half_life] [cooldown] [per_min]`.

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
