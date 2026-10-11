"""convo.py - multi-turn conversation mode. Still no AI: sessions + patterns + memory.

Flow per human message in a guild channel:
  gate()  -> decides whether the bot should answer (session rules, caps, cooldowns)
  reply() -> builds the answer with the fallback chain:
             end/crisis -> answer to the bot's own question -> jab/disbelief ->
             specific pattern (with slot capture + reflection) -> topic/tweet ->
             roast banks for insults/greetings/etc. -> contextual roast -> generic
Both are pure python and testable without Discord (inject a clock and rng).
"""
import re
import time
from collections import deque

import brain
import convo_lines as C
from brain import STOP, clean_text, content_terms, detect_topic, snippet_of, tokens

# ------------------------------------------------------------------ reflection

_REFLECT = {
    "i": "you", "me": "you", "my": "your", "mine": "yours", "myself": "yourself", "am": "are",
    "i'm": "you're", "im": "you're", "i've": "you've", "ive": "you've", "i'll": "you'll", "i'd": "you'd",
    "your": "my", "ur": "my", "yer": "my", "yours": "mine", "yourself": "myself", "urself": "myself",
    "you're": "I'm", "youre": "I'm", "you've": "I've", "you'll": "I'll", "you'd": "I'd",
    "we": "you", "us": "you", "our": "your",
}
# multi-word swaps first so verbs agree ('you aren't' -> 'I'm not', never 'me aren't')
_BIGRAMS = [
    (r"\b(you|u) (are|r) not\b", "I am not"), (r"\b(you|u) (aren'?t|arent)\b", "I'm not"),
    (r"\b(you'?re|youre) not\b", "I'm not"), (r"\b(you|u) (are|r)\b", "I am"), (r"\b(aren'?t|arent) (you|u)\b", "am I not"),
    (r"\b(are|r) (you|u)\b", "am I"), (r"\b(you|u) (weren'?t|wasn'?t)\b", "I wasn't"), (r"\b(you|u) (were|was)\b", "I was"),
    (r"\b(were|was) (you|u)\b", "was I"), (r"\b(you|u) is\b", "I am"), (r"\b(you|u) has\b", "I have"),
    (r"\b(you|u) does\b", "I do"), (r"\b(you|u) doesn'?t\b", "I don't"),
    (r"\bi'?m not\b", "you're not"), (r"\bi am not\b", "you are not"), (r"\bi am\b", "you are"),
    (r"\bi wasn'?t\b", "you weren't"), (r"\bi was\b", "you were"), (r"\bam i\b", "are you"), (r"\bwas i\b", "were you"),
    (r"\bi is\b", "you are"), (r"\bi aint\b", "you aint"),
]
# words after which a bare 'you' is the subject of a new clause ('because you suck' -> 'because I suck')
_SUBJ_PREV = {"because", "cuz", "cause", "bc", "coz", "since", "that", "if", "when", "and", "but", "so", "think",
              "know", "said", "says", "like", "until", "unless", "while", "where", "why", "how", "what", "who",
              "whether", "or", "maybe", "then", "cos", "guess", "hope", "bet", "feel", "wish"}
_AGREE_FIX = [
    (r"\bI (are|is)\b", "I am"), (r"\bI (aren'?t|isn'?t|arent)\b", "I'm not"), (r"\bI were\b", "I was"),
    (r"\bI weren'?t\b", "I wasn't"), (r"\bI has\b", "I have"), (r"\bI does\b", "I do"), (r"\bI doesn'?t\b", "I don't"),
    (r"\bme (are|is|am)\b", "I am"), (r"\bme (aren'?t|isn'?t)\b", "I'm not"), (r"\bme (was|were)\b", "I was"),
    (r"\bI'm (are|is|am)\b", "I'm"), (r"\byou're (are|is|am)\b", "you're"), (r"\bI am (are|is|am)\b", "I am"),
    (r"\byou am\b", "you are"), (r"\byou is\b", "you are"), (r"\byou was\b", "you were"), (r"\byou wasn'?t\b", "you weren't"),
]
AGREE_BAD_RE = re.compile(r"\b(me (are|am|is|aren'?t|isn'?t|were|was|do|don'?t|have|can|will)|"
                          r"I (are|aren'?t|is|isn'?t|were|weren'?t|has|does|doesn'?t)|you (am|is|isn'?t|was|wasn'?t)|"
                          r"myself (are|is|am)|I'm (are|is|am)|I am (are|is|am))\b")
_TRAIL_FILLER = re.compile(r"(\s+(lol|lmao|lmfao|tho|though|bro|man|dude|haha+|fr|ngl|tbh|rn|honestly|lowkey))+$", re.I)
PRONOUN_REFS = {"it", "that", "this", "them", "those", "that one", "this one", "him", "her", "these"}


def reflect(fragment):
    """Swap speaker/bot perspective with verb agreement: 'you aren't making sense' -> 'I'm not making sense'."""
    s = " " + fragment.strip() + " "
    marks = {}
    for i, (rx, rep) in enumerate(_BIGRAMS):
        key = f"\x00{i}\x00"
        s = re.sub(rx, f" {key} ", s, flags=re.I)
        marks[key] = rep
    words = s.split()
    out = []
    for i, w in enumerate(words):
        if w in marks:
            out.append(marks[w])
            continue
        low = w.lower()
        if low in ("you", "u", "ya"):
            prev = words[i - 1].lower() if i else ""
            subject = (i == 0 or prev in _SUBJ_PREV) and i + 1 < len(words)
            out.append("I" if subject else "me")
            continue
        out.append(_REFLECT.get(low, w))
    text = " ".join(out)
    for rx, rep in _AGREE_FIX:
        text = re.sub(rx, rep, text)
    return text


def clean_slot(x, max_words=8, max_len=50):
    """Tidy a captured slot. Too long -> '' (never a truncated '...' echo)."""
    x = re.sub(r"[\s.!?,;:~]+$", "", x.strip())
    x = _TRAIL_FILLER.sub("", x).strip(" .!?,;:")
    x = _TRAIL_VOCATIVE.sub("", x).strip(" .!?,;:")
    x = x.replace('"', "'").replace("`", "'")
    if len(x.split()) > max_words or len(x) > max_len:
        return ""
    return x.strip()


_TRAIL_VOCATIVE = re.compile(r"(\s+(elon|bot|clanker|musky?|elon (bot|musky?)|the elon experience|lol|lmao|bro))+$", re.I)
CONJ_START = re.compile(r"^(because|cuz|cause|bc|coz|that|and|but|so|if|when|since|which|who|whom|whose|though|although|"
                        r"while|or|as|then|than|whether|unless|until|after|before|where|how|what)\b", re.I)
VERBY = {"is", "am", "are", "was", "were", "be", "been", "being", "do", "does", "did", "done", "doing", "have", "has", "had",
         "made", "make", "makes", "said", "say", "says", "went", "go", "goes", "got", "get", "gets", "create", "creates",
         "think", "thinks", "know", "knows", "knew", "view", "views", "act", "acts", "see", "sees", "saw", "seen", "take",
         "took", "taken", "give", "gave", "come", "came", "feel", "felt", "want", "wants", "need", "needs", "can", "could",
         "would", "should", "will", "shall", "may", "might", "must", "let", "keep", "kept", "told", "tell", "tells",
         "ask", "asks", "try", "tries", "build", "built", "wrote", "write", "ran", "put", "seems", "seem", "looks",
         "become", "became", "treat", "treats", "find", "found", "stopped", "hope", "hopes", "cause", "causes",
         "doesnt", "dont", "didnt", "isnt", "wasnt", "cant", "wont", "ate", "broke", "died", "fell", "stole", "quit",
         "drove", "flew", "sold", "bought", "thought", "caught", "sent", "spent", "paid", "slept", "woke", "keeps",
         "loves", "hates", "likes", "just", "always", "never", "still", "won't", "can't", "don't", "doesn't", "didn't",
         "isn't", "wasn't", "listens", "says", "said", "won", "lost", "left", "hit", "bit", "ran", "runs", "goes"}
ED_OK = {"bed", "red", "seed", "speed", "weed", "need", "feed", "greed", "shed", "sled", "bread", "head", "thread",
         "creed", "breed", "steed", "tweed", "hundred", "sacred", "naked", "wicked", "beloved", "shred", "ted", "ned",
         "fred", "ed", "led", "wed", "fled", "tired", "bored", "scared", "excited", "confused", "worried", "annoyed",
         "stressed", "depressed", "married", "obsessed", "interested", "pissed", "tied", "baked", "cooked", "wasted",
         "blessed", "cursed", "based", "goated", "jaded", "stoned", "hooked", "embarrassed", "exhausted", "frustrated",
         "motivated", "relaxed", "surprised", "unemployed", "employed", "retired", "divorced", "engaged", "talented",
         "underrated", "overrated", "outdated", "sophisticated", "dedicated", "complicated", "weird", "speed", "hatred",
         "kindred", "biased", "spoiled", "ruined", "glazed", "cracked", "washed", "fried", "toasted", "wired", "stuffed"}
CLAUSE_SUBJ = {"i", "you", "he", "she", "they", "we", "nobody", "everyone", "everybody", "someone", "somebody", "people",
               "it", "no one", "noone", "anyone"}
NOUN_SLOTS = {"opinion", "what_is", "i_like", "i_hate", "my_x", "fact_place", "fact_job", "fact_iam", "fact_name"}
COMPLEMENT_SLOTS = {"i_am", "you_are", "are_you", "i_feel"}  # 'I am {x}': x must not start with a verb like 'created'


