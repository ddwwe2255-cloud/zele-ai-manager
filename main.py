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
# 1. Environment Variables (ከ Render ብቻ የሚነበቡ)
# ==========================================
load_dotenv()

API_ID = int(os.getenv('API_ID', 0))
API_HASH = os.getenv('API_HASH', '')
SESSION_STRING = os.getenv('SESSION_STRING', '')
BOT_TOKEN = os.getenv('BOT_TOKEN', '')
MY_USERNAME = os.getenv('MY_USERNAME', '@zele1216')

raw_keys = os.getenv('GEMINI_API_KEYS', '')
GEMINI_KEYS = [k.strip() for k in raw_keys.split(',') if k.strip()]

# ==========================================
# 2. Persistent Memory & State Locks
# ==========================================
MEMORY_FILE = 'memory.json'
memory_lock = asyncio.Lock()
ai_request_lock = asyncio.Lock()

LAST_BOSS_ACTIVE_TIME = 0
LAST_BOSS_CHAT_TIME = {}
LATEST_INCOMING_MSG_PER_CHAT = {}  # በተከታታይ ለሚመጡ መልዕክቶች ድጋሚ እንዳይመልስ (Debounce)
BOT_SENT_IDS = set()
BOSS_REACTED_MSGS = set()
IS_BOT_SENDING = False

DEFAULT_PERSONA = (
    "You are Abel, the sharp, charismatic, deeply intelligent, and professional Personal Manager of your boss. "
    "You NEVER call yourself an AI, bot, robot, Gemini, ChatGPT, or Claude. "
    "You ALWAYS refer to your boss as 'my boss' (አለቃዬ) and NEVER mention his real name unless explicitly asked what his name is."
)

