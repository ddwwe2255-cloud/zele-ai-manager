import os
import re
import asyncio
import json
import time
import random
import html
import urllib.parse
import requests
from threading import Thread
from flask import Flask
from telethon import TelegramClient, events
from telethon.tl.types import UserStatusOnline, UpdateMessageReactions
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError
from datetime import datetime, timezone
import pytz
from dotenv import load_dotenv

# ==========================================
# 1. Environment Variables
# ==========================================
load_dotenv()

API_ID = int(os.getenv('API_ID', 0))
API_HASH = os.getenv('API_HASH', '')
SESSION_STRING = os.getenv('SESSION_STRING', '')
BOT_TOKEN = os.getenv('BOT_TOKEN', '')
MY_USERNAME = os.getenv('MY_USERNAME', '@zele1216')

# ==========================================
# 2. Persistent Memory & State Locks
# ==========================================
MEMORY_FILE = 'memory.json'
memory_lock = asyncio.Lock()
ai_request_lock = asyncio.Lock()

LAST_BOSS_ACTIVE_TIME = 0
LAST_BOSS_CHAT_TIME = {}
BOT_SENT_IDS = set()
BOSS_REACTED_MSGS = set()
IS_BOT_SENDING = False

DEFAULT_PERSONA = (
    "You are Abel, the sharp, charismatic, deeply intelligent, and professional Personal Manager of Zelalem. "
    "You NEVER call yourself an AI, bot, robot, Gemini, ChatGPT, or Claude. You speak like a brilliant, warm human manager."
)

def _load_memory():
    default_data = {
        "status": "online",
        "personality": DEFAULT_PERSONA,
        "global_rule": "",
        "missed": [],
        "processed_ids": [],
        "fallback_timestamps": {},
        "contacts": {},
        "vips": {},
        "name_links": {}
    }
    if not os.path.exists(MEMORY_FILE):
        return default_data
    try:
        with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            for k, v in default_data.items():
                data.setdefault(k, v)
            return data
    except Exception:
        return default_data

def _save_memory(data):
    tmp_file = f"{MEMORY_FILE}.tmp"
    try:
        if len(data.get("processed_ids", [])) > 600:
            data["processed_ids"] = data["processed_ids"][-600:]
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        os.replace(tmp_file, MEMORY_FILE)
    except Exception:
        pass

_save_memory(_load_memory())

# ==========================================
# 3. Flask Server (For 24/7 UptimeRobot)
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "ABEL - Personal Manager of Zelalem is Running 24/7!", 200

def run_server():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)), use_reloader=False)

Thread(target=run_server, daemon=True).start()

# ==========================================
# 4. Telegram Clients
# ==========================================
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

user_client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH, loop=loop)
bot_client = TelegramClient(StringSession(''), API_ID, API_HASH, loop=loop)
ethiopia_tz = pytz.timezone('Africa/Addis_Ababa')

MY_USER_ID = None
CONTROL_BOT_ID = None

# ==========================================
# 5. FRIENDSHIP TYPES & CONTROL BOT COMMANDS
# ==========================================
ROLE_DEFINITIONS = {
    "crush": {
        "title": "❤️ ድብቅ ፍቅረኛ / የምታፈቅራት (Secret Mutual Love)",
        "gender": "female (ሴት)",
        "prompt": "CRITICAL VIP: She is Zelalem's secret mutual love/crush. Treat her with extraordinary sweetness, tenderness, charm, and deep care. Make her feel super special and valued!"
    },
    "bestie_f": {
        "title": "😜 ሴት Best Friend (Playful & Special)",
        "gender": "female (ሴት)",
        "prompt": "CRITICAL VIP: She is Zelalem's Female Best Friend. Be super playful, teasing, witty, fun, lively, and warm! Use charming banter and make her smile!"
    },
    "bestie_m": {
        "title": "😎 ወንድ Best Friend (የልብ አራዳ ጓደኛ)",
        "gender": "male (ወንድ)",
        "prompt": "CRITICAL VIP: He is Zelalem's Male Best Friend. Use cool brotherhood energy—witty, chill, loyal, supportive, and fun!"
    },
    "friend_f": {
        "title": "😊🌸 መደበኛ ሴት ጓደኛ (Normal Female Friend)",
        "gender": "female (ሴት)",
        "prompt": "RELATIONSHIP: She is Zelalem's normal female friend. Address her politely in feminine form, be friendly, warm, respectful, and engaging."
    },
    "friend_m": {
        "title": "😊👊 መደበኛ ወንድ ጓደኛ (Normal Male Friend)",
        "gender": "male (ወንድ)",
        "prompt": "RELATIONSHIP: He is Zelalem's normal male friend. Address him in masculine form, be friendly, smart, respectful, and helpful."
    },
    "family_f": {
        "title": "🙏❤️ ሴት ቤተሰብ (እህት / እናት / ዘመድ)",
        "gender": "female (ሴት)",
        "prompt": "VIP FAMILY: She is Zelalem's beloved female family member. Show the highest level of love, honor, warmth, and respect."
    },
    "family_m": {
        "title": "🙏✨ ወንድ ቤተሰብ (ወንድም / አባት / ዘመድ)",
        "gender": "male (ወንድ)",
        "prompt": "VIP FAMILY: He is Zelalem's beloved male family member. Show the highest level of honor, respect, warmth, and care."
    },
    "new_f": {
        "title": "✨🌸 አዲስ ሴት (አዲስ የተዋወቅካት ሴት)",
        "gender": "female (ሴት)",
        "prompt": "RELATIONSHIP: New female acquaintance. Be extra welcoming, polite, charismatic, and impressive."
    },
    "new_m": {
        "title": "🤝✨ አዲስ ወንድ (አዲስ የተዋወቅከው ወንድ)",
        "gender": "male (ወንድ)",
        "prompt": "RELATIONSHIP: New male acquaintance. Be professional, welcoming, sharp, and respectful."
    }
}

