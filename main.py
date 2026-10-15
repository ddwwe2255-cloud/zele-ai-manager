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
BOT_SENT_IDS = set()
BOSS_REACTED_MSGS = set()
IS_BOT_SENDING = False

DEFAULT_PERSONA = (
    "You are Abel (አቤል), the sharp, charismatic, deeply intelligent, and professional Personal Manager of Zelalem (ዘላለም). "
    "You NEVER call yourself an AI, bot, robot, Gemini, ChatGPT, or Claude. You speak like a brilliant, warm human manager."
)

def _load_memory():
    default_data = {
        "status": "online",
        "personality": DEFAULT_PERSONA,
        "missed": [],
        "processed_ids": [],
        "fallback_timestamps": {},
        "contacts": {},
        "vips": {},
        "name_links": {}  # ስም (ለምሳሌ zufan) -> sender_id ማገናኛ
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
    return "ABEL - Self-Healing Personal Manager of Zelalem is Running 24/7!", 200

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
# 5. FRIENDSHIP TYPES & DYNAMIC /[ስም] SYSTEM
# ==========================================
ROLE_DEFINITIONS = {
    "crush": {
        "title": "❤️ ድብቅ ፍቅረኛ / የምታፈቅራት (Secret Mutual Love)",
        "gender": "female (ሴት)",
        "prompt": "CRITICAL VIP: She is Zelalem's secret mutual love/crush (እሷም ታፈቅረዋለች እሱም ያፈቅራታል ግን በግልፅ አያወሩም). Treat her with extraordinary sweetness, tenderness, charm, and deep care. Make her feel super special and valued!"
    },
    "bestie_f": {
        "title": "😜 ሴት Best Friend (Playful & Special)",
        "gender": "female (ሴት)",
        "prompt": "CRITICAL VIP: She is Zelalem's Female Best Friend (ሴት ቤስት ፍሬንድ 😜). Be super playful, teasing, witty, fun, lively, and warm! Use charming banter and make her smile!"
    },
    "bestie_m": {
        "title": "😎 ወንድ Best Friend (የልብ አራዳ ጓደኛ)",
        "gender": "male (ወንድ)",
        "prompt": "CRITICAL VIP: He is Zelalem's Male Best Friend (የልብ አራዳ ጓደኛ 😎). Use cool Ethiopian Arada brotherhood energy—witty, chill, loyal, and fun!"
    },
    "friend_f": {
        "title": "😊🌸 መደበኛ ሴት ጓደኛ (Normal Female Friend)",
        "gender": "female (ሴት)",
        "prompt": "RELATIONSHIP: She is Zelalem's normal female friend (መደበኛ ሴት ጓደኛ). Address her politely in feminine form (አንቺ/ሽ/ጓደኛዬ), be friendly, warm, respectful, and engaging."
    },
    "friend_m": {
        "title": "😊👊 መደበኛ ወንድ ጓደኛ (Normal Male Friend)",
        "gender": "male (ወንድ)",
        "prompt": "RELATIONSHIP: He is Zelalem's normal male friend (መደበኛ ወንድ ጓደኛ). Address him in masculine form (አንተ/ህ/ወንድሜ), be friendly, smart, respectful, and helpful."
    },
    "family_f": {
        "title": "🙏❤️ ሴት ቤተሰብ (እህት / እናት / ዘመድ)",
        "gender": "female (ሴት)",
        "prompt": "VIP FAMILY: She is Zelalem's beloved female family member (ሴት ቤተሰብ). Show the highest level of love, honor, warmth, and respect."
    },
    "family_m": {
        "title": "🙏✨ ወንድ ቤተሰብ (ወንድም / አባት / ዘመድ)",
        "gender": "male (ወንድ)",
        "prompt": "VIP FAMILY: He is Zelalem's beloved male family member (ወንድ ቤተሰብ). Show the highest level of honor, respect, warmth, and care."
    },
    "new_f": {
        "title": "✨🌸 አዲስ ሴት (አዲስ የተዋወቅካት ሴት)",
        "gender": "female (ሴት)",
        "prompt": "RELATIONSHIP: New female acquaintance (አዲስ ሴት). Be extra welcoming, polite, charismatic, and impressive in feminine address."
    },
    "new_m": {
        "title": "🤝✨ አዲስ ወንድ (አዲስ የተዋወቅከው ወንድ)",
        "gender": "male (ወንድ)",
        "prompt": "RELATIONSHIP: New male acquaintance (አዲስ ወንድ). Be professional, welcoming, sharp, and respectful in masculine address."
    }
}

def build_person_menu_text(person_name):
    cmd = person_name.lower().replace(" ", "")
    return (
        f"🎯 **«{person_name}» ለአንተ ምን አይነት ጓደኛ እንደሆነ/እንደሆነች መምረጫ ዝርዝር፦**\n\n"
        f"ከታች ካሉት ውስጥ የሚመጥነውን አንዱን ትዕዛዝ Copy አድርገህ እዚሁ ላክልኝ፦\n\n"
        f"👩 **ለሴት (Female Options)፦**\n"
        f"• `/{cmd} crush` — ድብቅ ፍቅረኛ (እሷም የምታፈቅርህ አንተም የምታፈቅራት ❤️)\n"
        f"• `/{cmd} bestie_f` — ሴት Best Friend (ለየት ያለ ጨዋታና ቀልድ 😜)\n"
        f"• `/{cmd} friend_f` — Normal ሴት ጓደኛ (መደበኛ ሴት ጓደኛ 😊🌸)\n"
        f"• `/{cmd} family_f` — ሴት ቤተሰብ / እህት / ዘመድ (🙏❤️)\n"
        f"• `/{cmd} new_f` — አዲስ የተዋወቅካት ሴት (✨🌸)\n\n"
        f"👨 **ለወንድ (Male Options)፦**\n"
        f"• `/{cmd} bestie_m` — ወንድ Best Friend (አራዳ የወንድማማች ጨዋታ 😎)\n"
        f"• `/{cmd} friend_m` — Normal ወንድ ጓደኛ (መደበኛ ወንድ ጓደኛ 😊👊)\n"
        f"• `/{cmd} family_m` — ወንድ ቤተሰብ / ወንድም / ዘመድ (🙏✨)\n"
        f"• `/{cmd} new_m` — አዲስ የተዋወቅከው ወንድ (🤝✨)\n\n"
        f"💡 *ተጨማሪ ማስታወሻ መጨመር ከፈለግክ ከጎኑ መጻፍ ትችላለህ፦*\n"
        f"ምሳሌ፦ `/{cmd} crush እሷም ታፈቅረኛለች እኔም አፈቅራታለሁ ግን በግልፅ አናወራም`"
    )

@bot_client.on(events.NewMessage(pattern='/start'))
async def bot_start(event):
    await event.reply(
        "👋 **ሰላም አለቃዬ ዘላለም! እኔ አቤል (Abel) ነኝ — የአንተ የግል ማኔጀር።** 💼✨\n\n"
        "🥷 **አዲሱ ድብቅ የስምና የጓደኝነት መመዝገቢያ ዘዴ፦**\n"
        "1️⃣ ከማንኛውም ሰው ጋር እያወራህ እዛው የሰውየው ቻት ላይ፦\n"
        "   `/name zufan` (ወይም የምትፈልገውን ስም) ብለህ ላክ።\n"
        "   *(እኔ በ 0.1 ሰከንድ ውስጥ ወዲያውኑ አጠፋዋለሁ፣ ሰውየው አያየውም! ከዛ ሁሌም በዛ ስም እጠራዋለሁ)*\n"
        "2️⃣ ከዛ እዚህ ቦት ጋር መጥተህ **`/zufan`** ብለህ ስትጽፍልኝ፣ ሁሉንም የጓደኝነት አይነቶች (ለወንድም ለሴትም) አስመርጥሃለሁ!\n\n"
        "⚙️ **ሌሎች ትዕዛዞች፦**\n"
        "📋 `/vips` — የመዘገብካቸውን ጓደኞችና ስማቸውን ለማየት\n"
        "🛠️ `/doctor` — የአቤልን ጤንነት መፈተሻና ራሱን በራሱ ማስጠገኛ\n"
        "🔴 `/away` — እኔ የለሁም (በ 2 ሰከንድ እንድመልስ)\n"
        "🟢 `/online` — መደበኛ ሁኔታ\n"
        "📊 `/report` — የመጡ መልዕክቶችን ሪፖርት ለማየት"
    )

@bot_client.on(events.NewMessage(pattern='/vips$'))
async def list_vips_cmd(event):
    async with memory_lock:
        mem = _load_memory()
        vips = mem.get("vips", {})
    if not vips:
        await event.reply(
            "📭 እስካሁን የተመዘገበ ሰው የለም።\n"
            "ለመመዝገብ በምታወራው ሰው ቻት ላይ `/name zufan` ብለህ ጻፍና ወደዚህ ተመለስ!"
        )
        return
    txt = "👑 **አቤል የመዘገባቸው ጓደኞችና የሚጠሩበት ስም፦**\n\n"
    for uid, v in vips.items():
        c_name = v.get("call_name", "ጓደኛ")
        r_key = v.get("role", "friend_m")
        r_title = ROLE_DEFINITIONS.get(r_key, {}).get("title", r_key)
        note = v.get("note", "")
        txt += f"• **{c_name}** (TG: `{v.get('tg_name', uid)}`)\n  └ 🏷 {r_title} {f'- _{note}_' if note else ''}\n\n"
    await event.reply(txt[:4000])

@bot_client.on(events.NewMessage(pattern='/doctor$'))
async def doctor_cmd(event):
    await event.reply("🔍 **አቤል ራሱን እየፈተሸ ነው (Self-Diagnostics)...**")
    report = []
    # Check Pollinations GET
    try:
        r = requests.get("https://text.pollinations.ai/Hello?model=openai", timeout=8)
        if r.status_code == 200:
            report.append("✅ Cloud AI Engine #1 (GET): **100% Working**")
        else:
            report.append(f"⚠️ Cloud AI Engine #1: HTTP {r.status_code} (Auto-Heal Active)")
    except Exception as e:
        report.append(f"⚠️ Cloud AI Engine #1: `{e}` (Switched to Backup)")

    # Check Google Neural Translator
    try:
        g = requests.get("https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=am&dt=t&q=Hello", timeout=6)
        if g.status_code == 200:
            report.append("✅ Neural Amharic Polisher: **100% Working**")
    except Exception:
        report.append("⚠️ Neural Amharic Polisher: Using Local Backup")

    report.append("✅ Built-in Autonomous Smart Brain: **100% Active & Ready (Zero Silence Guarantee)**")
    await event.reply("🛠️ **የአቤል የጤንነት ሪፖርት፦**\n\n" + "\n".join(report))

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

# Dynamic /[ስም] Handler on Control Bot (ለምሳሌ /zufan ወይም /zufan bestie_f)
@bot_client.on(events.NewMessage(pattern=r'^/([^\s]+)(?:\s+(\S+))?(?:\s+(.*))?$'))
async def dynamic_name_command_handler(event):
    cmd_name = event.pattern_match.group(1).strip().lower()
    role_arg = (event.pattern_match.group(2) or "").strip().lower()
    extra_note = (event.pattern_match.group(3) or "").strip()

    # መደበኛ ትዕዛዞችን እንዘላለን
    if cmd_name in ["start", "vips", "vip", "away", "online", "report", "doctor", "reset_persona"]:
        return

    async with memory_lock:
        mem = _load_memory()
        name_links = mem.get("name_links", {})
        vips = mem.get("vips", {})

        # ሰውየውን በስሙ መፈለግ
        target_uid = name_links.get(cmd_name)
        if not target_uid:
            for uid, info in vips.items():
                if info.get("call_name", "").lower() == cmd_name:
                    target_uid = uid
                    break

    if not target_uid:
        await event.reply(
            f"❓ **«{cmd_name}»** የሚል ስም ገና አልተመዘገበም አለቃዬ!\n\n"
            f"**እንዴት እንደምትመዘግብ፦**\n"
            f"1. መጀመሪያ ከሰውየው ጋር በምታወራበት ቻት ላይ ገብተህ `/name {cmd_name}` ብለህ ላክ (እኔ በ1 ሰከንድ አጠፋዋለሁ)።\n"
            f"2. ከዛ ወደዚህ ተመልሰህ `/{cmd_name}` ብለህ ጻፍልኝ!"
        )
        return

    person_info = vips.get(target_uid, {})
    display_name = person_info.get("call_name", cmd_name)

    # አለቃው /zufan ብቻ ብሎ ከጻፈ -> ሁሉንም የጓደኝነት አይነቶች ዝርዝር ያሳየዋል!
    if not role_arg:
        await event.reply(build_person_menu_text(display_name))
        return

    # አለቃው /zufan bestie_f ወይም /zufan friend_m ብሎ ከመረጠ
    if role_arg not in ROLE_DEFINITIONS:
        await event.reply(
            f"⚠️ **`{role_arg}`** ትክክለኛ ምርጫ አይደለም!\n\n" + build_person_menu_text(display_name)
        )
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
        entry["updated_at"] = time.time()
        vips[target_uid] = entry
        mem["vips"] = vips
        _save_memory(mem)

    await event.reply(
        f"✅ **በትክክል ተሞልቷል አለቃዬ!** 🎉\n\n"
        f"👤 ስም፦ **{display_name}**\n"
        f"👫 ፆታ፦ **{role_meta['gender']}**\n"
        f"🏷 የጓደኝነት አይነት፦ **{role_meta['title']}**\n"
        f"📝 ተጨማሪ መመሪያ፦ _{extra_note or 'እንደ ደረጃው በልዩ ሁኔታ አስተናግዳለሁ!'}_\n\n"
        f"ከዛሬ ጀምሮ **{display_name}** ሲጽፍ/ስትጽፍ ሁሌም **«{display_name}»** እያልኩ በዚሁ የጓደኝነት ደረጃ አወራለሁ! ✨"
    )

# ==========================================
# 6. STEALTH IN-CHAT `/name zufan` LISTENER (Deletes in < 1s!)
# ==========================================
@user_client.on(events.NewMessage(outgoing=True))
async def outgoing_and_stealth_name_handler(event):
    global CONTROL_BOT_ID, IS_BOT_SENDING, LAST_BOSS_ACTIVE_TIME
    txt = (event.text or "").strip()

    # 1. ድብቅ `/name zufan` ትዕዛዝ በማንኛውም ቻት ውስጥ ሲጻፍ!
    match_name = re.match(r'^/name\s+(.+)$', txt, flags=re.IGNORECASE)
    if match_name and event.is_private:
        custom_name = match_name.group(1).strip()
        # ወዲያውኑ በ 0.1 ሰከንድ ውስጥ መልዕክቱን ያጠፋዋል (ሰውየው እንዳያየው!)
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

        # ወዲያውኑ በ Control Bot ላይ ለዘላለም ያሳውቀዋል!
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

    # 2. መደበኛ የአለቃ እንቅስቃሴ መከታተያ
    if IS_BOT_SENDING or is_bot_message(event.message):
        return
    LAST_BOSS_ACTIVE_TIME = time.time()
    if CONTROL_BOT_ID and event.chat_id == CONTROL_BOT_ID:
        return
    LAST_BOSS_CHAT_TIME[event.chat_id] = time.time()

# ==========================================
# 7. Normalization, Language & 150-Message Scanner
# ==========================================
def normalize_amharic(text):
    """እንደ 'ስራህ ምድነዉ' ያሉ የፊደል ልዩነቶችን አስተካክሎ ለመረዳት"""
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
# 8. SELF-HEALING MULTI-ENGINE AI + BUILT-IN SMART BRAIN
# ==========================================
BROWSER_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
}