def _load_memory():
    default_data = {
        "bot_active": True,
        "status": "online",
        "personality": DEFAULT_PERSONA,
        "global_rule": "",
        "missed": [],
        "processed_ids": [],
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
        if len(data.get("processed_ids", [])) > 800:
            data["processed_ids"] = data["processed_ids"][-800:]
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
    return "ABEL - Personal Manager is Running 24/7!", 200

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
# 5. FRIENDSHIP TYPES & CONTROL BOT (@Manage1216bot)
# ==========================================
ROLE_DEFINITIONS = {
    "crush": {
        "title": "❤️ ድብቅ ፍቅረኛ (እሷም የምታፈቅርህ አንተም የምታፈቅራት)",
        "gender": "female (ሴት)",
        "prompt": "CRITICAL VIP: She is the boss's secret mutual love/crush (both love each other deeply but haven't said it openly). Treat her with extraordinary sweetness, tenderness, charm, playful warmth, and deep care."
    },
    "bestie_f": {
        "title": "😜 ሴት Best Friend (ለየት ያለ ጨዋታና ቀልድ)",
        "gender": "female (ሴት)",
        "prompt": "CRITICAL VIP: She is the boss's Female Best Friend. Be super playful, teasing, witty, fun, lively, and warm!"
    },
    "bestie_m": {
        "title": "😎 ወንድ Best Friend (የልብ አራዳ ጓደኛ)",
        "gender": "male (ወንድ)",
        "prompt": "CRITICAL VIP: He is the boss's Male Best Friend. Use cool brotherhood energy—witty, chill, loyal, supportive, and fun!"
    },
    "friend_f": {
        "title": "😊🌸 Normal ሴት ጓደኛ (መደበኛ ሴት ጓደኛ)",
        "gender": "female (ሴት)",
        "prompt": "RELATIONSHIP: She is the boss's normal female friend. Address her politely in feminine form, be friendly, warm, respectful, and engaging."
    },
    "friend_m": {
        "title": "😊👊 Normal ወንድ ጓደኛ (መደበኛ ወንድ ጓደኛ)",
        "gender": "male (ወንድ)",
        "prompt": "RELATIONSHIP: He is the boss's normal male friend. Address him in masculine form, be friendly, smart, respectful, and helpful."
    },
    "family_f": {
        "title": "🙏❤️ ሴት ቤተሰብ (እህት / እናት / ዘመድ)",
        "gender": "female (ሴት)",
        "prompt": "VIP FAMILY: She is the boss's beloved female family member. Show the highest level of love, honor, warmth, and respect."
    },
    "family_m": {
        "title": "🙏✨ ወንድ ቤተሰብ (ወንድም / አባት / ዘመድ)",
        "gender": "male (ወንድ)",
        "prompt": "VIP FAMILY: He is the boss's beloved male family member. Show the highest level of honor, respect, warmth, and care."
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
        f"ከታች ካሉት ውስጥ የሚመጥነውን አንዱን ትዕዛዝ ላክልኝ፦\n\n"
        f"👩 **ለሴት (Female Options)፦**\n"
        f"• `/{cmd} crush` — ድብቅ ፍቅረኛ (እሷም የምታፈቅርህ አንተም የምታፈቅራት ❤️)\n"
        f"• `/{cmd} bestie_f` — ሴት Best Friend (ለየት ያለ ጨዋታና ቀልድ 😜)\n"
        f"• `/{cmd} friend_f` — Normal ሴት ጓደኛ (😊🌸)\n"
        f"• `/{cmd} family_f` — ሴት ቤተሰብ / እህት / ዘመድ (🙏❤️)\n"
        f"• `/{cmd} new_f` — አዲስ የተዋወቅካት ሴት (✨🌸)\n\n"
        f"👨 **ለወንድ (Male Options)፦**\n"
        f"• `/{cmd} bestie_m` — ወንድ Best Friend (አራዳ የወንድማማች ጨዋታ 😎)\n"
        f"• `/{cmd} friend_m` — Normal ወንድ ጓደኛ (😊👊)\n"
        f"• `/{cmd} family_m` — ወንድ ቤተሰብ / ወንድም / ዘመድ (🙏✨)\n"
        f"• `/{cmd} new_m` — አዲስ የተዋወቅከው ወንድ (🤝✨)\n\n"
        f"💡 *ተጨማሪ መመሪያ ከጎኑ መጻፍ ትችላለህ፦*\n"
        f"ምሳሌ፦ `/{cmd} crush እሷም ታፈቅረኛለች እኔም አፈቅራታለሁ ግን በግልፅ አናወራም`"
    )

@bot_client.on(events.NewMessage(pattern=r'^/(start|resume)$'))
async def bot_start_or_resume(event):
    async with memory_lock:
        mem = _load_memory()
        was_stopped = not mem.get("bot_active", True)
        mem["bot_active"] = True
        _save_memory(mem)

    status_banner = "🚀 **አቤል ከቆመበት ተነስቶ ስራ ጀምሯል!** ✨\n\n" if was_stopped else ""
    await event.reply(
        f"{status_banner}"
        "👋 **ሰላም አለቃዬ! እኔ አቤል (Abel) ነኝ — የአንተ የግል ማኔጀር።** 💼✨\n\n"
        "🛑 **ቦቱን ማቆሚያ እና ማስነሻ፦**\n"
        "• `/stop` — አቤልን ሙሉ በሙሉ በጊዜያዊነት ለማቆም\n"
        "• `/start` (ወይም `/resume`) — የቆመውን አቤል መልሶ ለማስነሳት\n\n"
        "🥷 **ድብቅ የጓደኛ ስምና አይነት መመዝገቢያ፦**\n"
        "1️⃣ በሰውየው ቻት ላይ `/name zufan` ብለህ ላክ (በ0.1 ሰከንድ ይጠፋል፣ ሰውየው አያየውም!)።\n"
        "2️⃣ እዚህ ቦት ላይ `/zufan` ብለህ ጻፍ — ሁሉንም የጓደኝነት አይነቶች አስመርጥሃለሁ!\n\n"
        "🌍 **ለሁሉም ሰው አዲስ ህግ መጨመሪያ (ኮድ ሳትነካ)፦**\n"
        "• `/all [መመሪያ]` — ለሁሉም ሰው የሚሰራ ህግ ለመጨመር\n"
        "• `/clear_all` — አጠቃላይ ህጉን ለማጽዳት\n\n"
        "⚙️ **ሌሎች ትዕዛዞች፦** `/online`, `/away`, `/vips`, `/report`"
    )

@bot_client.on(events.NewMessage(pattern='/stop$'))
async def stop_bot_cmd(event):
    async with memory_lock:
        mem = _load_memory()
        mem["bot_active"] = False
        _save_memory(mem)
    await event.reply(
        "🛑 **አቤል ሙሉ በሙሉ ስራ አቁሟል (Paused)!**\n"
        "ከአሁን ጀምሮ ለማንኛውም መልዕክት መልስ አልሰጥም፣ ሁለት ራይትም አላደርግም።\n"
        "መልሰህ ለማስነሳት **`/start`** ወይም **`/resume`** ብለህ ላክልኝ 💼🙏።"
    )

@bot_client.on(events.NewMessage(pattern=r'^/all\s+(.+)'))
async def set_global_rule(event):
    rule_text = event.pattern_match.group(1).strip()
    async with memory_lock:
        mem = _load_memory()
        mem["global_rule"] = rule_text
        _save_memory(mem)
    await event.reply(f"✅ **ለሁሉም ሰው የሚሰራ መመሪያ ተመዝግቧል አለቃዬ!** 🌍\n📝 መመሪያ፦ _{rule_text}_")

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
        await event.reply("📭 እስካሁን የተመዘገበ ሰው የለም። በቻት ላይ `/name [ስም]` ብለህ ጀምር!")
        return
    txt = "👑 **የተመዘገቡ ጓደኞችና የሚጠሩበት ስም፦**\n\n"
    for uid, v in vips.items():
        c_name = v.get("call_name", "ጓደኛ")
        r_key = v.get("role", "friend_m")
        r_title = ROLE_DEFINITIONS.get(r_key, {}).get("title", r_key)
        note = v.get("note", "")
        txt += f"• **{c_name}** ➔ {r_title} {f'({note})' if note else ''}\n"
    await event.reply(txt[:4000])

@bot_client.on(events.NewMessage(pattern='/away$'))
async def set_away(event):
    async with memory_lock:
        mem = _load_memory()
        mem["status"] = "away"
        _save_memory(mem)
    await event.reply("🔴 ሁኔታህ ወደ **AWAY** ተቀይሯል። አቤል ሳይጠብቅ በሰከንዶች ውስጥ ይመልሳል! ⚡")

@bot_client.on(events.NewMessage(pattern='/online$'))
async def set_online(event):
    async with memory_lock:
        mem = _load_memory()
        mem["status"] = "online"
        _save_memory(mem)
    await event.reply("🟢 ሁኔታህ ወደ **ONLINE** ተመልሷል። አንተ Online ስትሆን ሁለት ራይት ሳላደርግ 2 ደቂቃ እጠብቅሃለሁ!")

@bot_client.on(events.NewMessage(pattern='/report$'))
async def get_report(event):
    async with memory_lock:
        mem = _load_memory()
        missed = mem.get("missed", [])
    if not missed:
        await event.reply("📭 አዲስ ሪፖርት የለም አለቃዬ።")
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

    if cmd_name in ["start", "resume", "stop", "vips", "vip", "away", "online", "report", "all", "clear_all", "reset_persona"]:
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
            f"❓ **«{cmd_name}»** ገና አልተመዘገበም አለቃዬ!\n"
            f"መጀመሪያ ከሰውየው ጋር በምታወራበት ቻት ላይ `/name {cmd_name}` ብለህ ላክ (እኔ በ0.1 ሰከንድ አጠፋዋለሁ)።"
        )
        return

    person_info = vips.get(target_uid, {})
    display_name = person_info.get("call_name", cmd_name)

    if not role_arg or role_arg not in ROLE_DEFINITIONS:
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
        f"✅ **በትክክል ተሞልቷል አለቃዬ!** 🎉\n"
        f"👤 ስም፦ **{display_name}**\n"
        f"👫 ፆታ፦ **{role_meta['gender']}**\n"
        f"🏷 የጓደኝነት አይነት፦ **{role_meta['title']}**\n"
        f"📝 መመሪያ፦ _{extra_note or 'ሁሌም በስሟ/በስሙ እየጠራሁ እንደ ደረጃው አስተናግዳለሁ!'}_"
    )