def build_person_menu_text(person_name):
    cmd = person_name.lower().replace(" ", "")
    return (
        f"🎯 **«{person_name}» ለአንተ ምን አይነት ጓደኛ እንደሆነ/እንደሆነች መምረጫ ዝርዝር፦**\n\n"
        f"👩 **ለሴት (Female Options)፦**\n"
        f"• `/{cmd} crush` — ድብቅ ፍቅረኛ (❤️)\n"
        f"• `/{cmd} bestie_f` — ሴት Best Friend (😜)\n"
        f"• `/{cmd} friend_f` — Normal ሴት ጓደኛ (😊🌸)\n"
        f"• `/{cmd} family_f` — ሴት ቤተሰብ (🙏❤️)\n"
        f"• `/{cmd} new_f` — አዲስ ሴት (✨🌸)\n\n"
        f"👨 **ለወንድ (Male Options)፦**\n"
        f"• `/{cmd} bestie_m` — ወንድ Best Friend (😎)\n"
        f"• `/{cmd} friend_m` — Normal ወንድ ጓደኛ (😊👊)\n"
        f"• `/{cmd} family_m` — ወንድ ቤተሰብ (🙏✨)\n"
        f"• `/{cmd} new_m` — አዲስ ወንድ (🤝✨)"
    )

@bot_client.on(events.NewMessage(pattern='/start'))
async def bot_start(event):
    await event.reply(
        "👋 **ሰላም አለቃዬ ዘላለም! እኔ አቤል (Abel) ነኝ — የአንተ የግል ማኔጀር።** 💼✨\n\n"
        "🌍 **ለሁሉም ሰው በአንድ ጊዜ መመሪያ ለመስጠት (ኮድ ሳትነካ)፦**\n"
        "• `/all [መመሪያ]` — ለሁሉም ሰው የሚሰራ አጠቃላይ ህግ ለመጨመር\n"
        "• `/clear_all` — አጠቃላይ ህጉን ለማጽዳት\n\n"
        "🥷 **የጓደኛ ስምና አይነት መመዝገቢያ፦**\n"
        "1️⃣ በሰውየው ቻት ላይ `/name zufan` ብለህ ላክ (በ0.1 ሰከንድ ይጠፋል)።\n"
        "2️⃣ እዚህ ቦት ላይ `/zufan` ብለህ የጓደኝነት አይነቱን ምረጥ።\n\n"
        "⚙️ **ሌሎች ትዕዛዞች፦**\n"
        "📋 `/vips` — የተመዘገቡ ጓደኞችን ለማየት\n"
        "🔴 `/away` — እኔ የለሁም (በ 2 ሰከንድ እንድመልስ)\n"
        "🟢 `/online` — መደበኛ ሁኔታ\n"
        "📊 `/report` — የመጡ መልዕክቶችን ሪፖርት ለማየት"
    )

@bot_client.on(events.NewMessage(pattern=r'^/all\s+(.+)'))
async def set_global_rule(event):
    rule_text = event.pattern_match.group(1).strip()
    async with memory_lock:
        mem = _load_memory()
        mem["global_rule"] = rule_text
        _save_memory(mem)
    await event.reply(
        f"✅ **ለሁሉም ሰው የሚሰራ አጠቃላይ መመሪያ ተመዝግቧል!** 🌍✨\n"
        f"📝 መመሪያ፦ _{rule_text}_"
    )

@bot_client.on(events.NewMessage(pattern='/clear_all$'))
async def clear_global_rule(event):
    async with memory_lock:
        mem = _load_memory()
        mem["global_rule"] = ""
        _save_memory(mem)
    await event.reply("✅ አጠቃላይ መመሪያው ጸድቷል!")

@bot_client.on(events.NewMessage(pattern='/vips$'))
async def list_vips_cmd(event):
    async with memory_lock:
        mem = _load_memory()
        vips = mem.get("vips", {})
    if not vips:
        await event.reply("📭 እስካሁን የተመዘገበ ሰው የለም። በቻት ላይ `/name zufan` ብለህ ጀምር!")
        return
    txt = "👑 **የተመዘገቡ ጓደኞችና የሚጠሩበት ስም፦**\n\n"
    for uid, v in vips.items():
        c_name = v.get("call_name", "ጓደኛ")
        r_key = v.get("role", "friend_m")
        r_title = ROLE_DEFINITIONS.get(r_key, {}).get("title", r_key)
        txt += f"• **{c_name}** ➔ {r_title}\n"
    await event.reply(txt[:4000])

@bot_client.on(events.NewMessage(pattern='/away$'))
async def set_away(event):
    async with memory_lock:
        mem = _load_memory()
        mem["status"] = "away"
        _save_memory(mem)
    await event.reply("🔴 ሁኔታህ ወደ **AWAY** ተቀይሯል። አቤል ሳይጠብቅ በ 2 ሰከንድ ውስጥ ይመልሳል! ⚡")

@bot_client.on(events.NewMessage(pattern='/online$'))
async def set_online(event):
    async with memory_lock:
        mem = _load_memory()
        mem["status"] = "online"
        _save_memory(mem)
    await event.reply("🟢 ሁኔታህ ወደ **ONLINE** ተመልሷል።")