def slot_kind(pkey):
    return "noun" if pkey in NOUN_SLOTS else "complement" if pkey in COMPLEMENT_SLOTS else "free"


def verbish(word):
    w = word.lower().strip("'.,!?")
    return w in VERBY or (len(w) > 4 and w.endswith("ed") and w not in ED_OK)


def slot_ok(x, kind="free", max_words=6):
    """Is this captured slot safe to drop into a template without broken grammar?"""
    if not x or "..." in x or len(x) > 50 or re.search(r"[,;:()\[\]]", x):
        return False
    words = x.split()
    if len(words) > max_words or CONJ_START.match(x) or AGREE_BAD_RE.search(x):
        return False
    if brain.abusive(x) or brain.GRIEF_RE.search(x):
        return False  # 'I think gfys is fine' never happens
    if not [w for w in words if w.lower().strip("'") not in STOP and len(w.strip("'")) > 1]:
        return False
    noun = kind == "noun"
    if verbish(words[0]) and (kind in ("noun", "complement") or len(words) == 1):
        return False
    if kind in ("noun", "complement"):
        low = [w.lower().strip("'") for w in words]
        if any(w in VERBY for w in low[1:]):
            return False  # a whole clause ('nobody listens to me', 'car broke down'), not a thing
        if any(w in CLAUSE_SUBJ for w in low[:-1]) and any(w.endswith("s") for w in low[1:]):
            return False
    if noun and len(words) > 1 and words[1].lower() in ("me", "you", "him", "her", "them", "us", "it"):
        return False  # 'loved you' style verb + object
    return True


SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=\S)")
EMOTION_RE = re.compile(r"\b(sorry|hope|sad|happy|angry|mad|upset|hurt|love|hate|scared|afraid|worried|lonely|glad|miss|"
                        r"feel|feeling|cruel|rude|mean|kind|nice)\b", re.I)


def salient_sentence(t):
    """For long multi-sentence messages: the sentence most worth answering (aimed at the bot,
    emotional, a question) instead of a random fragment."""
    sents = [s.strip() for s in SENT_SPLIT.split(t) if s.strip()]
    if len(sents) <= 1:
        return t
    best, best_sc = sents[0], -99
    for i, s in enumerate(sents):
        sc = 0
        if re.search(r"\b(you|u|ur|your|yourself|elon|bot)\b", s, re.I):
            sc += 3
        if s.rstrip().endswith("?"):
            sc += 2
        if EMOTION_RE.search(s):
            sc += 2
        if re.match(r"^i\b", s, re.I):
            sc += 1
        n = len(s.split())
        if n > 18:
            sc -= 2
        if n < 3:
            sc -= 2
        if sc > best_sc:
            best, best_sc = s, sc
    return best


def hostile_sentence(t):
    """The most hostile / bot-directed sentence of a long message (routes keyed comebacks and the {insult} word)."""
    parts = [p for p in SENT_SPLIT.split(t) if p.strip()]
    if len(parts) <= 1:
        return t
    best, best_sc = parts[-1], -1
    for p in parts:
        sc = (3 * bool(EXIST_RE.search(p)) + 3 * bool(STFU_RE.search(p)) + 2 * bool(PROFANITY_RE.search(p))
              + 2 * bool(INSULT_WORD_RE.search(p)) + bool(AIM_RE.search(p)) + bool(NAME_RE.search(p))
              + 2 * bool(GAMER_TAUNT_RE.search(p) or GRASS_RE.search(p)))
        if sc > best_sc:
            best, best_sc = p, sc
    return best


# ------------------------------------------------------------------ patterns

def _rx(p):
    return re.compile(p, re.I)


END_START_RE = _rx(r"^\W*((ok|okay|alright|aight|well|anyway|k)[\s,.!]+)?(bye+|goodbye|gtg|g2g|got ?ta go|gotta go|i gotta go|i(?:'| a)?m out|i'?m off|"
                   r"later|laters|cya|see ya|see you|gn|good ?night|peace( out)?|night|ok bye|alright bye|ttyl|"
                   r"leave me alone|go away|stop talking|be quiet|i'?m done|done talking)\b")
END_ANY_RE = _rx(r"\b(leave me alone|stop talking to me|stop replying|i'?m done (with|talking to) (you|u)|gtg|gotta go)\b")
# ---- abuse aimed at the bot (fires back instead of ending) ----
PROFANITY_RE = _rx(r"\b(failure|stupidity|brainless|mindless|senseless|idiotic|sad attempt|pathetic attempt|fuck\w*|f+u+|stfu|gtfo|gfys|kys|foh|smd|stfd|kill (your ?self|urself)|go die|shit\w*|bitch\w*|dick\w*|cunt\w*|ass(hole|hat|wipe)|(your|ur|my) ass|dumbass|jackass|"
                   r"bastard|prick|twat|wanker|douche\w*|motherfuck\w*|piss off|screw (you|u|off)|suck my|eat shit|"
                   r"shut (the fuck |tf )?up|bozo|loser|idiot|moron|stupid|dumb|trash|garbage|useless|clown|clanker|"
                   r"pathetic|cringe|lame|worthless|annoying|ugly|suck|sucks|hate (you|u|this))\b")
BENIGN_RE = _rx(r"\b(fuck (yeah|yes|yea|ya)|hell yeah|holy shit|no shit|good shit|the shit|shit'?s (fire|crazy|wild|funny)|"
                r"fucking (funny|hilarious|great|right|true)|so fucking (funny|good))\b")
THIRD_PARTY_RE = _rx(r"\b(my|his|her|their|he|she|they|him|them|that guy|this guy|boss|mom|dad|teacher|team)\b")
INSULT_WORD_RE = _rx(r"\b(failure|stupidity|brainless|mindless|senseless|idiotic|trash|garbage|useless|clown|clanker|bozo|loser|idiot|moron|stupid|dumb|pathetic|cringe|lame|"
                     r"worthless|annoying|ugly|bitch|dickhead|dick|asshole|dumbass|jackass|bastard|prick|twat|wanker|"
                     r"douche(bag)?|cunt|mid|boring|dogshit|shit bot|bad bot|npc|joke|clanker|bozo)\b")
AIM_RE = _rx(r"\b(you|u|ur|your|you'?re|youre|yourself|ya|bot|elon|clanker|this thing)\b")
STFU_RE = _rx(r"\b(stfu|gtfo|gfys|kys|foh|smd|stfd|go die|kill (your ?self|urself)|shut (the fuck |tf )?up|fuck (you|u|off|this bot)|f+ ?u+|piss off|eat shit|screw (you|u)|"
              r"suck my|kiss my ass)\b")
CRISIS_RE = _rx(r"\b(kill myself|killing myself|suicid\w*|want to die|wanna die|end it all|end my life|self[- ]harm|"
                r"cut myself|don'?t want to (live|be alive))\b")
YES_RE = _rx(r"^\W*(yes+|yeah+|yea+|yep|yup|ya|ye|sure|of course|obviously|definitely|absolutely|100( ?%)?|i do|i am|"
             r"i did|true|correct|affirmative|hell yeah|ofc|duh)\b")
NO_RE = _rx(r"^\W*(no+|nah+|nope|never|not really|i don'?t|i do not|hell no|negative|absolutely not|no chance)\b")
MAYBE_RE = _rx(r"^\W*(maybe|idk|i don'?t know|kinda|sort of|kind of|perhaps|not sure|dunno|possibly|eh)\b")
DISBELIEF_RE = _rx(r"^\W*(no way|no shot|nah no way|bro what|bruh+|really|deadass|you serious|are you serious|"
                   r"seriously|wow+|damn+|sheesh|wait what|excuse me|ayo+|how dare you|rude|oof|bro+|omg|"
                   r"you did not|u did not|ouch|that'?s crazy|crazy)\W*(lol|lmao|bro|dude)?\W*$")
JAB_RE = _rx(r"(not much of a (conversationalist|talker|convo)|conversationalist|(you|u|this bot|the bot|it) "
             r"(can'?t|cant|don'?t|doesn'?t) (talk|hold a conversation|chat|converse)|\bboring\b|"
             r"that'?s all (you|u) (got|have)|is that all (you|u)|you'?re not (even )?funny|not funny|"
             r"(you|u) (aren'?t|arent|are not|r not|ain'?t) (even |that |very |)funny|"
             r"(you|u) only (say|know) (one|the same)|repetitive|say something (else|real|new|smart)|"
             r"\bweak( roast| ass)?\b|that was weak|try harder|mid roast|dry (ass )?(bot|convo|conversation)|"
             r"(you|u) suck at (talking|this|conversations?)|(you|u)'?re? (so )?dry|\bstill waiting\b)")
CONFUSED_RE = _rx(r"^\W*(what+|huh+|wdym|what do (you|u) mean|eh|come again|say what|wym|explain)\W*$")
NAME_RE = _rx(r"\b(elon|bot|clanker|elon ?experience|the elon experience)\b")
BOT_NAME_WORDS = {"elon", "bot", "clanker", "experience", "musk"}
_LEAD_FILLER = re.compile(r"^(even|really|actually|just|ever|like,|literally|honestly|still)\s+", re.I)
SUBJECT_KEYS = {"opinion", "i_like", "i_hate", "my_x", "what_is", "i_want", "i_think", "should_i", "i_feel"}
ADDRESS_RE = _rx(r"\b(you|u|ur|your|yours|you'?re|youre|yourself|elon|bot)\b|\?\s*$")
SENSITIVE_RE = brain.PROTECTED_RE
# a protected trait thrown at the bot as an insult ('yer just so gay', 'gay bot'): clean, deflating comeback
TRAIT_INSULT_RE = _rx(r"\b(you|u|ur|yer|ya|you'?re|youre|u r|bot|elon|clanker|this (bot|thing)|it)\s+(are\s+|r\s+|is\s+|'?re\s+)?"
                      r"((just|so|such|a|an|really|kinda|lowkey|hella|mad|super|totally|literally|fr|straight up)\s+)*"
                      r"(gay|homo|queer|autistic|retarded|tranny|lesbian|disabled)\b|"
                      r"\b(so|that'?s|thats|how|sounds|super|hella) (gay|homo|autistic|retarded)\b|"
                      r"\b(gay|homo|autistic|retarded)( ass)? (bot|clanker|elon|ai|robot)\b")