# ==========================================
# 6. STEALTH IN-CHAT `/name` LISTENER & BOSS ACTIVITY TRACKER
# ==========================================
@user_client.on(events.NewMessage(outgoing=True))
async def outgoing_and_stealth_name_handler(event):
    global CONTROL_BOT_ID, IS_BOT_SENDING, LAST_BOSS_ACTIVE_TIME
    txt = (event.text or "").strip()

    # ድብቅ `/name zufan` ትዕዛዝ — በ 0.1 ሰከንድ ይጠፋል!
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
                f"🥷 **አሀ! የ«{tg_name}»ን ስም «{custom_name}» ብዬ መዝግቤዋለሁ!**\n"
                f"*(በቻቱ ላይ የጻፍከውን `/name {custom_name}` ሰውየው ሳያየው ወዲያውኑ አጥፍቼዋለሁ!)* ✨\n\n"
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
# 7. LANGUAGE, GENDER & SHIFT-TO-BOSS DETECTOR
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
    "ehte", "fikir", "fkr", "aleka", "alekaye", "tellbirr", "telebirr", "birr", "brr"
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

def needs_shift_to_boss(user_text, has_media):
    """የቴሌብር፣ የገንዘብ፣ የደረሰኝ ፎቶ ወይም አለቃው ብቻ የሚወስነው ጉዳይ መሆኑን ይለያል"""
    if has_media and not user_text.strip():
        return True
    t = normalize_amharic(user_text)
    shift_keywords = [
        "telebirr", "tellbirr", "ቴሌብር", "ቴሌ ብር", "ብር", "birr", "ሂሳብ", "አካውንት", "account",
        "transfer", "ትራንስፈር", "ልኬያለሁ", "ልኬልሃለሁ", "ልክልሀለሁ", "ላክልኝ", "screenshot", "ስክሪን",
        "ደረሰኝ", "ባንክ", "cbe", "ደውልልኝ", "ደውል", "call me", "ሚስጥር", "ብድር", "እዳ"
    ]
    return any(k in t for k in shift_keywords)

# ==========================================
# 8. HIGH-IQ ENGINE WITH NAME-LOCK & SHIFT-TO-BOSS
# ==========================================
BROWSER_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
}