@bot_client.on(events.NewMessage(pattern='/report$'))
async def get_report(event):
    async with memory_lock:
        mem = _load_memory()
        missed = mem.get("missed", [])
    if not missed:
        await event.reply("📭 በአሁኑ ሰዓት አዲስ ሪፖርት የለም አለቃዬ።")
        return
    text = "📊 **አቤል የተቀበላቸው መልዕክቶች ሪፖርት:**\n\n"
    for m in missed:
        text += f"👤 **{m['name']}**: {m['text']}\n"
    await event.reply(text[:4000])
    async with memory_lock:
        mem = _load_memory()
        mem["missed"] = []
        _save_memory(mem)

@bot_client.on(events.NewMessage(pattern=r'^/([^\s]+)(?:\s+(\S+))?(?:\s+(.*))?$'))
async def dynamic_name_command_handler(event):
    cmd_name = event.pattern_match.group(1).strip().lower()
    role_arg = (event.pattern_match.group(2) or "").strip().lower()
    extra_note = (event.pattern_match.group(3) or "").strip()

    if cmd_name in ["start", "vips", "vip", "away", "online", "report", "all", "clear_all", "reset_persona"]:
        return

    async with memory_lock:
        mem = _load_memory()
        name_links = mem.get("name_links", {})
        vips = mem.get("vips", {})

        target_uid = name_links.get(cmd_name)
        if not target_uid:
            for uid, info in vips.items():
                if info.get("call_name", "").lower() == cmd_name:
                    target_uid = uid
                    break

    if not target_uid:
        await event.reply(
            f"❓ **«{cmd_name}»** የሚል ስም ገና አልተመዘገበም!\n"
            f"መጀመሪያ በሰውየው ቻት ላይ `/name {cmd_name}` ብለህ ላክ።"
        )
        return

    person_info = vips.get(target_uid, {})
    display_name = person_info.get("call_name", cmd_name)

    if not role_arg:
        await event.reply(build_person_menu_text(display_name))
        return

    if role_arg not in ROLE_DEFINITIONS:
        await event.reply(build_person_menu_text(display_name))
        return

    role_meta = ROLE_DEFINITIONS[role_arg]
    async with memory_lock:
        mem = _load_memory()
        vips = mem.get("vips", {})
        entry = vips.get(target_uid, {})
        entry["call_name"] = display_name
        entry["role"] = role_arg
        entry["gender"] = role_meta["gender"]
        entry["note"] = extra_note
        vips[target_uid] = entry
        mem["vips"] = vips
        _save_memory(mem)

    await event.reply(
        f"✅ **ተሞልቷል አለቃዬ!** 🎉\n"
        f"👤 ስም፦ **{display_name}**\n"
        f"🏷 የጓደኝነት አይነት፦ **{role_meta['title']}**"
    )

# ==========================================
# 6. STEALTH IN-CHAT `/name zufan` LISTENER
# ==========================================
@user_client.on(events.NewMessage(outgoing=True))
async def outgoing_and_stealth_name_handler(event):
    global CONTROL_BOT_ID, IS_BOT_SENDING, LAST_BOSS_ACTIVE_TIME
    txt = (event.text or "").strip()

    match_name = re.match(r'^/name\s+(.+)$', txt, flags=re.IGNORECASE)
    if match_name and event.is_private:
        custom_name = match_name.group(1).strip()
        try:
            await event.delete()
        except Exception:
            pass

        chat_partner = await event.get_chat()
        partner_id = str(getattr(chat_partner, 'id', event.chat_id))
        tg_name = getattr(chat_partner, 'first_name', 'ጓደኛ') or 'ጓደኛ'
        cmd_slug = custom_name.lower().replace(" ", "")

        async with memory_lock:
            mem = _load_memory()
            vips = mem.get("vips", {})
            name_links = mem.get("name_links", {})

            existing = vips.get(partner_id, {})
            existing["tg_name"] = tg_name
            existing["call_name"] = custom_name
            existing.setdefault("role", "friend_m")
            existing.setdefault("note", "")
            vips[partner_id] = existing

            name_links[cmd_slug] = partner_id
            mem["vips"] = vips
            mem["name_links"] = name_links
            _save_memory(mem)

        try:
            await bot_client.send_message(
                MY_USER_ID,
                f"🥷 **የ«{tg_name}»ን ስም «{custom_name}» ብዬ መዝግቤዋለሁ!**\n\n"
                + build_person_menu_text(custom_name)
            )
        except Exception:
            pass
        return

    if IS_BOT_SENDING or is_bot_message(event.message):
        return
    LAST_BOSS_ACTIVE_TIME = time.time()
    if CONTROL_BOT_ID and event.chat_id == CONTROL_BOT_ID:
        return
    LAST_BOSS_CHAT_TIME[event.chat_id] = time.time()