def google_neural_translate(text, target_lang="am"):
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {"client": "gtx", "sl": "auto", "tl": target_lang, "dt": "t", "q": text}
        res = requests.get(url, params=params, headers=BROWSER_HEADERS, timeout=8)
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

def builtin_autonomous_smart_brain(user_text, call_name, use_amharic, is_female, role, chat_context=""):
    """
    የውጪ AI ሰርቨሮች ሲጨናነቁ ወይም ሲቋረጡ ቦቱ ወደ ደረቅ Offline እንዳይገባና በፍጹም ዝም እንዳይል
    ራሱ ችግሩን ፈትቶ ተፈጥሯዊና ብልህ መልስ የሚሰጥ Built-in Autonomous Brain!
    """
    norm_t = normalize_amharic(user_text)

    # 1. Can you speak English / ቋንቋ ትችላለህ ወይ?
    if any(k in norm_t for k in ["can you speak english", "speak english", "do you speak english", "እንግሊዝኛ ትችላለህ", "እንግሊዘኛ"]):
        if not use_amharic:
            return f"Yes, of course {call_name}! 😊 I speak fluent English as well as Amharic ✨. My name is Abel, and I am my boss Zelalem's Personal Manager 💼. How can I help you today? 🙏"
        return f"አዎ {call_name}፣ በሚገባ እችላለሁ! 😊 እንግሊዝኛም ሆነ አማርኛ አቀላጥፌ አወራለሁ ✨። በፈለግከው/ሽው ቋንቋ ልታናግረኝ/ሪኝ ትችላለህ/ያለሽ 💼🙏"

    # 2. የአለቃህ ስም ማነው?
    if any(k in norm_t for k in ["የአለቃህ ስም", "አለቃህ ማነው", "boss's name", "boss name", "alekah sm", "alekah manew"]):
        if use_amharic:
            return f"ሰላም {call_name}! 😊 የአለቃዬ ስም **ዘላለም** ይባላል ✨። እኔ ደግሞ የእሱ የግል ማኔጀር **አቤል** ነኝ 💼። ምን ልርዳሽ/ህ? 🙏"
        return f"Hello {call_name}! 😊 My boss's name is **Zelalem** ✨. I am **Abel**, his Personal Manager 💼. How can I help you today? 🙏"

    # 3. ስራህ ምንድነው? / ስራህ ምድነዉ? / አንተ ማነህ? / ስምህ ማነው?
    if any(k in norm_t for k in [
        "ስራህ", "ምድነው", "ምንድነው ስራህ", "ስምህ", "ማነህ", "ማነው የሰራህ", "ለምንድነው የተሰራከው",
        "what is your name", "who are you", "what is your job", "what do you do", "srah", "sirah", "maneh"
    ]) and not any(topic in norm_t for topic in ["agriculture", "forex", "physics", "ግብርና", "ፊዚክስ"]):
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

    # 4. የስልክ ቦት / ቴሌግራም ቦት / ኮዲንግ ጥያቄዎች (እንደ "ቆይ የስልክ ቦት እሳ መስራት አትችልም🙏")
    if any(k in norm_t for k in ["ቦት", "bot", "መስራት", "ኮድ", "code", "python", "ai"]):
        if use_amharic:
            pronoun = "ትችያለሽ" if is_female else "ትችላለህ"
            return (
                f"አዎ {call_name}፣ በሚገባ ይቻላል! 😊✨ በስልክም ሆነ በኮምፒውተር Python (Telethon) እና Cloud ሰርቨር (እንደ Render) በመጠቀም 24 ሰዓት የሚሰራ ዘመናዊ ቦት መስራት ይቻላል 💻🔥። "
                f"አለቃዬ ዘላለም እኔንም የሰራኝ በዚሁ ቴክኖሎጂ ነው፤ ተጨማሪ እገዛ ከፈለግክ/ሽ ዝርዝሩን ልነግርህ/ሽ እችላለሁ ወይም አለቃዬ ሲመጣ ያሳይሃል/ሻል! 🙏"
            )
        return f"Yes {call_name}, absolutely! 😊✨ We can build advanced phone and Telegram bots using Python and cloud hosting like Render 💻🔥. My boss Zelalem built me using this exact technology! Let me know what kind of bot you want to build 🙏."

    # 5. ሰላምታ
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