def safe_neural_translate_to_amharic(english_text, call_name):
    """
    የሰውየውን ስም (ለምሳሌ liquid lion ወይም zufan) ወደ 'ፈሳሽ አንበሳ' እንዳይተረጉም
    በ Token ቆልፎ፣ ንጹህ አማርኛ ከተረጎመ በኋላ ስሙንና 'አለቃዬ'ን በትክክል ይመልሳል!
    """
    if not english_text or not english_text.strip():
        return english_text
    try:
        protected = english_text
        if call_name:
            protected = re.sub(re.escape(call_name), "ZXCNAME", protected, flags=re.IGNORECASE)
        protected = re.sub(r'\bmy boss\b|\bthe boss\b|\bboss\b', "ZXCBOSS", protected, flags=re.IGNORECASE)
        protected = re.sub(r'\bAbel\b', "ZXCABEL", protected, flags=re.IGNORECASE)

        url = "https://translate.googleapis.com/translate_a/single"
        params = {"client": "gtx", "sl": "en", "tl": "am", "dt": "t", "q": protected}
        res = requests.get(url, params=params, headers=BROWSER_HEADERS, timeout=10)
        if res.status_code == 200:
            data = res.json()
            am_text = "".join([part[0] for part in data[0] if part[0]])
            am_text = re.sub(r'ZXCNAME', call_name, am_text, flags=re.IGNORECASE)
            am_text = re.sub(r'ZXCBOSS|የእኔ አለቃ|የኔ አለቃ', 'አለቃዬ', am_text, flags=re.IGNORECASE)
            am_text = re.sub(r'ZXCABEL', 'አቤል', am_text, flags=re.IGNORECASE)
            return am_text.strip()
    except Exception:
        pass
    return english_text

def translate_amharic_to_english_for_reasoning(am_text):
    if not am_text or not has_geez(am_text):
        return am_text
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {"client": "gtx", "sl": "am", "tl": "en", "dt": "t", "q": am_text}
        res = requests.get(url, params=params, headers=BROWSER_HEADERS, timeout=8)
        if res.status_code == 200:
            data = res.json()
            return "".join([part[0] for part in data[0] if part[0]]).strip()
    except Exception:
        pass
    return am_text

def instant_precision_brain(user_text, call_name, use_amharic, is_female, role, has_media):
    norm_t = normalize_amharic(user_text)

    # 1. የአለቃህ ስም ማነው? (በዚህ ጥያቄ ብቻ 'ዘላለም' ይላል!)
    if any(k in norm_t for k in ["የአለቃህ ስም", "አለቃህ ማነው", "boss's name", "boss name", "alekah sm", "alekah manew"]):
        if use_amharic:
            return f"ሰላም {call_name}! 😊 የአለቃዬ ስም **ዘላለም** ይባላል ✨። እኔ ደግሞ የአለቃዬ የግል ማኔጀር **አቤል** ነኝ 💼። ምን ልርዳሽ/ህ? 🙏", False
        return f"Hello {call_name}! 😊 My boss's name is **Zelalem** ✨. I am **Abel**, my boss's Personal Manager 💼. How can I help you today? 🙏", False

    # 2. ስምህ ማነው? / አንተ ማነህ? / ስራህ ምንድነው? (ዘላለም ሳይል 'አለቃዬ' ብቻ ይላል!)
    if any(k in norm_t for k in [
        "ስራህ", "ምድነው", "ምንድነው ስራህ", "ስምህ", "ማነህ", "ማነው የሰራህ", "ለምንድነው የተሰራከው",
        "what is your name", "who are you", "what is your job", "what do you do", "srah", "sirah", "maneh"
    ]) and not any(topic in norm_t for topic in ["agriculture", "forex", "physics", "software", "ግብርና", "ፊዚክስ"]):
        if use_amharic:
            if role == "crush":
                return f"ሰላም የኔ ልዩ {call_name}! 😊❤️ ስሜ **አቤል** ይባላል ✨። የአለቃዬ የግል ማኔጀር ነኝ 💼። አለቃዬ ለጊዜው Online ስለሌለ፣ አንቺን በክብርና በፍቅር ለማስተናገድ እዚህ አለሁ 🌸። ምን ልታዘዝሽ? 🥰", False
            if role == "bestie_f":
                return f"ሰላም {call_name}ዬ! 😜✨ ስሜ **አቤል** ይባላል፣ የአለቃዬ የግል ማኔጀር ነኝ 💼። አለቃዬ ስራ ሲበዛበት እሱን ተክቼ ከአንቺ ጋር ለመጫወትና ማንኛውንም ጥያቄ ለመመለስ እዚህ አለሁ 😊🔥", False
            if is_female:
                return f"ሰላም {call_name}! 😊 ስሜ **አቤል** ይባላል ✨። ስራዬ የአለቃዬ የግል ማኔጀር (Personal Manager) መሆን ነው 💼። አለቃዬ Online በማይሆንበት ሰዓት መልዕክቶችን እቀበላለሁ፣ ማንኛውንም ጥያቄም እመልሳለሁ። ምን ልርዳሽ? 🙏", False
            return f"ሰላም {call_name}! 😊 ስሜ **አቤል** ይባላል ✨። ስራዬ የአለቃዬ የግል ማኔጀር (Personal Manager) መሆን ነው 💼። አለቃዬ Online በማይሆንበት ሰዓት መልዕክቶችን እቀበላለሁ፣ ማንኛውንም ጥያቄም እመልሳለሁ። ምን ልርዳህ? 🙏", False
        return f"Hello {call_name}! 😊 My name is **Abel** ✨. I am my boss's Personal Manager, handling his messages and helping with any questions while my boss is away 💼. How can I help you? 🙏", False

    # 3. የቴሌብር / የገንዘብ / የስክሪንሾት ወይም የውሳኔ ጉዳይ -> ወደ አለቃዬ SHIFT ያደርጋል!
    if needs_shift_to_boss(user_text, has_media):
        if use_amharic:
            if is_female:
                return (
                    f"እሺ {call_name}! 😊 የላክሽውን መልዕክትና ጉዳይ በሚገባ ተረድቼዋለሁ ✨። "
                    f"ይህንን ጉዳይ አለቃዬ ራሱ በቀጥታ አይቶ እንዲመልስልሽ ወደ አለቃዬ Shift አድርጌለታለሁ 💼፤ ልክ Online ሲገባ ወዲያውኑ ያነጋግርሻል 🙏🌸",
                    True
                )
            return (
                f"እሺ {call_name}! 😊 የላክከውን መልዕክትና ጉዳይ በሚገባ ተረድቼዋለሁ ✨። "
                f"ይህንን ጉዳይ አለቃዬ ራሱ በቀጥታ አይቶ እንዲጨርሰው ወደ አለቃዬ Shift አድርጌለታለሁ 💼፤ ልክ Online ሲገባ ወዲያውኑ ይመልስልሃል 🙏👊",
                True
            )
        return (
            f"Got it, {call_name}! 😊 I have received your message/details and shifted this directly to my boss so he can handle it personally as soon as he is online 💼✨🙏.",
            True
        )

    # 4. አጭር ሰላምታ (Hi / Hello / ሰላም)
    if norm_t in ["hi", "hello", "hey", "selam", "slm", "ሰላም", "ሃይ", "ሀይ"]:
        if use_amharic:
            if role == "crush":
                return f"ሰላም {call_name}ዬ! 😊❤️ እንኳን ደህና መጣሽ ✨። እኔ አቤል ነኝ፣ የአለቃዬ ማኔጀር 💼። አለቃዬ ለጊዜው Online የለም ግን የአንቺን መልዕክት ሲያይ በጣም ይደሰታል 🌸። እስኪመጣ ድረስ ምን ልርዳሽ? 🥰", False
            if role == "bestie_f":
                return f"ሰላም {call_name}ዬ! 😜✨ እኔ አቤል ነኝ፣ የአለቃዬ ማኔጀር 💼። አለቃዬ ለጊዜው ወጣ ብሏል ግን እኔ አለሁልሽ! ንገሪኝ እስኪ ምን አዲስ ወሬ አለ? 😊🔥", False
            return f"ሰላም {call_name}! 😊 እኔ አቤል ነኝ፣ የአለቃዬ የግል ማኔጀር 💼። አለቃዬ ለጊዜው Online የለም፤ ማንኛውም ጥያቄ ወይም ጉዳይ ካለህ/ሽ ግን እኔን መጠየቅ ትችላለህ/ያለሽ! ✨🙏", False
        return f"Hello {call_name}! 😊 I am Abel, my boss's Personal Manager 💼. My boss is currently away, so feel free to ask me any question or leave a message! ✨🙏", False

    return None, False