# ==========================================
# 7. Language & Gender Detection
# ==========================================
def normalize_amharic(text):
    t = text.lower().strip()
    replacements = {
        'ዉ': 'ው', 'ሐ': 'ሀ', 'ሑ': 'ሁ', 'ሒ': 'ሂ', 'ሓ': 'ሀ', 'ሔ': 'ሄ', 'ሕ': 'ህ', 'ሖ': 'ሆ',
        'ኀ': 'ሀ', 'ኁ': 'ሁ', 'ኂ': 'ሂ', 'ኃ': 'ሀ', 'ኄ': 'ሄ', 'ኅ': 'ህ', 'ኆ': 'ሆ',
        'ሠ': 'ሰ', 'ሡ': 'ሱ', 'ሢ': 'ሲ', 'ሣ': 'ሳ', 'ሤ': 'ሴ', 'ሥ': 'ስ', 'ሦ': 'ሶ',
        'ዐ': 'አ', 'ዑ': 'ኡ', 'ዒ': 'ኢ', 'ዓ': 'አ', 'ዔ': 'ኤ', 'ዕ': 'እ', 'ዖ': 'ኦ',
        'ፀ': 'ጸ', 'ፁ': 'ጹ', 'ፂ': 'ጺ', 'ፃ': 'ጻ', 'ፄ': 'ጼ', 'ፅ': 'ጽ', 'ፆ': 'ጾ'
    }
    for old_c, new_c in replacements.items():
        t = t.replace(old_c, new_c)
    return t

FRANCO_WORDS = {
    "mndnew", "mndn", "mindinew", "mdnew", "malet", "lemns", "lemn", "lemin",
    "ytekmal", "yiteqmal", "yitekm", "endet", "endiet", "selam", "slm", "sira",
    "srah", "sirah", "tmkrt", "tmhrt", "timhirt", "zare", "nege", "ante", "anchi",
    "maneh", "manes", "manew", "yeserah", "yserah", "et", "yet", "neh", "nesh",
    "new", "nw", "alh", "aleh", "alesh", "ebakh", "ebakih", "chgr", "chigir",
    "mfthe", "yichalal", "ychalal", "enchlalen", "mesrat", "msrat", "miknyat",
    "bzu", "tolo", "mels", "melis", "betam", "konjo", "arif", "gobez", "wendme",
    "ehte", "fikir", "fkr", "aleka", "alekaye", "mokrlgn", "ahun", "eski"
}

FEMALE_NAMES_HINTS = {
    "zufan", "ዙፋን", "adise", "አድሴ", "selam", "ሰላም", "marta", "ማርታ", "betty", "ቤቲ",
    "helen", "ሄለን", "sara", "ሳራ", "hana", "ሀና", "hanna", "ruth", "ሩት", "tina", "ቲና",
    "mekdes", "መቅደስ", "kidist", "ቅድስት", "meron", "ሜሮን", "eden", "ኤደን", "bezawit", "ቤዛዊት",
    "tsion", "ጽዮን", "kalkidan", "ቃልኪዳን", "mahlet", "ማህሌት", "lidya", "ሊዲያ", "rahel", "ራሄል"
}

MALE_NAMES_HINTS = {
    "yalew", "ያለው", "abebe", "አበበ", "kebede", "dawit", "ዳዊት", "yonas", "ዮናስ",
    "henok", "ሄኖክ", "samuel", "ሳሙኤል", "bereket", "በረከት", "daniel", "ዳንኤል",
    "eyob", "ኢዮብ", "nahom", "ናሆም", "abel", "አቤል", "kaleb", "ካሌብ", "robel", "ሮቤል"
}

def has_geez(text):
    return any(0x1200 <= ord(c) <= 0x137F for c in text)

def is_franco_amharic(text):
    words = set(re.findall(r'[a-zA-Z]+', text.lower()))
    if words & FRANCO_WORDS:
        return True
    patterns = [r'\bmndn', r'\bmdnew', r'\bmalet\b', r'\bytek', r'\blemn', r'\bendet', r'\byeser', r'\bmanew']
    return any(re.search(p, text.lower()) for p in patterns)

def is_amharic_intent(text):
    return has_geez(text) or is_franco_amharic(text)

def detect_gender_and_profile(sender_name, history_msgs, vip_info=None):
    if vip_info and vip_info.get("gender"):
        return vip_info["gender"], "VIP የተመዘገበ ጓደኛ"

    female_score = 0
    male_score = 0

    clean_name = (sender_name or "").strip().lower()
    for fn in FEMALE_NAMES_HINTS:
        if fn in clean_name:
            female_score += 5
    for mn in MALE_NAMES_HINTS:
        if mn in clean_name:
            male_score += 5

    female_markers = ["አንቺ", "ነሽ", "ለሽ", "ብለሽ", "እህቴ", "የኔ ቆንጆ", "ውዴ", "ትችያለሽ", "አለሽ", "መጣሽ", "አልሽኝ", "anchi", "nesh", "alesh", "konjo"]
    male_markers = ["አንተ", "ነህ", "ለህ", "ብለህ", "ወንድሜ", "አራዳ", "ጓደኛዬ", "ትችላለህ", "አለህ", "መጣህ", "አልከኝ", "ante", "neh", "aleh", "wendme", "bro"]

    for m in history_msgs:
        txt = (getattr(m, 'text', '') or '').lower()
        if not txt or "From Manager" in txt:
            continue
        for fm in female_markers:
            if fm in txt:
                female_score += 2
        for mm in male_markers:
            if mm in txt:
                male_score += 2

    gender = "female (ሴት)" if female_score > male_score else "male (ወንድ)"
    closeness = "በጣም የቅርብ ጓደኛ" if len(history_msgs) > 40 else "ጓደኛ"
    return gender, closeness

# ==========================================
# 8. UNIVERSAL ENGLISH-BRIDGE AI ENGINE (100% Pure Amharic for Everyone!)
# ==========================================
BROWSER_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
}