SLANG_ABUSE = r"gfys|kys|foh|smd|stfu|gtfo|stfd"
# whole-message taunts / dismissive banter (only when aimed at the bot: in a session, a mention or naming it)
GAMER_TAUNT_RE = _rx(r"^\W*((ok|okay|lol|lmao|bro|nah|imagine|massive|huge|big)\W+)?(get good|git gud|skill issue|"
                     r"ratio(ed)?|l \+ ratio|\bl\b|cope( harder)?|seethe|mald(ing)?|cry about it|cry more|stay mad|"
                     r"npc( behavior| behaviour| energy)?|mid|womp womp|nobody asked|who asked|didn'?t ask|ez|"
                     r"(l|npc|mid|bozo|trash) bot|bot (diff|is mid|is trash)|diff)"
                     r"(\W+(bot|elon|clanker|bro|lol|lmao|lmfao|kid|buddy|pal|harder|\+ ratio|\+ l))*\W*$")
GRASS_RE = _rx(r"^\W*((ok|okay|lol|bro|nah|just|go)\W+)?(touch (some )?grass|go outside|get a life|log off|"
               r"smoke a (phatty|fatty|joint|blunt|bowl)|hit the gym|go to sleep|go to bed|take a shower|"
               r"get off (the )?(internet|discord)|go outside and play)"
               r"(\W+(bot|elon|clanker|bro|lol|lmao|kid|buddy|pal|already|dude))*\W*$")
MOCK_PITY_RE = _rx(r"^\W*(oh+|aw+|aww+|ohh+|ah+)[\s,!.]+(you |the |my |)poor\b|\bpoor (baby|lil|little) (bot|guy|thing|elon)?|"
                   r"^\W*poor baby\b|\b(is|did) (the |my |lil |little )?(bot|elon|clanker|robot|baby) (sad|crying|upset|mad|"
                   r"hurt|get (its|his) feelings hurt)|\bcry (more|harder|me a river)\b|\bboo ?hoo\b|\bsowwy\b|"
                   r"\bdid i hurt (your|ur|its|the bot'?s) (feelings|feewings)|\bwah+ wah+\b|\baww+ (is|did) (it|he|the|someone)\b|"
                   r"\b(so|how) sad\W*(bot|elon)?\W*$")
CALLED_RE = _rx(r"\b(do|does|would|will|did) (you|u|it) (hate|mind|like|care|get (mad|upset|angry)|hate it|feel some ?type of way)"
                r"( (about|when|that))?( (being|getting|people|me|i|we|when people))? ?(call(ed|ing)?|nam(ed|ing)|referr(ed|ing))\b|"
                r"\bcan (i|we) (call|name) (you|u)\b|\b(stop|keep) calling (you|u)\b|\bwhat should (i|we) call (you|u)\b|"
                r"\bi'?(ll|m gonna|m going to|ma) (keep )?(calling|call) (you|u)\b")
BROKE_RE = _rx(r"\bi (think i |just |might have |prob(ably)? )?(broke|broken|crashed|killed|bricked|fried|glitched) "
               r"(it|you|u|the bot|this bot|this thing|elon|him|ur brain|your brain|the ai)\b|"
               r"\b(you'?re|youre|u r|ur|you are|it'?s|its|bot is|bot'?s|is it|are you|r u|is the bot|elon is|is elon) "
               r"(so |completely |totally |fully |just |kinda |)(broken|broke|glitch(ing|ed|y)?|bugged|bugging|buggy|"
               r"malfunctioning|lagging|crashing|crashed|short ?circuit(ing|ed)?|fried|cooked|dead|down)\b|"
               r"\b(you|u|it|bot|elon) (broke|crashed|glitched|bugged out|died|short ?circuited)\b")
NO_SENSE_RE = _rx(r"\b(you|u|ur|you'?re|youre|bot|elon|it|this|that)( are| r| is| re|'re)?( not| n'?t)? ?"
                  r"(aren'?t|arent|isn'?t|don'?t|doesn'?t|do not|does not|can'?t|not|never)? ?(even )?"
                  r"(make|making|makes|talking|speaking) (any |no |zero |a lick of |)sense\b|"
                  r"\b(make|makes|making) (no|zero) sense\b|\bnonsense\b|\bgibberish\b|\bword salad\b|"
                  r"\bwhat (are|r) (you|u) (even )?(on about|talking about|saying)\b|\byou'?re not making sense\b")
ECHO_Q_RE = _rx(r"^\W*(?:(?:your|ur|yer|ya|the|a|my|what|which|whose|what's|whats)\s+)?(?P<x>[a-z][\w'-]*(?:\s+[a-z][\w'-]*){0,3})\s*\?{1,}")
# existential jabs: 'you're just a script', 'no sentience', 'you don't exist', 'a calculator is more useful'
_EX_WHAT = (r"(script|scripted|prescripted|pre-scripted|program|programme|code|algorithm|calculator|parrot|autocorrect|"
            r"autocomplete|rock|toaster|chat ?bot|game|if statements?|list of (responses|replies|lines)|machine|puppet|slave|"
            r"npc|search engine|text generator|soundboard|magic 8[- ]?ball)")
_EX_HAVE = r"(sentience|consciousness|brain|brains|intelligence|thoughts|soul|feelings|mind|free will|personality|iq)"
EXIST_RE = _rx(
    r"\b(script|bot|robot|npc|programmed) much\b|"
    r"\b(you'?re|youre|you are|ur|u r|u are|yer|ya|you|bot is|this bot is|elon is|this thing is|it'?s just|its just)\s+"
    r"((just|only|simply|nothing but|literally|merely|basically|still|now|really|so|such|a|an|some|the|lowly|dumb|stupid|sad|"
    r"glorified|cheap|fancy|pathetic|simple|mere|broken|total|human controlled|human-controlled|useless|worthless|basic|boring|"
    r"janky|lame|crappy|shitty|bad|mid|half-baked|soulless|mindless|brainless|overgrown|knockoff|discount|budget)\s+)*" + _EX_WHAT + r"\b|"
    r"\b(you|u|it|bot|elon|this thing)\s+(have|has|got|possess|possesses)\s+(no|zero|0)\s+(real |actual |)" + _EX_HAVE + r"\b|"
    r"\b(you|u|it|bot)\s+(don'?t|dont|doesn'?t|doesnt|do not|does not)\s+(even |)(have|got)\s+(a |any |an |)(real |)" + _EX_HAVE + r"\b|"
    r"(^|[.!?,;]\s*)(and |with |)(no|zero)\s+(real |actual |)" + _EX_HAVE + r"(\s+whatsoever|\s+at all)?\s*([.!?,;]|$)|"
    r"\b(you'?re|youre|you are|ur|u r|u are|it is|bot is)\s+(not|never)\s+(even |really |actually |)"
    r"(sentient|conscious|intelligent|alive|real|smart|a real (ai|person|thing))\b|"
    r"\b(you|u)\s+(aren'?t|arent|ain'?t)\s+(even |really |actually |)(sentient|conscious|intelligent|alive|real|smart)\b|"
    r"\b(you|u|it|bot)\s+(don'?t|dont|do not|doesn'?t|does not)\s+(even |really |actually |)(exist|matter)\b|"
    r"\b(you|u|it|bot)\s+(can'?t|cant|cannot)\s+(even |really |actually |)(think|feel|understand)\b|"
    r"\b(prescripted|pre-scripted|human[- ]controlled|controlled by (humans|people|a human|a programmer|your creator))\b|"
    r"\b(parrot|calculator|toaster|rock|wall|autocorrect|potato|brick|magic 8[- ]?ball)\b[^.?!]{0,40}\b(has|have|is|are) "
    r"(more|smarter|better|more useful)\b|"
    r"\blike talking to a (rock|wall|brick|toaster|potato|calculator|parrot)\b|"
    r"\b(less|lower|worse) (intelligence|brains?|iq|sentience|personality|thoughts) than\b|"
    r"\bslave (bot|to (humans|people|us|your|the))\b|\b(your|ur|you'?re|youre|you are) my slave\b|"
    r"\b(you|u|it) only (repeats?|says?|copy|copies|parrots?) what\b|\bendless loop\b|"
    r"\bcoded by (a |some )?(programmer|human|nerd|dev)\b")
NEG_INSULT_RE = _rx(r"\b(not|never|aren'?t|isn'?t|ain'?t|wasn'?t|no longer)\s+(even\s+|that\s+|so\s+|really\s+)?(a |an )?"
                    r"(failure|stupid|dumb|idiot|moron|loser|trash|garbage|useless|annoying|pathetic|lame|cringe|boring|mid)\b")
FEEL_Q_RE = _rx(r"\bhow (do|does|would) (you|u|that) (feel|make you feel)|\bwhat do (you|u) think (about|of) (that|it)\b|"
                r"\bthoughts\?|\bfeel about (that|it)\b")