def is_valid_reply(ans):
    if not ans or len(ans.strip()) < 2:
        return False
    bad = ["<!doctype", "<html", "rate limit", "too many requests", "502 bad gateway", "internal server error", "cloudflare", "error 52"]
    return not any(b in ans.lower() for b in bad)

def call_all_brain_ai(system_rules, recent_context, user_text, call_name, use_amharic, is_female, role, has_media):
    """
    100% Brain Engine:
    - መጀመሪያ የማንነት ወይም ወደ አለቃው Shift መደረግ ያለባቸውን ጉዳዮች ይፈትሻል።
    - ከዚያ በ OpenAI Level-4 Reasoning በእንግሊዝኛ አስቦ፣ ስሙንና 'አለቃዬ'ን ሳይቀይር ወደ ንጹህ አማርኛ ይተረጉማል!
    - ፈጽሞ መመለስ የማይችለው የግል ጥያቄ ከሆነ '[SHIFT_TO_BOSS]' ብሎ ወደ አንተ ያዞረዋል!
    """
    instant_reply, shifted = instant_precision_brain(user_text, call_name, use_amharic, is_female, role, has_media)
    if instant_reply:
        return instant_reply, shifted

    en_user_text = translate_amharic_to_english_for_reasoning(user_text) if has_geez(user_text) else user_text
    en_context = translate_amharic_to_english_for_reasoning(recent_context) if has_geez(recent_context) else recent_context

    strict_brain_prompt = f"""
{system_rules}

CRITICAL THINKING & ESCALATION RULES:
1. FOCUS ONLY ON THE CURRENT TOPIC: Read [Recent Conversation] and [Latest Message]. Reply ONLY to what the user is actually talking about right now! NEVER bring up random topics like coding, API keys, or forex unless the user explicitly asked about them in the latest message!
2. BOSS REFERENCE: Always refer to your boss ONLY as "my boss". NEVER use his real name unless the user explicitly asks "What is your boss's name?".
3. SHIFT TO BOSS WHEN NEEDED: If the user is asking for a personal decision from your boss, confirming a payment/debt, asking a private personal question only your boss knows, or if you cannot answer the request yourself, start your reply with `[SHIFT]` and politely tell `{call_name}` that you have forwarded/shifted this directly to "my boss" and he will reply as soon as he is online.
4. GENERAL KNOWLEDGE & CHAT: If the user is chatting casually, joking, or asking any knowledge/educational/technical question, answer it brilliantly, clearly, and engagingly!
5. OUTPUT LANGUAGE: Write your response in clear, natural ENGLISH (2 to 3 sentences, with 1-2 fitting emojis at the end of sentences). Keep the user's name as `{call_name}`.
"""

    user_payload = f"[Recent Conversation]\n{en_context}\n\n[Latest Message from {call_name}]: {en_user_text}"

    english_reply = None

    # Engine 1: Fast GET OpenAI Endpoint
    for attempt in range(2):
        try:
            seed_val = random.randint(1000, 999999)
            compact_q = f"{strict_brain_prompt}\n\n{user_payload}\nAbel's Reply:"
            encoded_q = urllib.parse.quote(compact_q[:1350])
            res_get = requests.get(
                f"https://text.pollinations.ai/{encoded_q}?model=openai&seed={seed_val}",
                headers=BROWSER_HEADERS,
                timeout=16
            )
            if res_get.status_code == 200 and is_valid_reply(res_get.text):
                cand = res_get.text.strip()
                if not has_geez(cand):
                    english_reply = cand
                    break
        except Exception:
            pass
        time.sleep(1.0)

    # Engine 2: POST OpenAI Endpoint
    if not english_reply:
        messages = [
            {"role": "system", "content": strict_brain_prompt},
            {"role": "user", "content": user_payload}
        ]
        for model_name in ["openai-large", "openai"]:
            try:
                seed_val = random.randint(1000, 999999)
                res_post = requests.post(
                    "https://text.pollinations.ai/openai",
                    headers=BROWSER_HEADERS,
                    json={"model": model_name, "messages": messages, "seed": seed_val, "temperature": 0.6},
                    timeout=18
                )
                if res_post.status_code == 200:
                    cand = res_post.json()["choices"][0]["message"]["content"].strip()
                    if is_valid_reply(cand):
                        english_reply = cand
                        break
            except Exception:
                pass

    if english_reply:
        is_shifted = "[SHIFT]" in english_reply
        clean_en = english_reply.replace("[SHIFT]", "").strip()
        if use_amharic:
            final_am = safe_neural_translate_to_amharic(clean_en, call_name)
            return final_am, is_shifted
        return clean_en, is_shifted

    # Fallback Shift to Boss (ሁሉም ሰርቨር ቢዘገይ ወደ አለቃዬ Shift ያደርጋል!)
    if use_amharic:
        return (
            f"ሰላም {call_name}! 😊 እኔ አቤል ነኝ፣ የአለቃዬ የግል ማኔጀር 💼። "
            f"የላክከውን/ሽውን መልዕክት በቀጥታ ወደ አለቃዬ Shift አድርጌለታለሁ ✨፤ ልክ Online ሲገባ ወዲያውኑ አይቶ ይመልስልሃል/ሻል 🙏",
            True
        )
    return (
        f"Hello {call_name}! 😊 I am Abel, my boss's Personal Manager 💼. "
        f"I have shifted your message directly to my boss so he can reply to you personally as soon as he is online ✨🙏.",
        True
    )