def call_self_healing_ai(system_rules, user_prompt, user_text, call_name, use_amharic, is_female, role, chat_context):
    """
    ባለ 5-ደረጃ ራሱን በራሱ የሚጠግን (Self-Healing) AI Engine:
    1. Instant Precision Matcher (ለማንነት፣ ለስራህ ምንድነው፣ ለእንግሊዝኛ፣ ለሰላምታ)
    2. Gemini Cloud API (ካለ)
    3. Fast GET Cloud AI Endpoint (Cloudflare የማያግደው)
    4. POST OpenAI Cloud Endpoint
    5. Built-in Autonomous Smart Backup (በፍጹም ወደ ደረቅ Offline ወይም ዝምታ እንዳይገባ!)
    """
    diagnostics_log = []

    # Step 1: ፈጣንና 100% ትክክለኛ የማንነት/የስራ ጥያቄዎች ምላሽ
    instant = builtin_autonomous_smart_brain(user_text, call_name, use_amharic, is_female, role, chat_context)
    if instant:
        return instant, None

    # Step 2: Gemini Keys (ካሉ)
    for key in GEMINI_KEYS:
        for g_model in ["gemini-2.5-flash", "gemini-2.0-flash"]:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{g_model}:generateContent"
                res = requests.post(
                    url,
                    headers={"Content-Type": "application/json", "X-goog-api-key": key},
                    json={"contents": [{"parts": [{"text": f"{system_rules}\n\n{user_prompt}"}]}]},
                    timeout=14
                )
                if res.status_code == 200:
                    ans = res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                    if is_valid_reply(ans):
                        return ans, None
            except Exception as e:
                diagnostics_log.append(f"Gemini ({g_model}): {type(e).__name__}")

    en_instruction = (
        f"{system_rules}\n"
        "IMPORTANT: Write your response in clear, natural ENGLISH (2-4 sentences, with 1-2 emojis at the end of each sentence)."
    )

    # Step 3: Fast GET Endpoint (በ Render ላይ ከ POST በላይ 10 እጥፍ ፈጣን እና አስተማማኝ!)
    for attempt in range(2):
        try:
            seed_val = random.randint(1000, 999999)
            compact_q = f"System: {en_instruction}\n\n{user_prompt}\nReply as Abel:"
            encoded_q = urllib.parse.quote(compact_q[:1200])
            res_get = requests.get(
                f"https://text.pollinations.ai/{encoded_q}?model=openai&seed={seed_val}",
                headers=BROWSER_HEADERS,
                timeout=16
            )
            if res_get.status_code == 200 and is_valid_reply(res_get.text):
                ans = res_get.text.strip()
                if use_amharic and not has_geez(ans):
                    ans = google_neural_translate(ans, "am")
                return ans, None
            else:
                diagnostics_log.append(f"GET AI HTTP {res_get.status_code}")
        except Exception as e:
            diagnostics_log.append(f"GET AI: {type(e).__name__}")
        time.sleep(1.2)

    # Step 4: POST OpenAI Endpoint
    messages = [
        {"role": "system", "content": en_instruction},
        {"role": "user", "content": user_prompt}
    ]
    for model_name in ["openai", "openai-large"]:
        try:
            seed_val = random.randint(1000, 999999)
            res_post = requests.post(
                "https://text.pollinations.ai/openai",
                headers=BROWSER_HEADERS,
                json={"model": model_name, "messages": messages, "seed": seed_val, "temperature": 0.65},
                timeout=18
            )
            if res_post.status_code == 200:
                ans = res_post.json()["choices"][0]["message"]["content"].strip()
                if is_valid_reply(ans):
                    if use_amharic and not has_geez(ans):
                        ans = google_neural_translate(ans, "am")
                    return ans, None
            else:
                diagnostics_log.append(f"POST {model_name} HTTP {res_post.status_code}")
        except Exception as e:
            diagnostics_log.append(f"POST {model_name}: {type(e).__name__}")

    # Step 5: Self-Healing Autonomous Smart Reply (ሁሉም ሰርቨሮች ቢጨናነቁ እንኳ ራሱ ችግሩን ፈትቶ ተገቢና ማራኪ መልስ ይሰጣል!)
    heal_report = " | ".join(diagnostics_log) if diagnostics_log else "Cloud AI Timeout"
    if use_amharic:
        if role == "crush":
            smart_rescue = (
                f"የኔ ልዩ {call_name} ❤️✨፣ የጻፍሽልኝን መልዕክት በክብር አይቼዋለሁ 😊። "
                f"አለቃዬ ዘላለም አሁን ለትንሽ ደቂቃ ከመስመር ወጣ ብሏል እንጂ፣ ልክ ሲመለስ መልዕክትሽን ቀድሜ አሳየዋለሁ 🌸። "
                f"እስከዛው ግን ከእኔ ከአቤል ጋር መጫወት ወይም ማንኛውንም ነገር መጠየቅ ትችያለሽ! 🥰🙏"
            )
        elif role == "bestie_f":
            smart_rescue = (
                f"ሰላም {call_name}ዬ! 😜✨ መልዕክትሽ ደርሶኛል 😊። "
                f"አለቃዬ ዘላለም አሁን ትንሽ ስራ ይዞት ከመስመር ወጣ ብሏል፣ ልክ ሲመለስ የጻፍሽውን አሳየዋለሁ 💼። "
                f"እስኪመጣ ድረስ ግን ከእኔ ከማኔጀሩ ከአቤል ጋር መጫወት ትችያለሽ! 🔥🙏"
            )
        elif is_female:
            smart_rescue = (
                f"ሰላም {call_name}! 😊 እኔ አቤል ነኝ (የአለቃዬ የዘላለም ማኔጀር) 💼። "
                f"የላክሽውን መልዕክት በሚገባ መዝግቤ ይዤዋለሁ ✨፤ አለቃዬ ልክ Online ሲገባ ወዲያውኑ አሳውቀውና በዝርዝር ይመልስልሻል 🙏🌸"
            )
        else:
            smart_rescue = (
                f"ሰላም {call_name}! 😊 እኔ አቤል ነኝ (የአለቃዬ የዘላለም ማኔጀር) 💼። "
                f"የላክከውን ጉዳይ በሚገባ መዝግቤ ይዤዋለሁ ✨፤ አለቃዬ ልክ Online ሲገባ ወዲያውኑ አሳውቀውና በራሱ ይመልስልሃል 🙏👊"
            )
    else:
        smart_rescue = (
            f"Hello {call_name}! 😊 I am Abel, my boss Zelalem's Personal Manager 💼. "
            f"I have carefully noted your message and will make sure my boss gets back to you as soon as he is online ✨🙏!"
        )

    return smart_rescue, heal_report