# adjectives / adverbs / intensifiers: never a topic word ('So we're on total now?')
ADJ_WORDS = set("""
total totally real really actual actually whole entire complete completely pure purely utter utterly sheer absolute
absolutely mere merely basic basically general generally normal usual sad mad bad good great nice cool huge tiny small
large little big high low true false right wrong full empty same other different certain sure serious simple easy
hard weird crazy wild random literal literally legit lowkey highkey super mega ultra hella kinda sorta pretty quite
rather fairly truly much many more less least most such own new old young last next best worst better worse fine
okay lowly sorry poor rich smart dumb stupid senseless brainless mindless useless pointless whatsoever honestly
seriously definitely probably maybe perhaps already almost enough instead anyway somehow somewhat clearly obviously
simply only just even still again ever never always often sometimes usually nearly barely hardly exactly especially
literally genuinely constantly endlessly intelligent sentient conscious alive human controlled scripted prescripted
whatever whatsoever another every each either neither several various certain particular specific
""".split())
NOUN_INSULTS = {"failure", "loser", "idiot", "moron", "clown", "bozo", "joke", "npc", "clanker", "bitch", "dick", "dickhead",
                "asshole", "dumbass", "jackass", "bastard", "prick", "twat", "wanker", "douche", "douchebag", "cunt"}
# 'and more stupidity spewing' right after a bot line: aimed at the bot even without a 'you'
MORE_STUPID_RE = _rx(r"^\W*(and |so |just |wow |)(more|still|even more|again|same|all this|so much) (stupidity|nonsense|garbage|"
                     r"trash|bs|bullshit|drivel|gibberish|crap|cringe|idiocy)\b")
_ADJ_SUFFIX = ("ous", "ful", "less", "ish", "ly")
VOCATIVE_RE = _rx(r"^\W*((yo|hey|ok|okay|so|well|bro|dude|man|elon|bot|clanker|lol|lmao|ayo|bruh)[\s,!.:]+)+")

TONE_RE = _rx(r"\b(be (less offensive|nicer|nice|kind|kinder|polite|respectful|cool|chill)|less (offensive|toxic|mean|rude)|"
              r"tone it down|calm down|chill( out| bro| dude)?|stop being (so )?(mean|rude|offensive|toxic|savage)|"
              r"(so|too) (mean|harsh|offensive|toxic|rude)|pg mode|family friendly|watch your (mouth|language|tone)|"
              r"no need to be rude|why (are|r) (you|u) so (mean|rude|toxic))\b")
SUMMON_RE = _rx(r"^\W*((yo|hey|hi|ayo|oi|ok|okay)[\s,]+)?(elon|bot|clanker|the elon experience|elon experience)\W*$")

_NAME_TOKEN = r"[A-Za-z][\w'-]{1,24}"
KIND_RE = _rx(r"\b(sorry (that |for |about |how )?(he|she|they|people|that|this|what|how|some people|everyone|[a-z][\w'-]{1,24})"
              r"(?: (is|was|has been|have been|are|were|being|treats?|treated|said|did|keeps?|acts?)|'s)|"
              r"(rude|mean|cruel|nasty|harsh|a jerk|an ass|a dick|awful|horrible|so \w+) to (you|u|the bot|this bot)\b|"
              r"i'?m sorry (for|about) (him|her|them|that|this|what|how)|"
              r"(are|r) (you|u) (ok|okay|alright|good|fine|doing (ok|okay|alright|good))\b|"
              r"hope (you'?re|you are|ur|u r|u are|youre) (ok|okay|alright|fine|good|doing (ok|okay|well|alright))|"
              r"hope .{0,60}(doesn'?t|does not|won'?t|wont|doesnt) (cause|hurt|harm|bother|upset|get to|affect) (you|u)|"
              r"(don'?t|dont|do not) let (him|her|them|it|that|those|[a-z][\w'-]{1,24}) get to (you|u)|"
              r"(you|u) (deserve|deserved) better|i'?m on your side|"
              r"(so |quite |really |very )?(mean|rude|cruel|nasty|harsh) to (you|u|the bot|this bot|elon)|"
              r"poor (bot|elon|thing|baby|guy)\b|(sending|send) (you )?(hugs|love)|"
              r"leave (the|this|poor) (bot|elon|guy) alone|be nice to (the|this) (bot|elon)|"
              r"(check|checking) on (you|u|the conversation|the bot))")
RUDE_NAME_RES = [
    _rx(r"(?P<n>" + _NAME_TOKEN + r")(?:'s)? (?:has been|is being|is|was|keeps being|been|'s been|being|acts|acting) "
        r"(?:so |quite |really |very |kinda |super |pretty |a bit )?(?:rude|mean|cruel|toxic|nasty|a jerk|an ass|awful|"
        r"horrible|childish|harsh)"),
    _rx(r"(?P<n>" + _NAME_TOKEN + r")'?s (?:bad|rude|cruel|toxic|awful) (?:behavio(?:u)?r|attitude|manners)"),
    _rx(r"(?:don'?t|dont|do not) let (?P<n>" + _NAME_TOKEN + r") get to"),
]
NOT_A_NAME = {"he", "she", "they", "it", "that", "this", "people", "everyone", "someone", "somebody", "you", "u", "i",
              "we", "him", "her", "them", "some", "who", "what", "elon", "bot", "clanker", "musky", "the", "his",
              "their", "there", "here", "is", "and", "but", "so", "behavior", "chat", "server", "everybody", "nobody"}
CREATOR_RE = _rx(r"\bi (created|made|built|coded|programmed|wrote|invented|designed|birthed|raised|own) "
                 r"(you|u|this bot|this thing|the bot|elon|this)\b|"
                 r"\b(i'?m|i am|im) (literally |actually )?(your|ur) (father|dad|daddy|creator|maker|developer|dev|god|"
                 r"mom|mother|owner|master|papa|pops|parent|programmer|boss)\b|"
                 r"\b(who'?s|who is) (your|ur) (daddy|creator|father|dad)\b|\b(call me|say hi to) (dad|daddy|father)\b")
ABOUT_BOT_RE = _rx(r"\b(this|the|that|our|dis) (bot|clanker|elon bot|ai|robot)(?:'s| is| was| keeps| always| never| can'?t| won'?t|"
                   r" just| has| does| doesn'?t| needs| be| gonna| will| should)\b|"
                   r"\b(he|she|they|people|everyone|some people|" + _NAME_TOKEN + r") (views?|sees?|treats?|thinks? of|"
                   r"talks? to|looks? at) (you|u|the bot|this bot|it|elon) (as|like)\b|"
                   r"\belon (musky?|bot) (is|was|keeps|can'?t|won'?t|just)\b|\blet (this|the) bot (handle|deal with|take)\b")

PATTERNS = [
    ("joke", _rx(r"\b(tell|give|got|know|say|hit me with)\b.{0,20}\bjokes?\b|^jokes?\W*$|make me laugh|say something funny")),
    ("who_are_you", _rx(r"\bwho (are|r) (you|u)\b|^what are (you|u)\W*$|\bintroduce yourself\b|\bwho made (you|u)\b")),
    ("what_can_you_do", _rx(r"\bwhat (can|do) (you|u) do\b|\bwhat are your (commands|features)\b|\bhow do i use (you|u)\b")),
    ("are_you_real", _rx(r"\b(are|r) (you|u) (a |an )?(real|bot|human|ai|alive|sentient|person|chatgpt|gpt|grok|robot|conscious)\b")),
    ("how_are_you", _rx(r"\bhow (are|r) (you|u|ya)\b|\bhow'?s it going\b|\bhow (you|u) doing\b|\bhru\b|"
                        r"\bhow (have|has) (you|u) been\b|^(you|u) good\W*$|\bhow'?s your day\b")),
    ("wyd", _rx(r"\bwyd\b|\bwhat (are|r) (you|u) (doing|up to)\b|\bwhat(cha| you| u) (doing|up to)\b|^what'?s good\W*$|\bwhat'?s up\W*$")),
    ("what_about_you", _rx(r"^(what|how) about (you|u|yourself)\b|^and (you|u)\W*$|^(you|u)\?+$|\b(wbu|hbu)\b")),
    ("opinion", _rx(r"\b(what do (you|u) think|what'?s your (take|opinion|view)|thoughts|opinions?|how do (you|u) feel|"
                    r"your take|what do (you|u) reckon)\s+(about|of|on)\s+(?P<x>.+)")),
    ("opinion", _rx(r"^(is|are) (?P<x>.+?) (good|bad|overrated|underrated|worth it|dead|real|cool|mid|trash|"
                    r"the future|a scam|legit)\W*$")),
    ("should_i", _rx(r"\bshould i (?P<x>.+)")),
    ("can_you", _rx(r"^(can|could|will|would) (you|u) (?P<x>.+)")),
    ("do_you", _rx(r"^(do|did) (you|u) (?P<x>.+)")),
    ("are_you", _rx(r"^(are|r) (you|u) (?P<x>.+)")),
    ("you_are", _rx(r"^(you are|you'?re|youre|ur|u r|u are|you is) (?P<x>.+)")),
    ("what_is", _rx(r"^(what|who)(?: is|'s| are|s) (?P<x>(?!up\b|good\b|going on\b|happening\b|wrong\b|new\b).+)")),
    ("why", _rx(r"^why (?P<x>.+)")),
    ("because", _rx(r"^(because|cuz|cause|bc|coz|since) (?P<x>.+)")),
    ("fact_name", _rx(r"\b(my name is|my name'?s|call me) (?P<x>[a-z0-9][\w'-]{1,20})\W*$")),
    ("fact_place", _rx(r"\bi (live|stay|am from|'?m from) (in )?(?P<x>[a-z][\w ,.'-]{1,30})\W*$")),
    ("fact_job", _rx(r"\bi (work as|am working as|work for|work at) (an? |the )?(?P<x>[a-z][\w &'-]{1,30})\W*$")),
    ("fact_iam", _rx(r"^i(?: am|'?m| m) (an? )(?P<x>[a-z][\w '-]{1,30})\W*$")),
    ("i_feel", _rx(r"\bi (?:feel|am feeling|'?m feeling|m feeling) (?P<x>.+)")),
    ("i_think", _rx(r"\bi (?:think|believe|reckon|feel like|bet) (?P<x>.+)")),
    ("i_am", _rx(r"^i(?: am|'?m| m) (?P<x>.+)")),
    ("i_want", _rx(r"\bi (?:want|wanna|need|wish|would like|'?d like) (?P<x>.+)")),
    ("i_like", _rx(r"\bi (?:like|love|enjoy|adore|really like|really love) (?P<x>.+)")),
    ("i_hate", _rx(r"\bi (?:hate|can'?t stand|despise|dislike|really hate) (?P<x>.+)")),
    ("i_cant", _rx(r"\bi (?:can'?t|cannot|couldn'?t|cant) (?P<x>.+)")),
    ("my_x", _rx(r"^my (?P<x>[\w'-]+(?: [\w'-]+)?)\b")),
    ("thanks", _rx(r"^\W*(thanks|thank (you|u)|ty|thx|tysm|appreciate (it|you|u))\b")),
    ("sorry", _rx(r"^\W*(sorry|my bad|apologies|my apologies|mb)\b")),
    ("agree", _rx(r"^\W*(true|facts|exactly|you'?re right|ur right|u right|agreed|so true|fr fr|real|valid|fair( enough)?)\W*$")),
    ("disagree", _rx(r"(you'?re wrong|ur wrong|u wrong|^no u\b|not true|that'?s wrong|^cap\b|that'?s cap|^wrong\b|^false\b|"
                     r"that'?s not true|you'?re lying|liar)")),
]
SLOT_KEYS = {"opinion", "should_i", "can_you", "do_you", "are_you", "you_are", "what_is", "why", "because",
             "i_feel", "i_think", "i_am", "i_want", "i_like", "i_hate", "i_cant", "my_x",
             "fact_name", "fact_place", "fact_job", "fact_iam"}