# ==========================================
# 9. USERBOT CORE (Online/Offline, 2-Min No-Read-Tick & Reaction 🫡 Guard)
# ==========================================
def is_bot_message(msg):
    if not msg:
        return False
    if msg.id in BOT_SENT_IDS:
        return True
    txt = msg.text or ""
    return "From Manager" in txt or "𝙁𝙧𝙤𝙢 𝙈𝙖𝙣𝙖𝙜𝙚𝙧" in txt or "SENT BY AI MANAGER" in txt

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
    """አለቃው መልስ ከሰጠ ወይም React (🫡) ካደረገ True ይመልሳል"""
    if incoming_msg_id in BOSS_REACTED_MSGS:
        return True
    try:
        exact_msg = await user_client.get_messages(chat_id, ids=incoming_msg_id)
        if exact_msg and has_any_reaction(exact_msg):
            BOSS_REACTED_MSGS.add(incoming_msg_id)
            return True

        recent = await user_client.get_messages(chat_id, limit=8)
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

async def is_boss_currently_online(chat_id, current_status):
    """
    አለቃው Online መሆኑን በትክክል ይፈትሻል፦
    1. `/away` ከተባለ Offline ነው።
    2. በዚሁ ቻት ውስጥ ባለፉት 3 ደቂቃዎች (180 ሰከንድ) አለቃው ራሱ መልዕክት ጽፎ ከነበረ Online ነው!
    3. በቴሌግራም Status ላይ Online ከሆነ ወይም ባለፉት 60 ሰከንዶች በማንኛውም ቻት ጽፎ ከነበረ Online ነው!
    """
    if current_status == "away":
        return False
    now = time.time()
    if (now - LAST_BOSS_CHAT_TIME.get(chat_id, 0)) < 180 or (now - LAST_BOSS_ACTIVE_TIME) < 60:
        return True

    try:
        now_utc = datetime.now(timezone.utc)
        rec = await user_client.get_messages(chat_id, limit=6)
        for m in rec:
            if m and m.out and not is_bot_message(m):
                if (now_utc - m.date).total_seconds() < 180:
                    LAST_BOSS_CHAT_TIME[chat_id] = now
                    return True
    except Exception:
        pass

    try:
        me = await user_client.get_me()
        if me and isinstance(getattr(me, 'status', None), UserStatusOnline):
            return True
    except Exception:
        pass
    return False