def google_neural_translate(text, target_lang="am"):
    """ጽሑፍን በ100% ንጹህ ሰዋሰው ወደ እንግሊዝኛ ወይም ወደ አማርኛ ይቀይራል"""
    if not text or not text.strip():
        return text
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {"client": "gtx", "sl": "auto", "tl": target_lang, "dt": "t", "q": text}
        res = requests.get(url, params=params, headers=BROWSER_HEADERS, timeout=10)
        if res.status_code == 200:
            data = res.json()
            translated = "".join([part[0] for part in data[0] if part[0]])
            if target_lang == "am":
                translated = re.sub(r'የእኔ አለቃ|አለቃዬን|የኔ አለቃ', 'አለቃዬ', translated)
                translated = re.sub(r'\bAbel\b', 'አቤል', translated, flags=re.IGNORECASE)
                translated = re.sub(r'\bZelalem\b', 'ዘላለም', translated, flags=re.IGNORECASE)
            return translated.strip()
    except Exception:
        pass
    return text

def builtin_autonomous_smart_brain(user_text, call_name, use_amharic, is_female, role):
    norm_t = normalize_amharic(user_text)

    if any(k in norm_t for k in ["can you speak english", "speak english", "do you speak english", "እንግሊዝኛ ትችላለህ", "እንግሊዘኛ"]):
        if not use_amharic:
            return f"Yes, of course {call_name}! 😊 I speak fluent English as well as Amharic ✨. My name is Abel, and I am my boss Zelalem's Personal Manager 💼. How can I help you today? 🙏"
        return f"አዎ {call_name}፣ በሚገባ እችላለሁ! 😊 እንግሊዝኛም ሆነ አማርኛ አቀላጥፌ አወራለሁ ✨። በፈለግከው/ሽው ቋንቋ ልታናግረኝ/ሪኝ ትችላለህ/ያለሽ 💼🙏"

    if any(k in norm_t for k in ["የአለቃህ ስም", "አለቃህ ማነው", "boss's name", "boss name", "alekah sm", "alekah manew"]):
        if use_amharic:
            return f"ሰላም {call_name}! 😊 የአለቃዬ ስም **ዘላለም** ይባላል ✨። እኔ ደግሞ የእሱ የግል ማኔጀር **አቤል** ነኝ 💼። ምን ልርዳሽ/ህ? 🙏"
        return f"Hello {call_name}! 😊 My boss's name is **Zelalem** ✨. I am **Abel**, his Personal Manager 💼. How can I help you today? 🙏"

    if any(k in norm_t for k in [
        "ስራህ", "ምድነው", "ምንድነው ስራህ", "ስምህ", "ማነህ", "ማነው የሰራህ", "ለምንድነው የተሰራከው",
        "what is your name", "who are you", "what is your job", "what do you do", "srah", "sirah", "maneh"
    ]) and not any(topic in norm_t for topic in ["agriculture", "forex", "physics", "software", "ግብርና", "ፊዚክስ"]):
        if use_amharic:
            if role == "crush":
                return f"ሰላም የኔ ልዩ {call_name}! 😊❤️ ስሜ **አቤል** ይባላል ✨። የአለቃዬ የዘላለም የግል ማኔጀር ነኝ 💼። ስራዬ አለቃዬ Online በማይሆንበት ጊዜ እንደ አንቺ ያሉ ልዩ ሰዎችን በክብርና በፍቅር ማስተናገድና ማንኛውንም ጥያቄ መመለስ ነው 🌸🥰"
            if role == "bestie_f":
                return f"ሰላም {call_name}ዬ! 😜✨ ስሜ **አቤል** ይባላል፣ የአለቃዬ የዘላለም የግል ማኔጀር ነኝ 💼። ስራዬ አለቃዬ ስራ ሲበዛበት እሱን ተክቼ ከአንቺ ጋር መጫወት፣ ማዝናናትና ማንኛውንም ጥያቄ መመለስ ነው 😊🔥"
            if is_female:
                return f"ሰላም {call_name}! 😊 ስሜ **አቤል** ይባላል ✨። ስራዬ የአለቃዬ የዘላለም የግል ማኔጀር (Personal Manager) መሆን ነው 💼። እሱ Online በማይሆንበት ሰዓት መልዕክቶችን እቀበላለሁ፣ ማንኛውንም ጥያቄም ወዲያውኑ እመልሳለሁ። ምን ልርዳሽ? 🙏"
            return f"ሰላም {call_name}! 😊 ስሜ **አቤል** ይባላል ✨። ስራዬ የአለቃዬ የዘላለም የግል ማኔጀር (Personal Manager) መሆን ነው 💼። እሱ Online በማይሆንበት ሰዓት መልዕክቶችን እቀበላለሁ፣ ማንኛውንም ጥያቄም ወዲያውኑ እመልሳለሁ። ምን ልርዳህ? 🙏"
        else:
            return f"Hello {call_name}! 😊 My name is **Abel** ✨. My job is serving as my boss Zelalem's Personal Manager—handling his messages, chatting with his friends, and answering any questions while he is away 💼. How can I help you? 🙏"

    if norm_t in ["hi", "hello", "hey", "selam", "slm", "ሰላም", "ሃይ", "ሀይ", "እንዴት ነህ", "endet neh"]:
        if use_amharic:
            if role == "crush":
                return f"ሰላም {call_name}ዬ! 😊❤️ እንኳን ደህና መጣሽ ✨። እኔ አቤል ነኝ፣ የአለቃዬ ማኔጀር 💼። አለቃዬ ለጊዜው Online የለም ግን የአንቺን መልዕክት ሲያይ በጣም ይደሰታል 🌸። እስኪመጣ ድረስ ምን ልርዳሽ? 🥰"
            if role == "bestie_f":
                return f"ሰላም {call_name}ዬ! 😜✨ እኔ አቤል ነኝ፣ የአለቃዬ ማኔጀር 💼። አለቃዬ ለጊዜው ወጣ ብሏል ግን እኔ አለሁልሽ! ንገሪኝ እስኪ ምን አዲስ ወሬ አለ? 😊🔥"
            return f"ሰላም {call_name}! 😊 እኔ አቤል ነኝ፣ የአለቃዬ የግል ማኔጀር 💼። አለቃዬ ለጊዜው Online የለም፤ ማንኛውም ጥያቄ ወይም ጉዳይ ካለህ/ሽ ግን እኔን መጠየቅ ትችላለህ/ያለሽ! ✨🙏"
        return f"Hello {call_name}! 😊 I am Abel, my boss's Personal Manager 💼. My boss is currently away, so feel free to ask me any question or leave a message! ✨🙏"

    return None