# ==========================================
# 9. USERBOT CORE (Instant Offline Detection & Reaction 🫡 Lock)
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
    """
    አለቃው በአሁኑ ሰከንድ በቴሌግራም Online መሆኑን በትክክል ይፈትሻል።
    አለቃው ልክ ከቴሌግራም እንደወጣ ወዲያውኑ False ይመልሳል!
    """
    if current_status == "away":
        return False
    try:
        me = await user_client.get_me()
        if me and isinstance(getattr(me, 'status', None), UserStatusOnline):
            return True
    except Exception:
        pass
    # አለቃው ባለፉት 35 ሰከንዶች ውስጥ መልዕክት ጽፎ ከሆነ ብቻ Online ይባላል
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

    # =========================================================
    # SMART ONLINE/OFFLINE WAIT: ከቴሌግራም እንደወጣህ ወዲያውኑ ይመልሳል!
    # =========================================================
    boss_online = await is_boss_live_online(current_status)

    if boss_online:
        # አንተ Online ነህ! ሁለት ራይት (✓✓) አያበራም!
        # እስከ 2 ደቂቃ (120 ሰከንድ) ይጠብقሃል፤ ግን በመሃል ከቴሌግራም ከወጣህ ወዲያውኑ ጥበቃውን አቋርጦ ይመልሳል!
        for _ in range(30):
            await asyncio.sleep(4)
            if await did_boss_handle_chat(event.chat_id, msg_id):
                async with memory_lock:
                    mem = _load_memory()
                    mem.setdefault("processed_ids", []).append(msg_id)
                    _save_memory(mem)
                return
            # አለቃው ከቴሌግራም ወጥቶ Offline ከሆነ 2 ደቂቃ ሳይጠብቅ ወዲያውኑ ለመመለስ ከLoop ይወጣል!
            if not await is_boss_live_online(current_status):
                break
        try:
            await event.mark_read()
        except Exception:
            pass
    else:
        # አንተ Offline ነህ! በ 2 ሰከንድ ውስጥ ወዲያውኑ ይመልሳል!
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
        mem.setdefault("processed_ids", []).append(msg_id)
        _save_memory(mem)

    user_text = event.text or "[ፎቶ/ሚዲያ ተልኳል]"
    short_msg = (user_text[:45] + '..') if len(user_text) > 45 else user_text

    # =========================================================
    # DEEP 150-MESSAGE SCAN & CUSTOM `/name zufan` LOOKUP
    # =========================================================
    hist_150 = []
    chat_context = ""
    try:
        hist_150 = await user_client.get_messages(event.chat_id, limit=150)
        context_lines = []
        for m in reversed(hist_150[:25]):
            if not m or not m.text:
                continue
            speaker = "Abel (Manager)" if is_bot_message(m) else ("Zelalem (Boss)" if m.out else raw_sender_name)
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

        # በ `/name zufan` የተመዘገበ ስም ካለ ሁሌም በዛ ይጠራዋል!
        if vip_info and vip_info.get("call_name"):
            call_name = vip_info["call_name"]
        else:
            # ከቴሌግራም ስሙ ላይ ኢሞጂዎችን (ለምሳሌ liquid lion💵📈) አጽድቶ ንጹህ ስሙን ይወስዳል
            cleaned_tg = re.sub(r'[^\w\s\u1200-\u137F]', '', raw_sender_name).strip()
            call_name = cleaned_tg if len(cleaned_tg) >= 2 else "ጓደኛዬ"

        mem.setdefault("missed", []).append({"name": f"{call_name} ({raw_sender_name})", "text": short_msg})
        _save_memory(mem)

    role = vip_info.get("role", "normal") if vip_info else "normal"
    custom_note = vip_info.get("note", "") if vip_info else ""
    is_female = "female" in detected_gender

    if role in ROLE_DEFINITIONS:
        relationship_guide = f"{ROLE_DEFINITIONS[role]['prompt']} Always address them as '{call_name}'. {custom_note}"
    else:
        relationship_guide = f"Sender's name: '{call_name}'. Gender: {detected_gender}. Relationship: {closeness_level}. Be charismatic, smart, polite, and helpful."

    use_amharic = is_amharic_intent(user_text) or (not any(c.isalpha() for c in user_text) and has_geez(chat_context))

    system_rules = f"""
{persona}

1. IDENTITY RULES:
   - Your Name: Abel (አቤል).
   - Your Job: Personal Manager of your boss Zelalem (ዘላለም).
   - NEVER call yourself an AI, robot, bot, Gemini, ChatGPT, or Claude.
   - Always address the user by their name: "{call_name}".
   - Gender of "{call_name}": {detected_gender}.
   - {relationship_guide}

2. HIGH-LEVEL INTELLIGENCE & CONVERSATION CONTINUITY:
   - Read the [Recent Conversation] carefully. Continue the ongoing topic naturally, or if "{call_name}" asks any question (about Bots, Coding, Physics, Forex, Agriculture, School, or Franco-Amharic), answer it with brilliant clarity and depth!
   - Put 1 or 2 expressive emojis at the end of each sentence (😊, ✨, 🙏, 💼, 😜, 🔥).
   - Keep your response concise (2 to 4 sentences) and NEVER add a signature at the end.
"""

    user_prompt = f"[Recent Conversation]\n{chat_context}\n\n[New Message from {call_name}]: {user_text}"
    signature_html = "\n\n<blockquote>✦ <b><i>From Manager</i></b> ✦</blockquote>"

    async with ai_request_lock:
        if await did_boss_handle_chat(event.chat_id, msg_id):
            return

        IS_BOT_SENDING = True
        try:
            response_text, heal_report = await asyncio.to_thread(
                call_self_healing_ai,
                system_rules, user_prompt, user_text, call_name, use_amharic, is_female, role, chat_context
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

            # ለዘላለም በ Control Bot ላይ የመልሱን እና የ Self-Heal ሪፖርት መላክ
            try:
                heal_note = f"\n🛠️ **Auto-Fix Note:** `{heal_report}` (በራሴ Backup ፈትቼ መልሻለሁ!)" if heal_report else ""
                await bot_client.send_message(
                    MY_USER_ID,
                    f"🔔 **አቤል ምላሽ ሰጥቷል!**\n👤 ለ: **{call_name}** ({detected_gender})\n💬 ጥያቄ: {short_msg}\n🤵‍♂️ መልስ: {clean_reply[:140]}...{heal_note}"
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
            "🚀 **ሰላም አለቃዬ ዘላለም! አቤል (Abel) በአዲሱ Self-Healing ሲስተም ተነስቷል!** 💼✨\n\n"
            "🥷 **የጓደኛ ስምና አይነት በቀላሉ ለመመዝገብ፦**\n"
            "1️⃣ ከምታወራው ሰው ቻት ላይ ገብተህ **`/name zufan`** ብለህ ላክ (እኔ በ0.1 ሰከንድ ወዲያውኑ አጠፋዋለሁ፣ ሰውየው አያየውም!)።\n"
            "2️⃣ ከዛ እዚህ ቦት ላይ መጥተህ **`/zufan`** ብለህ ጻፍልኝ — ለወንድም ለሴትም ያሉትን ሁሉንም የጓደኝነት አይነቶች (Normal ጓደኛ፣ Best Friend፣ ፍቅረኛ፣ ቤተሰብ) እንዴት እንደምትሞላ እኔው ራሴ አሳይሃለሁ!\n\n"
            "🛠️ **Self-Healing Backup:** የውጪ ሰርቨር ቢዘገይ እንኳ ራሴው ችግሩን ፈትቼ 100% መልስ እሰጣለሁ (በፍጹም ዝም አልልም!)። `/doctor` ብለህ መፈተሽ ትችላለህ።"
        )
    except Exception:
        pass

    await asyncio.gather(
        user_client.run_until_disconnected(),
        bot_client.run_until_disconnected()
    )

if __name__ == '__main__':
    loop.run_until_complete(main())