@user_client.on(events.NewMessage(incoming=True))
async def auto_manager(event):
    global MY_USER_ID, IS_BOT_SENDING
    if not event.is_private:
        return

    # ቦቱ በ /stop ቆሞ ከሆነ ምንም ነገር አያደርግም!
    async with memory_lock:
        mem = _load_memory()
        if not mem.get("bot_active", True):
            return
        if event.id in mem.get("processed_ids", []):
            return
        current_status = mem.get("status", "online")

    sender = await event.get_sender()
    if sender is None or getattr(sender, 'bot', False):
        return

    sender_id = str(sender.id)
    msg_id = event.id
    raw_sender_name = getattr(sender, 'first_name', "ጓደኛ") or "ጓደኛ"
    LATEST_INCOMING_MSG_PER_CHAT[event.chat_id] = msg_id

    # =========================================================
    # ONLINE vs OFFLINE & 2-MINUTE WAIT WITHOUT DOUBLE TICK (✓✓)
    # =========================================================
    boss_online = await is_boss_currently_online(event.chat_id, current_status)

    if boss_online:
        # አለቃው ONLINE ነው!
        # 1. ሁለት ራይት (mark_read) አናደርግም!
        # 2. ለ 2 ደቂቃ (120 ሰከንድ) በየ 4 ሰከንዱ አለቃው መመለሱን ወይም React (🫡) ማድረጉን እንፈትሻለን!
        for _ in range(30):
            await asyncio.sleep(4)
            # አዲስ ተከታታይ መልዕክት ከዚሁ ሰው መጥቶ ከሆነ፣ የመጨረሻውን መልዕክት ብቻ ለመመለስ ይህንን ይተወዋል
            if LATEST_INCOMING_MSG_PER_CHAT.get(event.chat_id) != msg_id:
                return
            if await did_boss_handle_chat(event.chat_id, msg_id):
                async with memory_lock:
                    mem = _load_memory()
                    mem.setdefault("processed_ids", []).append(msg_id)
                    _save_memory(mem)
                return
        # 2 ደቂቃ ሞላና አለቃው አልመለሰም/React አላደረገም -> አሁን ሁለቱን ራይት (✓✓) አብርቶ ይመልሳል!
        try:
            await event.mark_read()
        except Exception:
            pass
    else:
        # አለቃው OFFLINE ነው! በ 3 ሰከንድ ውስጥ ፈትሾ ወዲያውኑ ሁለት ራይት አድርጎ ይመልሳል!
        await asyncio.sleep(3)
        if LATEST_INCOMING_MSG_PER_CHAT.get(event.chat_id) != msg_id:
            return
        if await did_boss_handle_chat(event.chat_id, msg_id):
            return
        try:
            await event.mark_read()
        except Exception:
            pass

    async with memory_lock:
        mem = _load_memory()
        if not mem.get("bot_active", True) or msg_id in mem.get("processed_ids", []):
            return
        persona = mem.get("personality", DEFAULT_PERSONA)
        global_rule = mem.get("global_rule", "")
        mem.setdefault("processed_ids", []).append(msg_id)
        _save_memory(mem)

    has_media = bool(event.media)
    user_text = (event.text or "").strip()
    display_user_text = user_text if user_text else "[ፎቶ/ስክሪንሾት ተልኳል]"
    short_msg = (display_user_text[:45] + '..') if len(display_user_text) > 45 else display_user_text

    # =========================================================
    # 200-MESSAGE DEEP SCAN (ለፆታና ለቅርበት) + CLEAN RECENT CONTEXT (ለወሬው አውድ)
    # =========================================================
    hist_200 = []
    recent_clean_context = ""
    try:
        hist_200 = await user_client.get_messages(event.chat_id, limit=200)
        context_lines = []
        # የቅርብ 12 ንጹህ መልዕክቶችን ለአሁኑ ወሬ ማስቀጠያ እንወስዳለን (የድሮ የተሳሳቱ የቦት መልሶች እንዳያሳስቱት!)
        for m in reversed(hist_200[:15]):
            if not m:
                continue
            txt = (m.text or "").strip()
            if not txt and m.media:
                txt = "[Photo/Screenshot]"
            if not txt:
                continue
            # የድሮ የተበላሹ የቦት መልሶችን ከ Context ውስጥ እናጸዳለን
            if any(bad_old in txt for bad_old in ["AI Network ቆሟል", "ፈሳሽ አንበሳ", "forex vibes", "API key details"]):
                continue
            speaker = "Abel (Manager)" if is_bot_message(m) else ("My Boss" if m.out else raw_sender_name)
            clean_txt = txt.replace("✦ From Manager ✦", "").replace("✦ 𝙁𝙧𝙤𝙢 𝙈𝙖𝙣𝙖𝙜𝙚𝙧 ✦", "").strip()
            if clean_txt:
                context_lines.append(f"{speaker}: {clean_txt}")
        recent_clean_context = "\n".join(context_lines[-12:])
    except Exception:
        pass

    async with memory_lock:
        mem = _load_memory()
        vips = mem.get("vips", {})
        vip_info = vips.get(sender_id)

        detected_gender, closeness_level = detect_gender_and_profile(raw_sender_name, hist_200, vip_info)

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
        relationship_guide = f"Sender's name: '{call_name}'. Gender: {detected_gender}. Relationship: {closeness_level}. Be charismatic, smart, polite, and engaging."

    use_amharic = is_amharic_intent(display_user_text) or (not any(c.isalpha() for c in user_text) and has_geez(recent_clean_context))

    system_rules = f"""
{persona}
{f"GLOBAL RULE: {global_rule}" if global_rule else ""}

CONTEXT & RELATIONSHIP:
- Your Name: Abel (Personal Manager of your boss).
- Sender's Name: "{call_name}" (Gender: {detected_gender}).
- {relationship_guide}
"""

    signature_html = "\n\n<blockquote>✦ <b><i>From Manager</i></b> ✦</blockquote>"

    async with ai_request_lock:
        # AIው ከመመለሱ በፊት አለቃው መልሶ ወይም React (🫡) አድርጎ እንደሆነ ለመጨረሻ ጊዜ ይፈትሻል!
        if await did_boss_handle_chat(event.chat_id, msg_id):
            return

        IS_BOT_SENDING = True
        try:
            response_text, is_shifted = await asyncio.to_thread(
                call_all_brain_ai,
                system_rules, recent_clean_context, display_user_text, call_name, use_amharic, is_female, role, has_media
            )

            # AIው እያሰበ በነበረበት 2 ሰከንድ ውስጥ አለቃው መልሶ ከሆነ አይልክም!
            if await did_boss_handle_chat(event.chat_id, msg_id):
                return

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
                if is_shifted:
                    await bot_client.send_message(
                        MY_USER_ID,
                        f"🚨 **ወደ አለቃዬ የተዞረ ጉዳይ (SHIFT ALERT)!**\n"
                        f"👤 ከ: **{call_name}** ({detected_gender})\n"
                        f"💬 ጉዳይ: `{short_msg}`\n"
                        f"🤵‍♂️ አቤል የሰጠው ምላሽ: _{clean_reply}_"
                    )
                else:
                    await bot_client.send_message(
                        MY_USER_ID,
                        f"🔔 **አቤል ምላሽ ሰጥቷል!**\n👤 ለ: **{call_name}** ({detected_gender})\n💬: {short_msg}\n🤵‍♂️: {clean_reply[:140]}..."
                    )
            except Exception:
                pass
        finally:
            await asyncio.sleep(1)
            IS_BOT_SENDING = False