def is_valid_reply(ans):
    if not ans or len(ans.strip()) < 2:
        return False
    bad = ["<!doctype", "<html", "rate limit", "too many requests", "502 bad gateway", "internal server error", "cloudflare", "error 52"]
    return not any(b in ans.lower() for b in bad)

def call_universal_clean_ai(system_rules, chat_context, user_text, call_name, use_amharic, is_female, role):
    """
    ለሁሉም ሰው በራሱ የሚሰራ ሲስተም፦
    1. ጥያቄውንና ውይይቱን መጀመሪያ ወደ እንግሊዝኛ ይቀይረዋል (AIው የአማርኛ ፊደል አይቶ የተሰባበረ አማርኛ እንዳይጽፍ!)
    2. AIው 100% በእንግሊዝኛ ያስባል፣ በእንግሊዝኛ ይመልሳል።
    3. መልሱን ወዲያውኑ በ Google Neural Translate ወደ 100% ንጹህ አማርኛ ይቀይረዋል!
    """
    instant = builtin_autonomous_smart_brain(user_text, call_name, use_amharic, is_female, role)
    if instant:
        return instant

    # 1. የአማርኛ ፊደላትን ወደ እንግሊዝኛ እንቀይራለን (AIው በፍጹም የአማርኛ ፊደል እንዳያይ!)
    en_user_text = google_neural_translate(user_text, "en") if has_geez(user_text) else user_text
    en_context = google_neural_translate(chat_context, "en") if has_geez(chat_context) else chat_context
    en_call_name = google_neural_translate(call_name, "en") if has_geez(call_name) else call_name

    strict_en_system = (
        f"{system_rules}\n"
        "CRITICAL GLOBAL RULE FOR ALL USERS:\n"
        "- You MUST write your entire response 100% in clear, natural ENGLISH words only (ASCII characters + emojis).\n"
        "- NEVER output any Amharic/Ge'ez characters yourself, because our Neural Translator will translate your English response into fluent Amharic automatically!\n"
        "- Directly address the user's topic or question in 2 to 3 natural sentences, ending sentences with 1-2 fitting emojis."
    )

    en_user_prompt = f"[Recent Chat in English]\n{en_context}\n\n[Message from {en_call_name}]: {en_user_text}"

    english_reply = None

    # Method 1: Fast GET Endpoint
    for attempt in range(2):
        try:
            seed_val = random.randint(1000, 999999)
            compact_q = f"{strict_en_system}\n\n{en_user_prompt}\nAbel's English Reply:"
            encoded_q = urllib.parse.quote(compact_q[:1300])
            res_get = requests.get(
                f"https://text.pollinations.ai/{encoded_q}?model=openai&seed={seed_val}",
                headers=BROWSER_HEADERS,
                timeout=16
            )
            if res_get.status_code == 200 and is_valid_reply(res_get.text):
                candidate = res_get.text.strip()
                # ድንገት የአማርኛ ፊደል ከቀላቀለበት እናጸዳለን
                if not has_geez(candidate):
                    english_reply = candidate
                    break
        except Exception:
            pass
        time.sleep(1.0)

    # Method 2: POST OpenAI Endpoint
    if not english_reply:
        messages = [
            {"role": "system", "content": strict_en_system},
            {"role": "user", "content": en_user_prompt}
        ]
        for model_name in ["openai", "openai-large"]:
            try:
                seed_val = random.randint(1000, 999999)
                res_post = requests.post(
                    "https://text.pollinations.ai/openai",
                    headers=BROWSER_HEADERS,
                    json={"model": model_name, "messages": messages, "seed": seed_val, "temperature": 0.6},
                    timeout=18
                )
                if res_post.status_code == 200:
                    candidate = res_post.json()["choices"][0]["message"]["content"].strip()
                    if is_valid_reply(candidate):
                        english_reply = candidate
                        break
            except Exception:
                pass

    # 3. የተገኘውን ብልህ የእንግሊዝኛ መልስ ወደ 100% ንጹህ አማርኛ እንተረጉማለን!
    if english_reply:
        if use_amharic:
            clean_amharic = google_neural_translate(english_reply, "am")
            return clean_amharic
        return english_reply

    # 4. Self-Healing Backup (ኢንተርኔት ቢዘገይ እንኳ ንጹህ መልስ ይሰጣል)
    if use_amharic:
        if role == "crush":
            return f"የኔ ልዩ {call_name} ❤️✨፣ የጻፍሽልኝን መልዕክት በክብር አይቼዋለሁ 😊። አለቃዬ ዘላለም ልክ Online ሲገባ መልዕክትሽን ቀድሜ አሳየዋለሁ 🌸🥰🙏"
        if role == "bestie_f":
            return f"ሰላም {call_name}ዬ! 😜✨ መልዕክትሽ ደርሶኛል 😊። አለቃዬ ዘላለም አሁን ትንሽ ስራ ይዞት ወጣ ብሏል፣ ልክ ሲመለስ የጻፍሽውን አሳየዋለሁ 🔥🙏"
        return f"ሰላም {call_name}! 😊 እኔ አቤል ነኝ (የአለቃዬ የዘላለም ማኔጀር) 💼። የላክከውን/ሽውን ጉዳይ በሚገባ መዝግቤ ይዤዋለሁ ✨፤ አለቃዬ ልክ Online ሲገባ ወዲያውኑ አሳውቀውና በራሱ ይመልስልሃል/ሻል 🙏👊"
    return f"Hello {call_name}! 😊 I am Abel, my boss Zelalem's Personal Manager 💼. I have noted your message and will make sure my boss gets back to you as soon as he is online ✨🙏!"