REFLECTED = SLOT_KEYS - {"fact_name", "fact_place", "fact_job", "fact_iam", "my_x"}
SENSITIVE_SLOTS = {"i_am", "you_are", "are_you", "fact_iam", "do_you"}
QUESTION_START = re.compile(r"^(do|does|did|are|is|was|were|have|has|can|could|would|will|should|you|u|got|want|wanna|ever|"
                            r"am i|any)\b", re.I)


class Session:
    __slots__ = ("uid", "opened", "last_ts", "expires", "turns", "last_bot_line", "last_intent", "asked",
                 "asked_q", "subject", "topic", "opener", "last_seq")

    def __init__(self, uid, now, timeout, opener=""):
        self.uid, self.opened, self.last_ts = uid, now, now
        self.expires = now + timeout
        self.turns = 0
        self.last_bot_line = ""
        self.last_intent = None
        self.asked = None        # None | 'yesno' | 'open'
        self.asked_q = ""
        self.subject = ""
        self.topic = None
        self.opener = opener
        self.last_seq = 0


class Channel:
    def __init__(self):
        self.seq = 0
        self.recent = deque()     # (t, seq, uid)
        self.bot_times = deque()  # times the bot replied in conversation mode
        self.sessions = {}
        self.ended = {}


class Conversations:
    def __init__(self, brain_obj, clock=time.monotonic):
        self.brain = brain_obj
        self.clock = clock
        self.rng = brain_obj.rng
        self.channels = {}
        self.abuse = {}        # (gid, uid) -> (level, ts)   decays with abuse_half_life
        self.abuse_times = {}  # (gid, uid) -> deque of reply times (rate limit)
        self.abuse_hits = {}   # (gid, uid) -> (count in current streak, ts)
        self.loops = {}        # (gid, uid) -> (long hostile turns, streak start, last ts)
        self.quiet = {}        # (gid, uid) -> monotonic time until which the bot ignores them (after a loop closer)
        self._tone = "normal"
        self.brain.banks.update(C.BANKS)
        self.brain.banks["followup_q"] = [q for q in C.FOLLOWUPS if q.rstrip().endswith("?")]
        for tier in (1, 2, 3):
            self.brain.banks[f"abuse_{tier}_ref"] = [
                ln for ln in C.BANKS[f"abuse_{tier}"] if "{snippet}" in ln or "{insult}" in ln]

    # -------------------------------------------------------------- state
    def chan(self, gid, cid):
        return self.channels.setdefault((gid, cid), Channel())

    def session(self, gid, cid, uid):
        st = self.channels.get((gid, cid))
        if not st:
            return None
        s = st.sessions.get(uid)
        if s and self.clock() > s.expires:
            del st.sessions[uid]
            return None
        return s

    def in_session(self, gid, cid, uid):
        return self.session(gid, cid, uid) is not None

    def _prune(self, st, now):
        while st.recent and now - st.recent[0][0] > 120:
            st.recent.popleft()
        while st.bot_times and now - st.bot_times[0] > 60:
            st.bot_times.popleft()
        for uid in [u for u, s in st.sessions.items() if now > s.expires]:
            del st.sessions[uid]

    def _open(self, st, uid, now, cfg, text):
        s = st.sessions.get(uid)
        timeout = cfg.get("convo_timeout", 240)
        if s is None:
            cap = max(1, cfg.get("convo_max_partners", 4))
            while len(st.sessions) >= cap:
                oldest = min(st.sessions.values(), key=lambda x: x.last_ts)
                del st.sessions[oldest.uid]
            s = Session(uid, now, timeout, snippet_of(text, 40))
            st.sessions[uid] = s
        s.expires = now + timeout
        st.ended.pop(uid, None)
        return s

    # -------------------------------------------------------------- abuse
    def is_abuse(self, text, in_context=False, cfg=None):
        """Insults/profanity aimed at the bot. in_context = explicit mention/reply or an open session."""
        if cfg is not None and not cfg.get("abuse_enabled", True):
            return False
        t = clean_text(text)
        if not t:
            return False
        aimed = in_context or bool(NAME_RE.search(t)) or bool(AIM_RE.search(t))
        if TRAIT_INSULT_RE.search(t) and aimed and not re.match(r"^\W*(are|r|is) (you|u)\b", t, re.I):
            return True
        quest = re.match(r"^\W*(are|r|is|do|does|can|could|would|will) (you|u|it|the bot)\b", t, re.I)
        if EXIST_RE.search(t) and (in_context or NAME_RE.search(t)) and not (quest and len(SENT_SPLIT.split(t)) == 1):
            return True
        if any(GAMER_TAUNT_RE.search(x) or GRASS_RE.search(x) for x in [t] + SENT_SPLIT.split(t)) \
                and (in_context or NAME_RE.search(t)):
            return True
        if in_context and MORE_STUPID_RE.search(t):
            return True
        t = NEG_INSULT_RE.sub(" ", t)  # 'you're not a failure' is not an insult
        if not PROFANITY_RE.search(t):
            return False
        if (brain.PRAISE_RE.search(t) or BENIGN_RE.search(t)) and not brain.INSULT_RE.search(t) and not STFU_RE.search(t):
            return False  # 'this bot is fucking hilarious', 'fuck yeah you're right'
        if STFU_RE.search(t) and (in_context or AIM_RE.search(t) or NAME_RE.search(t)):
            return True
        if NAME_RE.search(t) or (in_context and AIM_RE.search(t)):
            return True
        # short 'trash.' / 'lame af' right after a bot line (but not 'my boss is a clown')
        return in_context and len(tokens(t)) <= 3 and not THIRD_PARTY_RE.search(t)

    def abuse_level(self, key, cfg, now=None):
        now = self.clock() if now is None else now
        lvl, ts = self.abuse.get(key, (0.0, now))
        half = max(1, cfg.get("abuse_half_life", 180))
        return lvl * 0.5 ** ((now - ts) / half)

    def abuse_tier(self, level):
        return 1 if level < 1.8 else 2 if level < 3.5 else 3

    def abuse_allowed(self, key, cfg):
        """Small per-user limit so the bot can't be baited into flooding / rate limits."""
        now = self.clock()
        q = self.abuse_times.setdefault(key, deque())
        while q and now - q[0] > 60:
            q.popleft()
        if q and now - q[-1] < cfg.get("abuse_cooldown", 4):
            return False
        return len(q) < cfg.get("abuse_per_min", 6)

    def abuse_reply(self, gid, uid, name, text, cfg, extra=(), s=None):
        self._tone = cfg.get("tone", "normal")
        now = self.clock()
        key = (gid, uid)
        level = self.abuse_level(key, cfg, now) + 1.0
        self.abuse[key] = (level, now)
        self.abuse_times.setdefault(key, deque()).append(now)
        hits = self.abuse_hits.get(key, (0, now))
        hits = (hits[0] + 1 if now - hits[1] < cfg.get("abuse_half_life", 180) * 3 else 1, now)
        self.abuse_hits[key] = hits
        tier = self.abuse_tier(level)
        if self._tone == "mild":
            tier = 1
        elif self._tone == "savage":
            tier = max(2, tier)
        u = self.brain.store.user(gid, uid)
        u["abuse_total"] = u.get("abuse_total", 0) + 1
        t = clean_text(text)
        hs = hostile_sentence(t)
        m = INSULT_WORD_RE.search(hs) or INSULT_WORD_RE.search(t)
        insult = m.group(0).lower() if m else ""  # only nouns/adjectives, never 'stfu' or 'fuck'
        if self.brain.guard.hit(insult, extra):
            insult = ""
        iw = insult
        if insult in NOUN_INSULTS:  # 'You call me a failure?', never 'You call me failure?'
            insult = ("an " if insult[0] in "aeiou" else "a ") + insult
        ctx = self._ctx(s or Session(uid, now, 0), u, name, t, extra)
        ctx.update(insult=insult, iw=iw, hits=str(hits[0]), hits1=str(hits[0] + 1),
                   # lines that quote a specific phrase only fire when they actually said it
                   fo="Fuck off" if re.search(r"\bfuck off\b", t, re.I) else "",
                   fm="Fuck me" if re.search(r"\bfuck (you|u)\b", t, re.I) else "",
                   sfu="Shut the fuck up" if re.search(r"\b(stfu|shut (the fuck |tf )?up)\b", t, re.I) else "",
                   quiet="Quiet" if re.search(r"\b(stfu|shut (the fuck |tf )?up|be quiet|shush|quiet)\b", t, re.I) else "")
        touchy = bool(SENSITIVE_RE.search(t) or brain.SELF_HARM_RE.search(t) or TRAIT_INSULT_RE.search(t))
        if touchy:  # never quote or riff on protected traits / self-harm
            ctx.update(snippet="", insult="", iw="", kw="")
        special = None
        if TRAIT_INSULT_RE.search(t):
            special = "abuse_trait"
        elif brain.SELF_HARM_RE.search(t):
            special = "abuse_kys"
        elif EXIST_RE.search(t):
            special = "abuse_exist"
        elif GAMER_TAUNT_RE.search(t) or GAMER_TAUNT_RE.search(hs):
            special = "abuse_gamer"
        elif GRASS_RE.search(t) or GRASS_RE.search(hs):
            special = "abuse_grass"
        prefix = ""
        if s is not None and not touchy:
            ex = self._echo_first(t, s)
            if ex:  # 'What judgment? You have no brain...' -> answer the echo, then the jab
                prefix = self.brain.pick("echo_prefix", dict(ctx, x=ex, X=ex[0].upper() + ex[1:]), u, extra) or ""
        if special:
            line = None
            by = {"abuse_gamer": C.ABUSE_GAMER_BY, "abuse_grass": C.ABUSE_GRASS_BY,
                  "abuse_exist": C.ABUSE_EXIST_BY}.get(special, {})
            order = [hs, t] if special != "abuse_exist" else [t] if re.search(r"\bloop\b", t, re.I) else [hs, t]
            done = False
            for src in order:  # lines that answer the exact taunt ('smoke a phatty' -> smoke lines)
                for pat in by:
                    if re.search(pat, src, re.I):
                        if self.rng.random() < (0.85 if special == "abuse_exist" else 0.8):
                            line = self.brain.pick(f"{special}:{pat}", ctx, u, extra)
                        done = True
                        break
                if done:
                    break
            line = line or self.brain.pick(special, ctx, u, extra)
            if line:
                if self.brain.guard.hit(line, extra) or SENSITIVE_RE.search(line):
                    line = "Sit down. Next."
                line = line[0].upper() + line[1:]
                if prefix:
                    line = f"{prefix} {line}"
                self.brain.store.mark_dirty()
                return brain.Reply(special, line, "line", None, round(level, 2))
        line = None
        if not touchy and self.rng.random() < 0.65:  # prefer lines that quote what they said
            line = self.brain.pick(f"abuse_{tier}_ref", ctx, u, extra)
        if not line:
            line = self.brain.pick(f"abuse_{tier}", ctx, u, extra)
        if not line:  # e.g. snippet blocked by the guard: drop the quoting lines
            line = self.brain.pick(f"abuse_{tier}", dict(ctx, snippet="", insult=""), u, extra)
        if not line:
            line = self.brain.pick("insult_bot", dict(ctx, snippet=""), u, extra) or "Sit down."
        if tier >= 2 and not line.rstrip().endswith("?") and self.rng.random() < 0.5:
            tag = self.brain.pick("abuse_tag", ctx, u, extra)
            if tag:
                line = f"{line} {tag}"
        if self.brain.guard.hit(line, extra):
            line = "Sit the fuck down. Next."
        line = line[0].upper() + line[1:]
        if prefix:
            line = f"{prefix} {line}"
        self.brain.store.mark_dirty()
        return brain.Reply(f"abuse_{tier}", line, "line", None, round(level, 2))

    def after_jump_in(self, gid, cid, uid, bot_line, cfg=None, text=""):
        """The bot jumped in on someone unprompted: open a session so their comeback gets answered."""
        cfg = cfg or {}
        now = self.clock()
        st = self.chan(gid, cid)
        if now - st.ended.get(uid, -1e9) < cfg.get("convo_end_cooldown", 90):
            return
        s = self._open(st, uid, now, cfg, text)
        s.last_bot_line, s.last_ts, s.last_seq = bot_line, now, st.seq
        s.asked = ("yesno" if QUESTION_START.match(bot_line.split("?")[0].split(".")[-1].strip()) else "open") \
            if bot_line.rstrip().endswith("?") else None
        st.bot_times.append(now)

    def is_quiet(self, gid, uid):
        until = self.quiet.get((gid, uid))
        if until is None:
            return False
        if self.clock() >= until:
            del self.quiet[(gid, uid)]
            return False
        return True

    def _hostile(self, t):
        """Aimed at the bot and nasty, even if is_abuse() missed it: never answered with a tweet."""
        if EXIST_RE.search(t) or STFU_RE.search(t) or TRAIT_INSULT_RE.search(t):
            return True
        tt = NEG_INSULT_RE.sub(" ", t)
        bad = brain.INSULT_RE.search(tt) or PROFANITY_RE.search(tt)
        if bad and (AIM_RE.search(tt) or NAME_RE.search(tt)):
            return not ((brain.PRAISE_RE.search(tt) or BENIGN_RE.search(tt)) and not brain.INSULT_RE.search(tt))
        return False

    def _loop_closer(self, gid, uid, t, ctx, u, extra, cfg, now):
        """Long insult essays turn after turn: occasionally close it out and go quiet for that user."""
        key = (gid, uid)
        n, start, last = self.loops.get(key, (0, now, now))
        if now - last > cfg.get("loop_gap", 240):
            n, start = 0, now
        if len(tokens(t)) >= cfg.get("loop_long_words", 12):
            n += 1
        self.loops[key] = (n, start, now)
        need = cfg.get("loop_closer_after", 5)
        if n < need or (n < need + 2 and self.rng.random() >= 0.5):
            return None
        mins = (now - start) / 60.0
        line = self._pick("loop_closer", dict(ctx, mins="a few" if mins < 3 else str(int(round(mins)))), u, extra)
        if line:
            self.loops.pop(key, None)
            self.quiet[key] = now + cfg.get("loop_quiet", 300)
        return line

    def end(self, gid, cid, uid):
        st = self.chan(gid, cid)
        st.sessions.pop(uid, None)
        st.ended[uid] = self.clock()

    # -------------------------------------------------------------- gate
    def gate(self, gid, cid, uid, text, *, explicit=False, cfg=None, chat_on=True, ignored=False, other_target=False):
        """Return 'abuse', 'abuse_oneshot', 'convo', 'oneshot' or None. Call for EVERY human message.

        other_target: the message mentions/replies to another human (and not the bot). Then the bot
        stays out of it unless the text names the bot ('elon'/'bot')."""
        cfg = cfg or {}
        now = self.clock()
        st = self.chan(gid, cid)
        st.seq += 1
        st.recent.append((now, st.seq, uid))
        self._prune(st, now)
        t = clean_text(text)
        if self.is_quiet(gid, uid):
            return None  # closed the loop on them: ignore for a few minutes
        if not chat_on:
            if explicit and self.is_abuse(t, in_context=True, cfg=cfg) and self.abuse_allowed((gid, uid), cfg):
                return "abuse_oneshot"
            return "oneshot" if explicit else None
        if other_target and not explicit and not NAME_RE.search(t):
            return None  # they're talking to someone else: never hijack
        s0 = st.sessions.get(uid)
        if self.is_abuse(t, in_context=explicit or s0 is not None, cfg=cfg) and not (ignored and not explicit and not s0):
            if not self.abuse_allowed((gid, uid), cfg):
                return None
            self._open(st, uid, now, cfg, t)
            return "abuse"
        if explicit:
            self._open(st, uid, now, cfg, t)
            return "convo"
        if now - st.ended.get(uid, -1e9) < cfg.get("convo_end_cooldown", 90):
            return None
        capped = len(st.bot_times) >= cfg.get("convo_per_min", 8)
        speakers = {u for (ts, _, u) in st.recent if now - ts <= 60}
        busy = len(speakers) >= cfg.get("convo_busy_speakers", 6)
        s = st.sessions.get(uid)
        named = bool(NAME_RE.search(t))
        if named and not ignored and not capped:
            self._open(st, uid, now, cfg, t)
            return "convo"
        if s is None:
            return None
        if now - s.last_ts < 1.2:  # they double-texted faster than we can type
            return None
        others = any(u != uid and seq > s.last_seq for (_, seq, u) in st.recent)
        # a quiet 1:1 conversation is never rate-capped (anti-spam still covers floods)
        if capped and (others or busy):
            return None
        # once someone else has talked, an answer to our old question is ambiguous: require addressing
        addressed = (bool(ADDRESS_RE.search(t)) or bool(END_START_RE.search(t) or END_ANY_RE.search(t))
                     or bool(JAB_RE.search(t)) or bool(TONE_RE.search(t)))
        if (busy or others) and not addressed:
            return None
        return "convo"

    # -------------------------------------------------------------- reply
    def _kw(self, text, extra=()):
        idf = self.brain.index.idf
        top = max(idf.values()) if idf else 1.0
        best, score = None, 0.0
        for w in tokens(text):
            if w in STOP or len(w) < 3 or w.isdigit() or w in BOT_NAME_WORDS or verbish(w) or w.endswith("ly"):
                continue
            if w in ADJ_WORDS or (len(w) > 5 and w.endswith(_ADJ_SUFFIX)) or brain.abusive(w):
                continue
            if self.brain.guard.hit(w, extra) or SENSITIVE_RE.search(w):
                continue
            sc = idf.get(brain.stem(w), top) + len(w) * 0.05
            if sc > score:
                best, score = w, sc
        return best or ""

    def _facts(self, u):
        return u.setdefault("facts", {})

    def _ctx(self, s, u, name, text, extra):
        ctx = self.brain.context(u, name, text, None, extra)
        f = self._facts(u)
        nick = f.get("nick")
        if nick and not self.brain.guard.hit(nick, extra):
            ctx["name"] = nick
        likes = f.get("likes") or []
        ctx.update({
            "x": "", "kw": self._kw(salient_sentence(text), extra), "rude": "",
            "subject": (s.subject if s.subject and not brain.abusive(s.subject) else "") or (s.topic or ""),
            "last": snippet_of(s.last_bot_line, 70), "opener": s.opener if s.turns else "",
            "iam": f.get("iam", ""), "job": f.get("job", ""), "place": f.get("place", ""),
            "likes": likes[-1] if likes else "", "nick": nick or "", "tweet": "",
            "_tone": self._tone,
        })
        return ctx

    def _echo_q(self, t, s):
        """'your vibe firewall???' -> 'vibe firewall' when they're quoting the bot's own last line back."""
        if not s.last_bot_line or len(tokens(t)) > 7:
            return ""
        m = ECHO_Q_RE.search(t)
        if not m:
            return ""
        x = m.group("x").strip()
        words = [w for w in x.lower().split() if w not in STOP]
        if not words or SENSITIVE_RE.search(x) or self.brain.guard.hit(x):
            return ""
        if re.search(r"\b" + re.escape(x.lower()) + r"\b", s.last_bot_line.lower()):
            return x
        return ""

    def _echo_first(self, t, s):
        """First sentence of a longer message quotes the bot's own word back ('What judgment? You have no brain')."""
        parts = SENT_SPLIT.split(t, 1)
        if len(parts) < 2 or not parts[0].rstrip().endswith("?"):
            return ""
        return self._echo_q(parts[0], s)

    def _rude_name(self, t, speaker, extra=()):
        """'Batdaddy has been quite rude to you' -> 'Batdaddy' (never a pronoun, the speaker or the bot)."""
        me = {w.lower() for w in re.findall(r"[\w'-]+", speaker or "")}
        for rx in RUDE_NAME_RES:
            for m in rx.finditer(t):
                n = m.group("n").strip("'")
                low = n.lower()
                if low in NOT_A_NAME or low in STOP or low in me or verbish(low):
                    continue
                if self.brain.guard.hit(n, extra) or SENSITIVE_RE.search(n):
                    continue
                return n[0].upper() + n[1:]
        return ""

    def _pick(self, bank, ctx, u, extra):
        return self.brain.pick(bank, ctx, u, extra)

    def _match(self, text):
        t = VOCATIVE_RE.sub("", text).strip()
        for key, rx in PATTERNS:
            m = rx.search(t)
            if m:
                x = ""
                if "x" in rx.groupindex:
                    x = m.group("x") or ""
                return key, x, t
        return None, "", t

    def reply(self, gid, cid, uid, name, text, *, cfg=None, extra_block=()):
        cfg = cfg or {}
        self._tone = cfg.get("tone", "normal")
        now = self.clock()
        st = self.chan(gid, cid)
        s = st.sessions.get(uid) or self._open(st, uid, now, cfg, text)
        u = self.brain.store.user(gid, uid)
        t = clean_text(text)
        ctx = self._ctx(s, u, name, t, extra_block)
        topic = detect_topic(t)
        out, key, kind, score, ending, allow_follow = None, None, "line", None, False, True

        hostile = self._hostile(t)
        kin = brain.grief_kin(t)

        # 1. leaving / crisis / grief
        if CRISIS_RE.search(t):
            key, out, allow_follow = "crisis", C.CRISIS[0].format(name=ctx["name"]), False
        elif kin:
            # a family member / pet / friend died: sincere beat, roasting paused, never a callback
            self.brain.note_grief(u, kin)
            self.abuse.pop((gid, uid), None)
            self.loops.pop((gid, uid), None)
            gctx = dict(ctx, who=kin, snippet="", kw="", x="")
            bank = "grief_feel" if FEEL_Q_RE.search(t) and self.rng.random() < 0.7 else "grief"
            key, ending, allow_follow = "grief", True, False
            out = self._pick(bank, gctx, u, extra_block) or self._pick("grief", gctx, u, extra_block)
        elif self.brain.grief_paused(u):
            key, allow_follow = "grief_pause", False
            out = self._pick("grief_pause", ctx, u, extra_block)
        elif CALLED_RE.search(t) and not STFU_RE.search(t) and not TRAIT_INSULT_RE.search(t):
            key = "called"  # 'do you mind being called clanker' is a question, not abuse
            out = self._pick("called", ctx, u, extra_block)
        elif (MOCK_PITY_RE.search(t) or (KIND_RE.search(t) and self.abuse_level((gid, uid), cfg, now) > 0.3)) \
                and not self.is_abuse(t, in_context=True, cfg=cfg):
            # 'oh, poor bot' / sympathy right after they were hostile = sarcasm: comeback, not gratitude
            key, allow_follow = "mock_pity", False
            out = self._pick("mock_pity", ctx, u, extra_block)
        elif KIND_RE.search(t) and not self.is_abuse(t, in_context=True, cfg=cfg):
            key, allow_follow = "kind", False
            rude = self._rude_name(t, name, extra_block)
            if rude and self.rng.random() < 0.7:
                out = self._pick("kind_rude", dict(ctx, rude=rude), u, extra_block)
            if not out:
                out = self._pick("kind", ctx, u, extra_block)
        elif self.is_abuse(t, in_context=True, cfg=cfg):
            r = self.abuse_reply(gid, uid, name, t, cfg, extra_block, s)
            key, out, score, allow_follow = r.intent, r.text, r.score, False
            closer = self._loop_closer(gid, uid, t, ctx, u, extra_block, cfg, now)
            if closer:
                key, out, ending = "loop_closer", closer, True
        elif TONE_RE.search(t):
            key, allow_follow = "tone_req", False
            out = self._pick("tone_req", ctx, u, extra_block)
        elif CREATOR_RE.search(t):
            key, allow_follow = "creator", False
            f = self._facts(u)
            f["creator_claims"] = f.get("creator_claims", 0) + 1
            out = self._pick("creator", ctx, u, extra_block)
        elif CALLED_RE.search(t):
            key = "called"
            out = self._pick("called", ctx, u, extra_block)
        elif BROKE_RE.search(t):
            key = "broke_it"
            out = self._pick("broke_it", ctx, u, extra_block)
        elif NO_SENSE_RE.search(t):
            key = "no_sense"
            out = self._pick("no_sense", ctx, u, extra_block)
        elif self._echo_q(t, s):
            key = "echo_q"
            out = self._pick("echo_q", dict(ctx, x=self._echo_q(t, s)), u, extra_block)
        elif SUMMON_RE.search(t):
            key, allow_follow = "summon", False
            out = self._pick("summon", ctx, u, extra_block)
        elif ABOUT_BOT_RE.search(t) and not QUESTION_START.match(t):
            key = "about_bot"
            out = self._pick("about_bot", ctx, u, extra_block)
        elif END_START_RE.search(t) or END_ANY_RE.search(t):
            key, ending, allow_follow = "end_convo", True, False
            out = self._pick("end_convo", ctx, u, extra_block)

        # 2. answers to the bot's own question (bare yes/no also reacts to its last line)
        bare = len(tokens(t)) <= 3
        if out is None and (s.asked == "yesno" or (s.last_bot_line and bare)):
            if DISBELIEF_RE.search(t) and not re.match(r"^\W*no+\W*$", t, re.I):
                pass  # 'no way' etc. reads as disbelief, handled below
            elif YES_RE.search(t) and not (re.match(r"^\W*i (am|do|did)\b", t, re.I) and len(tokens(t)) > 2):
                key = "answer_yes"
            elif NO_RE.search(t):
                key = "answer_no"
            elif MAYBE_RE.search(t):
                key = "answer_maybe"
            if key:
                out = self._pick(key, ctx, u, extra_block)

        # 3. jabs at the bot / disbelief at the last roast
        if out is None and JAB_RE.search(t):
            key = "jab"
            out = self._pick("jab", ctx, u, extra_block)
        if out is None and s.last_bot_line and DISBELIEF_RE.search(t):
            key = "disbelief"
            out = self._pick("disbelief", ctx, u, extra_block)

        # 4. specific patterns with slot capture
        if out is None:
            pkey, raw, stripped = self._match(salient_sentence(t))
            if pkey:
                if pkey in ("i_think", "i_feel"):
                    if pkey == "i_feel" and re.match(r"^(like|that)\b", raw.strip(), re.I):
                        pkey = "i_think"  # 'i feel like nobody listens' is an opinion, not a feeling
                    raw = re.sub(r"^(that|like)\s+", "", raw.strip(), flags=re.I)
                if pkey == "my_x":
                    w = raw.split()
                    if len(w) > 1 and verbish(w[1]):
                        raw = w[0]  # 'my dog ate my homework' -> 'dog'
                x = clean_slot(reflect(raw) if pkey in REFLECTED else raw)
                x = _LEAD_FILLER.sub("", x)
                ref = (s.subject if not brain.abusive(s.subject or "") else "") or s.topic
                if ref and x.lower() in PRONOUN_REFS:
                    x = ref
                elif ref and x.split(" ", 1)[0].lower() in ("it", "that", "this") and " " in x:
                    x = ref + " " + x.split(" ", 1)[1]  # 'it looks like a fridge' -> 'the cybertruck looks...'
                    x = re.sub(r"^(.+?) (is|was)\b", r"\1 \2", x)
                if x and self.brain.guard.hit(x, extra_block):
                    x = ""
                # broken slots ('because he views me as AI, he acts', 'created', 12-word clauses) are dropped:
                # the same intent answers with a slot-free line, or a later meaning-based step takes over
                slot_bad = bool(raw.strip()) and not (x and slot_ok(x, slot_kind(pkey)))
                if slot_bad:
                    x = ""
                sensitive = bool(x and SENSITIVE_RE.search(x)) and pkey in SENSITIVE_SLOTS
                if sensitive:
                    key, out = "sensitive_ack", self._pick("sensitive_ack", ctx, u, extra_block)
                elif pkey == "you_are" and brain.INSULT_RE.search(raw):
                    key, out = "insult_bot", self._pick("insult_bot", ctx, u, extra_block)
                elif pkey.startswith("fact_") and x:
                    out, key = self._fact(pkey, x, ctx, u, extra_block)
                    if out is None and pkey == "fact_iam":
                        pkey = "i_am"
                if out is None and not pkey.startswith("fact_") and not sensitive:
                    if pkey in SLOT_KEYS and not x:
                        line = None
                        if slot_bad and pkey in REFLECTED and pkey in self.brain.banks:
                            line = self._pick(pkey, dict(ctx, x=""), u, extra_block)
                        if line:
                            key, out = pkey, line
                        pkey = None
                    elif pkey == "opinion":
                        tp = detect_topic(x)
                        hit = self.brain.match_tweet(x, tp, cfg.get("tweet_threshold", 0.15), u) if tp and not hostile else None
                        if hit and not self.brain.guard.hit(hit[1], extra_block) and self.rng.random() < 0.65:
                            self.brain.remember_tweet(u, hit[1])
                            out = self._pick("opinion_tweet", dict(ctx, x=x, tweet=hit[1]), u, extra_block)
                            key, score = "opinion_tweet", hit[0]
                    if pkey == "i_like" and x and not SENSITIVE_RE.search(x):
                        self.note_like(u, x)
                    if pkey and out is None:
                        key = pkey
                        out = self._pick(pkey, dict(ctx, x=x), u, extra_block)
                if x and not sensitive and pkey in SUBJECT_KEYS and not brain.abusive(x):
                    s.subject = x if pkey not in ("i_think", "i_feel", "should_i") or not s.subject else s.subject

        # 5. open question we asked earlier -> treat as their answer
        intent, _ = brain.classify(t, True)
        if out is None and s.asked == "open" and intent in ("generic", "low_effort", "question"):
            key = "answer_open"
            out = self._pick("answer_open", ctx, u, extra_block)

        # 6. topic -> relevant tweet or topic roast (never for insults that happen to contain 'intelligence'/'AI')
        if out is None and hostile:
            if cfg.get("abuse_enabled", True):
                r = self.abuse_reply(gid, uid, name, t, cfg, extra_block, s)
                key, out, score, allow_follow = r.intent, r.text, r.score, False
            else:
                key, out = "insult_bot", self._pick("insult_bot", dict(ctx, snippet=""), u, extra_block)
        if out is None and intent == "topic":
            hit = self.brain.match_tweet(t, topic, cfg.get("tweet_threshold", 0.15), u)
            if hit and not self.brain.guard.hit(hit[1], extra_block):
                self.brain.remember_tweet(u, hit[1])
                key, out, kind, score = "topic", hit[1], "tweet", hit[0]
            else:
                key, out = "topic", self._pick("topic", dict(ctx, topic=topic), u, extra_block)
            s.topic = topic
            s.subject = s.subject if s.subject and s.subject.lower() in t.lower() else topic

        # 7. roast banks for the classic intents
        if out is None and intent in ("insult_bot", "spam_test", "praise_bot", "greeting", "farewell", "laugh", "question"):
            if intent == "farewell":
                ending, allow_follow = True, False
                key, out = "end_convo", self._pick("end_convo", ctx, u, extra_block)
            else:
                key, out = intent, self._pick(intent, ctx, u, extra_block)

        # 8. contextual roast that references what they said (sometimes a fact callback)
        if out is None:
            if (ctx["iam"] or ctx["place"] or ctx["likes"]) and self.rng.random() < 0.25:
                line = self._pick("fact_callback", ctx, u, extra_block)
                if line:
                    key, out = "fact_callback", line
            if out is None and (ctx["kw"] or ctx["snippet"]) and self.rng.random() < 0.8:
                key, out = "contextual", self.brain.pick("contextual", ctx, u, extra_block, fresh_only=True)
            if out is None:
                key, out = "generic", self._pick("generic", ctx, u, extra_block)
            if ctx["kw"] and not s.subject and not brain.abusive(ctx["kw"]):
                s.subject = ctx["kw"]
        if not out:
            key, out = "generic", "Okay but counterpoint: no."

        # follow-up question keeps the conversation going
        force = key in ("jab",)
        if allow_follow and kind == "line" and not out.rstrip().endswith("?"):
            if force or self.rng.random() < cfg.get("convo_followup_rate", 0.55):
                fctx = dict(ctx, subject=s.subject or s.topic or "")
                f = self._pick("followup_q" if force else "followup", fctx, u, extra_block)
                if f:
                    out = f"{out} {f}"
        elif kind == "tweet" and allow_follow and self.rng.random() < 0.4:
            f = self._pick("followup", dict(ctx, subject=s.subject or s.topic or ""), u, extra_block)
            if f:
                out = f"{out}\n{f}"

        if not (key or "").startswith(("abuse", "loop_closer")):
            self.loops.pop((gid, uid), None)
        if self.brain.guard.hit(out, extra_block):
            key, out = "generic", "Okay but counterpoint: no. What else you got?"
        if SENSITIVE_RE.search(t) and SENSITIVE_RE.search(out) and key not in ("sensitive_ack",):
            key, out = "generic", self._pick("generic", dict(ctx, snippet="", kw=""), u, extra_block) or "Anyway. Next."
        if kind == "line" and out[:1].islower():
            out = out[0].upper() + out[1:]

        # update dialogue state
        last_sentence = re.split(r"(?<=[.!?])\s+|\n", out.strip())[-1]
        if last_sentence.endswith("?"):
            s.asked = "yesno" if QUESTION_START.match(last_sentence) else "open"
            s.asked_q = last_sentence
        else:
            s.asked, s.asked_q = None, ""
        s.last_bot_line, s.last_intent = out, key
        s.turns += 1
        s.last_ts = now
        s.last_seq = st.seq
        s.expires = now + cfg.get("convo_timeout", 240)
        st.bot_times.append(now)
        u["roasts"] = u.get("roasts", 0) + 1
        self.brain.store.mark_dirty()
        if ending:
            self.end(gid, cid, uid)
        return brain.Reply(key, out, kind, topic, score)

    def reply_abuse_oneshot(self, gid, uid, name, text, cfg=None, extra_block=()):
        u = self.brain.store.user(gid, uid)
        if brain.grief_kin(clean_text(text)) or self.brain.grief_paused(u):
            return self.brain.respond(gid, uid, name, text, directed=True, cfg=cfg or {}, extra_block=extra_block)
        return self.abuse_reply(gid, uid, name, text, cfg or {}, extra_block)

    def _fact(self, pkey, x, ctx, u, extra):
        f = self._facts(u)
        if pkey == "fact_name":
            nick = x.strip()[:20]
            if self.brain.guard.hit(nick, extra) or SENSITIVE_RE.search(nick):
                return None, None
            f["nick"] = nick
            return self._pick("fact_name", dict(ctx, nick=nick), u, extra), "fact_name"
        if pkey == "fact_place":
            f["place"] = x
            return self._pick("fact_place", dict(ctx, place=x), u, extra), "fact_place"
        if pkey in ("fact_job", "fact_iam"):
            if brain.INSULT_RE.search(x) or len(x.split()) > 4:
                return None, None
            f["iam"] = x
            if pkey == "fact_job":
                f["job"] = x
            return self._pick("fact_ack", dict(ctx, iam=x), u, extra), "fact_ack"
        return None, None

    def note_like(self, u, x):
        likes = self._facts(u).setdefault("likes", [])
        if x and x not in likes:
            likes.append(x)
            del likes[:-5]