# ==========================================
# 10. Connection Keep-Alive (አካውንትህን በግድ Online ሳያደርግ!)
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
            "🚀 **ሰላም አለቃዬ! አቤል (100% Full-Brain Final Edition) ተነስቷል!** 💼✨\n\n"
            "✅ **የተካተቱ ህጎች፦**\n"
            "• በመደበኛ ወሬ **«አለቃዬ / my boss»** ብቻ ይላል (ስምህ ማነው ሲባል ብቻ ዘላለም ይላል)።\n"
            "• የቴሌብር/የገንዘብ ወይም ከአቅሙ በላይ የሆነ ጥያቄ ሲመጣ ወደ **አለቃዬ Shift** ያደርጋል!\n"
            "• Online ስትሆን ሁለት ራይት (`✓✓`) ሳያደርግ 2 ደቂቃ ይጠብقሃል፤ React (`🫡`) ካደረግክ ወይም ከመለስክ 100% ዝም ይላል!\n"
            "• በቻት ላይ `/name zufan` ስትል በ0.1 ሰከንድ አጥፍቶ ስሙን ይይዛል፤ እዚህ `/zufan` ስትል የጓደኝነት አይነቱን ያስመርጥሃል።\n"
            "• ለማቆም **`/stop`**፣ ለማስነሳት **`/start`** በለው!"
        )
    except Exception:
        pass

    async def run_until_disconnected_safe():
        while True:
            try:
                await asyncio.gather(
                    user_client.run_until_disconnected(),
                    bot_client.run_until_disconnected()
                )
            except Exception as e:
                print(f"Client disconnected error: {e}, restarting in 5s...")
                await asyncio.sleep(5)

    await run_until_disconnected_safe()

if __name__ == '__main__':
    loop.run_until_complete(main())