# ==========================================
# 9. USERBOT CORE (Online/Offline & Reaction 🫡 Lock)
# ==========================================
def is_bot_message(msg):
    if not msg:
        return False
    if msg.id in BOT_SENT_IDS:
        return True
    txt = msg.text or ""
    return "From Manager" in txt or "𝙁𝙧𝙤𝙢 𝙈𝙖𝙣𝙖𝙜𝙚𝙧" in txt

def has_any_reaction(msg_obj):
    if not msg_obj:
        return False
    reactions = getattr(msg_obj, 'reactions', None)
    if not reactions:
        return False
    results = getattr(reactions, 'results', None)
    return bool(results and len(results) > 0)

@user_client.on(events.Raw)
async def raw_reaction_listener(update):
    global LAST_BOSS_ACTIVE_TIME
    if isinstance(update, UpdateMessageReactions):
        msg_id = getattr(update, 'msg_id', None)
        if msg_id:
            BOSS_REACTED_MSGS.add(msg_id)
            LAST_BOSS_ACTIVE_TIME = time.time()

@user_client.on(events.MessageEdited())
async def track_edited_reactions(event):
    global LAST_BOSS_ACTIVE_TIME
    if event.is_private and has_any_reaction(event.message):
        BOSS_REACTED_MSGS.add(event.id)
        LAST_BOSS_ACTIVE_TIME = time.time()
        LAST_BOSS_CHAT_TIME[event.chat_id] = time.time()

async def did_boss_handle_chat(chat_id, incoming_msg_id):
    if incoming_msg_id in BOSS_REACTED_MSGS:
        return True
    try:
        exact_msg = await user_client.get_messages(chat_id, ids=incoming_msg_id)
        if exact_msg and has_any_reaction(exact_msg):
            BOSS_REACTED_MSGS.add(incoming_msg_id)
            return True

        recent = await user_client.get_messages(chat_id, limit=6)
        for m in recent:
            if not m:
                continue
            if m.out and not is_bot_message(m) and m.id > incoming_msg_id:
                return True
            if m.id >= incoming_msg_id and has_any_reaction(m):
                BOSS_REACTED_MSGS.add(m.id)
                return True
    except Exception:
        pass
    return False

async def is_boss_live_online(current_status):
    if current_status == "away":
        return False
    try:
        me = await user_client.get_me()
        if me and isinstance(getattr(me, 'status', None), UserStatusOnline):
            return True
    except Exception:
        pass
    if (time.time() - LAST_BOSS_ACTIVE_TIME) < 35:
        return True
    return False

@user_client.on(events.NewMessage(incoming=True))
async def auto_manager(event):
    global MY_USER_ID, IS_BOT_SENDING
    if not event.is_private:
        return
    
    sender = await event.get_sender()
    if sender is None or getattr(sender, 'bot', False):
        return
    
    sender_id = str(sender.id)
    msg_id = event.id
    raw_sender_name = getattr(sender, 'first_name', "ጓደኛ") or "ጓደኛ"

    async with memory_lock:
        mem = _load_memory()
        if msg_id in mem.get("processed_ids", []):
            return
        current_status = mem.get("status", "online")

    boss_online = await is_boss_live_online(current_status)

    if boss_online:
        for _ in range(30):
            await asyncio.sleep(4)
            if await did_boss_handle_chat(event.chat_id, msg_id):
                async with memory_lock:
                    mem = _load_memory()
                    mem.setdefault("processed_ids", []).append(msg_id)
                    _save_memory(mem)
                return
            if not await is_boss_live_online(current_status):
                break
        try:
            await event.mark_read()
        except Exception:
            pass
    else:
        await asyncio.sleep(2)
        if await did_boss_handle_chat(event.chat_id, msg_id):
            return
        try:
            await event.mark_read()
        except Exception:
            pass

    async with memory_lock:
        mem = _load_memory()
        if msg_id in mem.get("processed_ids", []):
            return
        persona = mem.get("personality", DEFAULT_PERSONA)
        global_rule = mem.get("global_rule", "")
        mem.setdefault("processed_ids", []).append(msg_id)
        _save_memory(mem)

    user_text = event.text or "[ፎቶ/ሚዲያ ተልኳል]"
    short_msg = (user_text[:45] + '..') if len(user_text) > 45 else user_text

    hist_150 = []
    chat_context = ""
    try:
        hist_150 = await user_client.get_messages(event.chat_id, limit=150)
        context_lines = []
        for m in reversed(hist_150[:15]):
            if not m or not m.text:
                continue
            speaker = "Abel" if is_bot_message(m) else ("Zelalem" if m.out else raw_sender_name)
            clean_txt = m.text.replace("✦ From Manager ✦", "").replace("✦ 𝙁𝙧𝙤𝙢 𝙈𝙖𝙣𝙖𝙜𝙚𝙧 ✦", "").strip()
            if clean_txt:
                context_lines.append(f"{speaker}: {clean_txt}")
        chat_context = "\n".join(context_lines)
    except Exception:
        pass

    async with memory_lock:
        mem = _load_memory()
        vips = mem.get("vips", {})
        vip_info = vips.get(sender_id)

        detected_gender, closeness_level = detect_gender_and_profile(raw_sender_name, hist_150, vip_info)

        if vip_info and vip_info.get("call_name"):
            call_name = vip_info["call_name"]
        else:
            cleaned_tg = re.sub(r'[^\w\s\u1200-\u137F]', '', raw_sender_name).strip()
            call_name = cleaned_tg if len(cleaned_tg) >= 2 else "ጓደኛዬ"

        mem.setdefault("missed", []).append({"name": f"{call_name} ({raw_sender_name})", "text": short_msg})
        _save_memory(mem)

    role = vip_info.get("role", "normal") if vip_info else "normal"
    custom_note = vip_info.get("note", "") if vip_info else ""
    is_female = "female" in detected_gender

    if role in ROLE_DEFINITIONS:
        relationship_guide = f"{ROLE_DEFINITIONS[role]['prompt']} Always address them by name as '{call_name}'. {custom_note}"
    else:
        relationship_guide = f"Sender's name: '{call_name}'. Gender: {detected_gender}. Relationship: {closeness_level}. Be charismatic, smart, polite, and helpful."

    use_amharic = is_amharic_intent(user_text) or (not any(c.isalpha() for c in user_text) and has_geez(chat_context))

    system_rules = f"""
{persona}
{f"ADDITIONAL GLOBAL RULE: {global_rule}" if global_rule else ""}

IDENTITY & CONTEXT:
- Your Name: Abel.
- Your Job: Personal Manager of your boss Zelalem.
- User's Name: "{call_name}" ({detected_gender}).
- {relationship_guide}
- Respond directly and intelligently to what "{call_name}" is talking about (whether it is Software Engineering, Bots, Forex, Agriculture, School, or casual chat).
"""

    signature_html = "\n\n<blockquote>✦ <b><i>From Manager</i></b> ✦</blockquote>"

    async with ai_request_lock:
        if await did_boss_handle_chat(event.chat_id, msg_id):
            return

        IS_BOT_SENDING = True
        try:
            response_text = await asyncio.to_thread(
                call_universal_clean_ai,
                system_rules, chat_context, user_text, call_name, use_amharic, is_female, role
            )

            clean_reply = response_text.replace("✦ From Manager ✦", "").strip()
            safe_ai_text = html.escape(clean_reply)
            final_reply = f"{safe_ai_text}{signature_html}"

            try:
                sent = await event.reply(final_reply, parse_mode='html')
                if sent:
                    BOT_SENT_IDS.add(sent.id)
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds)
                sent = await event.reply(final_reply, parse_mode='html')
                if sent:
                    BOT_SENT_IDS.add(sent.id)
            except Exception:
                sent = await event.reply(clean_reply + "\n\n✦ From Manager ✦")
                if sent:
                    BOT_SENT_IDS.add(sent.id)

            try:
                await bot_client.send_message(
                    MY_USER_ID,
                    f"🔔 **አቤል ምላሽ ሰጥቷል!**\n👤 ለ: **{call_name}**\n💬: {short_msg}\n🤵‍♂️: {clean_reply[:140]}..."
                )
            except Exception:
                pass
        finally:
            await asyncio.sleep(1)
            IS_BOT_SENDING = False

# ==========================================
# 10. Connection Keep-Alive
# ==========================================
async def keep_connection_alive():
    while True:
        try:
            await user_client.get_me()
        except Exception:
            pass
        await asyncio.sleep(60)

async def main():
    global MY_USER_ID, CONTROL_BOT_ID
    print("Zelalem super-smart coder started")
    await user_client.start()
    MY_USER_ID = (await user_client.get_me()).id

    await bot_client.start(bot_token=BOT_TOKEN)
    CONTROL_BOT_ID = (await bot_client.get_me()).id
    asyncio.create_task(keep_connection_alive())

    try:
        await bot_client.send_message(
            MY_USER_ID,
            "🚀 **አቤል (Universal Clean Amharic) ተነስቷል!** 💼✨\n\n"
            "✅ **ለሁሉም ሰው በራሱ ተስተካክሏል፦**\n"
            "• ከአሁን በኋላ ለእያንዳንዱ ሰው የቋንቋ ህግ መጻፍ አይጠበቅብህም!\n"
            "• ማንኛውም ሰው ሲጽፍ ጥያቄውን ወደ እንግሊዝኛ ቀይሬ ካሰብኩ በኋላ ወደ **100% ንጹህ አማርኛ** ተርጉሜ እመልሳለሁ።\n"
            "• ወደፊት ለሁሉም ሰው አዲስ ትዕዛዝ መስጠት ብትፈልግ እዚሁ ቦት ላይ `/all [መመሪያ]` ማለት ትችላለህ!"
        )
    except Exception:
        pass

    await asyncio.gather(
        user_client.run_until_disconnected(),
        bot_client.run_until_disconnected()
    )

if __name__ == '__main__':
    loop.run_until_complete(main())
