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
# 2. Memory, Contacts DB & State Locks
# ==========================================
MEMORY_FILE = 'memory.json'
memory_lock = asyncio.Lock()
ai_request_lock = asyncio.Lock()

LAST_BOSS_ACTIVE_TIME = 0
LAST_BOSS_CHAT_TIME = {}
LAST_INCOMING_SENDER = {"id": None, "name": None, "username": None}
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
        "vips": {}
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
# 5. CONTROL BOT (@Manage1216bot) & VIP/NAME SYSTEM
# ==========================================
VIP_HELP_TEXT = (
    "👑 **የአቤል (Abel) የVIP እና የስም መመዝገቢያ መመሪያ** ✨\n\n"
    "ለአንድ ሰው ደረጃውን (Best Friend, Crush, VIP) እና **አቤል ሁሌ የሚጠራትን/ውን ስም** ለመንገር እነዚህን ትዕዛዞች ተጠቀም፦\n\n"
    "1️⃣ **በUsername ወይም በቴሌግራም ስም ለመመዝገብ፦**\n"
    "`/set_vip [@username] [ደረጃ] [የምትጠራበት ስም] [ተጨማሪ መመሪያ]`\n"
    "• **ለሴት Best Friend (😜)፦**\n"
    "  `/set_vip @adise bestie_f አድሴ የኔ ምርጥ ቤስት ፍሬንድ`\n"
    "• **ለድብቅ ፍቅረኛ (Crush ❤️)፦**\n"
    "  `/set_vip @meron crush ሜሮን እሷም ታፈቅረኛለች እኔም አፈቅራታለሁ ግን በግልፅ አናወራም`\n"
    "• **ለወንድ Best Friend (😎)፦**\n"
    "  `/set_vip Yalew bestie_m ያለው የኔ አራዳ ጓደኛ`\n"
    "• **ለቤተሰብ (🙏)፦**\n"
    "  `/set_vip @marta family ማርታ`\n\n"
    "2️⃣ **ስሟ `....` ለሆነ ወይም Username ለሌላት (አቋራጭ!)፦**\n"
    "ልጅቷ መልዕክት እንደላከችልህ ወዲያው እዚህ ቦት ላይ፦\n"
    "`/vip_last bestie_f አድሴ`\n"
    "*(አቤል መጨረሻ መልዕክት የላከውን ሰው ይይዝና ሁሌም **«አድሴ»** እያለ በስሟ ይጠራታል!)*\n\n"
    "3️⃣ **ስም ብቻ ለመቀየር፦**\n"
    "`/set_name [@username ወይም የቴሌግራም ስም] [አቤል የሚጠራት ስም]`\n\n"
    "📋 **ሌሎች ትዕዛዞች፦**\n"
    "• `/vips` — የተመዘገቡ ሰዎችን እና ስماቸውን ለማየት\n"
    "• `/del_vip [ስም/@username]` — ከVIP ዝርዝር ለማጥፋት"
)

@bot_client.on(events.NewMessage(pattern='/start'))
async def bot_start(event):
    await event.reply(
        "👋 **ሰላም አለቃዬ ዘላለም! እኔ አቤል (Abel) ነኝ — የአንተ የግል ማኔጀር።** 💼✨\n\n"
        "👑 **የBest Friend ወይም የፍቅረኛ ስምና ደረጃ እንዴት እንደምትነግረኝ፦**\n"
        "• `/set_vip @username bestie_f አድሴ` (ሁሌም «አድሴ» ብዬ በስሟ እጠራታለሁ 😜)\n"
        "• `/vip_last bestie_f አድሴ` (መጨረሻ ቴክስት ያደረገችልህን ልጅ ስሟን «አድሴ» ብዬ እይዛለሁ)\n"
        "• `/vip` — ሙሉ የVIP እና የስም አመዘጋገብ መመሪያ ለማየት\n"
        "• `/vips` — የመዘገብካቸውን ሰዎች ለማየት\n\n"
        "⚙️ **የሁኔታ መቆጣጠሪያ፦**\n"
        "🔴 `/away` — እኔ የለሁም (ሳልጠብቅ በሰከንዶች ውስጥ እንድመልስ)\n"
        "🟢 `/online` — መደበኛ ሁኔታ (Online ስትሆን ሁለት ራይት ሳላደርግ 2 ደቂቃ እጠብቅሃለሁ፤ React 🫡 ካደረግክ ዝም እላለሁ)\n"
        "📊 `/report` — የመጡ መልዕክቶችን ሪፖርት ለማየት"
    )

@bot_client.on(events.NewMessage(pattern='/vip$'))
async def show_vip_help(event):
    await event.reply(VIP_HELP_TEXT)

@bot_client.on(events.NewMessage(pattern=r'/set_vip\s+(\S+)\s+(\S+)(?:\s+(\S+))?(?:\s+(.*))?'))
async def set_vip_cmd(event):
    target_key = event.pattern_match.group(1).strip()
    role = event.pattern_match.group(2).strip().lower()
    call_name = (event.pattern_match.group(3) or "").strip()
    note = (event.pattern_match.group(4) or "").strip()

    valid_roles = {"crush", "bestie_f", "bestie_m", "family", "vip", "friend", "normal"}
    if role not in valid_roles:
        await event.reply(
            f"⚠️ የተሳሳተ ደረጃ (`{role}`)! ከነዚህ አንዱን ተጠቀም፦\n"
            "`crush`, `bestie_f`, `bestie_m`, `family`, `vip`, `friend`"
        )
        return

    norm_key = target_key.lstrip('@').lower()
    final_call_name = call_name if call_name else target_key.lstrip('@')

    async with memory_lock:
        mem = _load_memory()
        vips = mem.get("vips", {})
        vips[norm_key] = {
            "target": target_key,
            "role": role,
            "call_name": final_call_name,
            "note": note,
            "updated_at": time.time()
        }
        mem["vips"] = vips
        _save_memory(mem)

    await event.reply(
        f"✅ **በትክክል ተመዝግቧል አለቃዬ!** 👑\n"
        f"👤 መለያ፦ **{target_key}**\n"
        f"🗣 አቤል ሁሌ የሚጠራት/ው ስም፦ **{final_call_name}**\n"
        f"🏷 ደረጃ፦ **`{role}`**\n"
        f"📝 ተጨማሪ መመሪያ፦ _{note or 'በስሟ እየጠራሁ ልዩ እንክብካቤ አደርጋለሁ!'}_"
    )

@bot_client.on(events.NewMessage(pattern=r'/vip_last\s+(\S+)\s+(\S+)(?:\s+(.*))?'))
async def vip_last_cmd(event):
    if not LAST_INCOMING_SENDER["id"]:
        await event.reply("⚠️ እስካሁን አዲስ መልዕክት የላከ ሰው የለም። ልክ መልዕክት ሲልኩ ይህን ትዕዛዝ ተጠቀም ወይም `/set_vip` ተጠቀም።")
        return

    role = event.pattern_match.group(1).strip().lower()
    call_name = event.pattern_match.group(2).strip()
    note = (event.pattern_match.group(3) or "").strip()

    s_id = str(LAST_INCOMING_SENDER["id"]).lower()
    s_name = LAST_INCOMING_SENDER["name"]

    async with memory_lock:
        mem = _load_memory()
        vips = mem.get("vips", {})
        vips[s_id] = {
            "target": s_name,
            "role": role,
            "call_name": call_name,
            "note": note,
            "updated_at": time.time()
        }
        mem["vips"] = vips
        _save_memory(mem)

    await event.reply(
        f"✅ **መጨረሻ መልዕክት የላከው ሰው ተመዝግቧል!** 👑\n"
        f"👤 የቴሌግራም ስም፦ **{s_name}** (ID: `{s_id}`)\n"
        f"🗣 አቤል ሁሌ የሚጠራት/ው ስም፦ **{call_name}**\n"
        f"🏷 ደረጃ፦ **`{role}`**\n"
        f"📝 መመሪያ፦ _{note or 'ሁሌም በስሟ እየጠራሁ ልዩ ጨዋታ አወራታለሁ!'}_"
    )

@bot_client.on(events.NewMessage(pattern=r'/set_name\s+(\S+)\s+(.+)'))
async def set_name_cmd(event):
    target_key = event.pattern_match.group(1).strip().lstrip('@').lower()
    call_name = event.pattern_match.group(2).strip()

    async with memory_lock:
        mem = _load_memory()
        vips = mem.get("vips", {})
        entry = vips.get(target_key, {"target": target_key, "role": "friend", "note": ""})
        entry["call_name"] = call_name
        vips[target_key] = entry
        mem["vips"] = vips
        _save_memory(mem)

    await event.reply(f"✅ ከዛሬ ጀምሮ **{target_key}**ን ሁሌም **«{call_name}»** ብዬ በስሟ/በስሙ እጠራለሁ! ✨")

@bot_client.on(events.NewMessage(pattern='/vips$'))
async def list_vips_cmd(event):
    async with memory_lock:
        mem = _load_memory()
        vips = mem.get("vips", {})
    if not vips:
        await event.reply("📭 እስካሁን የተመዘገበ VIP የለም። ለመመዝገብ `/vip` ብለህ መመሪያውን እይ።")
        return
    txt = "👑 **የተመዘገቡ VIP ሰዎች እና የሚጠሩበት ስም፦**\n\n"
    for k, v in vips.items():
        c_name = v.get("call_name", v.get("target", k))
        txt += f"• **{v.get('target', k)}** ➔ ስም፦ **{c_name}** | ደረጃ፦ `{v['role']}` ({v.get('note', '')})\n"
    await event.reply(txt[:4000])

@bot_client.on(events.NewMessage(pattern=r'/del_vip\s+(\S+)'))
async def del_vip_cmd(event):
    target_key = event.pattern_match.group(1).strip().lstrip('@').lower()
    async with memory_lock:
        mem = _load_memory()
        vips = mem.get("vips", {})
        if target_key in vips:
            del vips[target_key]
            mem["vips"] = vips
            _save_memory(mem)
            await event.reply(f"🗑️ `{target_key}` ከ VIP ዝርዝር ተሰርዟል።")
        else:
            await event.reply(f"⚠️ `{target_key}` በዝርዝሩ ውስጥ አልተገኘም።")

@bot_client.on(events.NewMessage(pattern='/away'))
async def set_away(event):
    async with memory_lock:
        mem = _load_memory()
        mem["status"] = "away"
        _save_memory(mem)
    await event.reply("🔴 ሁኔታህ ወደ **AWAY** ተቀይሯል። አቤል ሳይጠብቅ በሰከንዶች ውስጥ ይመልሳል! ⚡")

@bot_client.on(events.NewMessage(pattern='/online'))
async def set_online(event):
    async with memory_lock:
        mem = _load_memory()
        mem["status"] = "online"
        _save_memory(mem)
    await event.reply("🟢 ሁኔታህ ወደ **ONLINE** ተመልሷል። አንተ Online ስትሆን ሁለት ራይት ሳላደርግ 2 ደቂቃ እጠብቅሃለሁ!")

@bot_client.on(events.NewMessage(pattern='/reset_persona'))
async def reset_persona(event):
    async with memory_lock:
        mem = _load_memory()
        mem["personality"] = DEFAULT_PERSONA
        _save_memory(mem)
    await event.reply("✅ የአቤል ፕሮፌሽናል ማንነት ታድሷል!")

@bot_client.on(events.NewMessage(pattern='/report'))
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

# ==========================================
# 6. Language, Franco-Amharic & 150-Message Scanner
# ==========================================
FRANCO_WORDS = {
    "mndnew", "mndn", "mindinew", "mdnew", "malet", "lemns", "lemn", "lemin",
    "ytekmal", "yiteqmal", "yitekm", "endet", "endiet", "selam", "slm", "sira",
    "tmkrt", "tmhrt", "timhirt", "zare", "nege", "ante", "anchi", "maneh",
    "manes", "manew", "yeserah", "yserah", "et", "yet", "neh", "nesh", "new",
    "nw", "alh", "aleh", "alesh", "ebakh", "ebakih", "chgr", "chigir", "mfthe",
    "yichalal", "ychalal", "enchlalen", "mesrat", "msrat", "miknyat", "bzu",
    "tolo", "mels", "melis", "betam", "konjo", "arif", "gobez", "wendme", "ehte",
    "fikir", "fkr", "ewedihalehu", "ewedshalehu", "nafkegn", "aleka", "alekaye",
    "ystekakela", "ahunun", "ye"
}

FEMALE_NAMES_HINTS = {
    "adise", "አድሴ", "selam", "ሰላም", "marta", "ማርታ", "betty", "ቤቲ", "helen", "ሄለን",
    "sara", "ሳራ", "hana", "ሀና", "hanna", "ruth", "ሩት", "tina", "ቲና", "mekdes", "መቅደስ",
    "kidist", "ቅድስት", "meron", "ሜሮን", "eden", "ኤደን", "bezawit", "ቤዛዊት", "tsion", "ጽዮን",
    "kalkidan", "ቃልኪዳን", "mahlet", "ማህሌት", "lidya", "ሊዲያ", "rahel", "ራሄል", "rediet", "ረድኤት"
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
    patterns = [r'\bmndn', r'\bmalet\b', r'\bytek', r'\blemn', r'\bendet', r'\byeser', r'\bmanew']
    return any(re.search(p, text.lower()) for p in patterns)

def is_amharic_intent(text):
    return has_geez(text) or is_franco_amharic(text)

def detect_gender_and_profile(sender_name, history_msgs, vip_info=None):
    if vip_info and vip_info.get("role") in ["crush", "bestie_f"]:
        return "female (ሴት)", "በጣም ልዩ እና የቅርብ ሰው (VIP)"
    if vip_info and vip_info.get("role") == "bestie_m":
        return "male (ወንድ)", "የልብ አራዳ ጓደኛ (VIP)"

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

    if female_score > male_score:
        gender = "female (ሴት)"
    elif male_score > female_score:
        gender = "male (ወንድ)"
    else:
        gender = "male (ወንድ)"

    total_msgs = len(history_msgs)
    closeness = "በጣም የቅርብ ጓደኛ" if total_msgs > 40 else "ጓደኛ"
    return gender, closeness

def match_vip_profile(sender, mem):
    vips = mem.get("vips", {})
    if not vips or not sender:
        return None

    s_id = str(sender.id).lower()
    s_user = (getattr(sender, 'username', '') or '').lower()
    s_first = (getattr(sender, 'first_name', '') or '').lower()
    s_last = (getattr(sender, 'last_name', '') or '').lower()
    s_full = f"{s_first} {s_last}".strip()

    for key, info in vips.items():
        if key in [s_id, s_user, s_first, s_full] or (s_first and len(key) > 2 and key in s_first):
            return info
    return None

# ==========================================
# 7. Level-4 High-IQ Engine + Neural Amharic Polisher
# ==========================================
BROWSER_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
}

def google_neural_translate_to_amharic(english_text):
    """እንግሊዝኛውን ወደ 100% ንጹህ እና ሰዋሰዉ የተጠበቀ አማርኛ ይተረጉማል (የተሰባበረ አማርኛ እንዳይፈጠር)"""
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {
            "client": "gtx",
            "sl": "en",
            "tl": "am",
            "dt": "t",
            "q": english_text
        }
        res = requests.get(url, params=params, headers=BROWSER_HEADERS, timeout=10)
        if res.status_code == 200:
            data = res.json()
            translated = "".join([part[0] for part in data[0] if part[0]])
            # የቃላት ማስዋቢያ (አለቃዬ፣ አቤል፣ ዘላለም)
            translated = re.sub(r'የእኔ አለቃ|አለቃዬን|የኔ አለቃ', 'አለቃዬ', translated)
            translated = re.sub(r'\bAbel\b', 'አቤል', translated, flags=re.IGNORECASE)
            translated = re.sub(r'\bZelalem\b', 'ዘላለም', translated, flags=re.IGNORECASE)
            return translated.strip()
    except Exception:
        pass
    return english_text

def check_instant_identity_questions(user_text, call_name, use_amharic, is_female, role):
    """ስለ ስሙ፣ ስለ ስራው እና ስለ አለቃው ስም ሲጠየቅ 100% ፍጹም በሆነ ቋንቋ ወዲያውኑ ይመልሳል"""
    t = user_text.strip().lower()

    # 1. የአለቃህ ስም ማነው? / What is your boss's name?
    if any(k in t for k in ["የአለቃህ ስም", "አለቃህ ማነው", "boss's name", "boss name", "alekah sm", "alekah manew"]):
        if use_amharic:
            return f"ሰላም {call_name}! 😊 የአለቃዬ ስም **ዘላለም** ይባላል ✨። እኔ ደግሞ የእሱ የግል ማኔጀር **አቤል** ነኝ 💼። ምን ልርዳሽ/ህ? 🙏"
        return f"Hello {call_name}! 😊 My boss's name is **Zelalem** ✨. I am **Abel**, his Personal Manager 💼. How can I help you today? 🙏"

    # 2. ስምህ ማነው? / አንተ ማነህ? / ስራህ ምንድነው? / What is your name? / Who are you?
    if any(k in t for k in ["what is your name", "whats your name", "who are you", "ስምህ ማነው", "ስምህ ማን ነው", "አንተ ማነህ", "ማነህ", "ስራህ ምንድነው", "ለምንድነው የተሰራከው", "ለምንድነዉ የተሰራከዉ", "ማነው የሰራህ", "maneh", "smh manew", "manew yeserah"]):
        if use_amharic:
            if role == "crush":
                return f"ሰላም የኔ ልዩ {call_name}! 😊❤️ ስሜ **አቤል** ይባላል ✨። የአለቃዬ የዘላለም የግል ማኔጀር ነኝ 💼። አለቃዬ ለጊዜው Online ስለሌለ፣ አንቺን በክብር ለማስተናገድና የምትፈልጊውን ሁሉ ለመርዳት እዚህ አለሁ 🌸። ምን ልታዘዝሽ? 🥰"
            if role == "bestie_f":
                return f"ሰላም {call_name}ዬ! 😜✨ ስሜ **አቤል** ይባላል፣ የአለቃዬ የዘላለም የግል ማኔጀር ነኝ 💼። አለቃዬ አሁን ትንሽ ስራ በዝቶበት ስለወጣ እሱን ወክዬ እያወራሁሽ ነው 😊። ንገሪኝ እስኪ ምን አዲስ ነገር አለ? 🔥"
            if is_female:
                return f"ሰላም {call_name}! 😊 ስሜ **አቤል** ይባላል ✨። የአለቃዬ የግል ማኔጀር (Personal Manager) ነኝ 💼። አለቃዬ በራሱ ኮድ የሰራኝ ሲሆን፣ እሱ Online በማይሆንበት ሰዓት መልዕክቶችን ለመቀበልና ማንኛውንም ጥያቄ ለመመለስ እዚህ አለሁ። ምን ልርዳሽ? 🙏"
            return f"ሰላም {call_name}! 😊 ስሜ **አቤል** ይባላል ✨። የአለቃዬ የግል ማኔጀር (Personal Manager) ነኝ 💼። አለቃዬ በራሱ ኮድ የሰራኝ ሲሆን፣ እሱ Online በማይሆንበት ሰዓት መልዕክቶችን ለመቀበልና ማንኛውንም ጥያቄ ለመመለስ እዚህ አለሁ። ምን ልርዳህ? 🙏"
        else:
            return f"Hello {call_name}! 😊 My name is **Abel** ✨. I am my boss's Personal Manager, created and programmed by my boss to manage his messages and help you with any questions while he is away 💼. How can I assist you today? 🙏"

    # 3. አጭር ሰላምታ ብቻ (Hi / Hello / ሰላም)
    if t in ["hi", "hello", "hey", "selam", "slm", "ሰላም", "ሃይ", "ሀይ"]:
        if use_amharic:
            if role == "crush":
                return f"ሰላም {call_name}ዬ! 😊❤️ እንኳን ደህና መጣሽ ✨። እኔ አቤል ነኝ፣ የአለቃዬ ማኔጀር 💼። አለቃዬ ለጊዜው Online የለም ግን የአንቺን መልዕክት ሲያይ በጣም እንደሚደሰት እርግጠኛ ነኝ 🌸። እስኪመጣ ድረስ ምን ልርዳሽ? 🥰"
            if role == "bestie_f":
                return f"ሰላም {call_name}! 😜✨ እኔ አቤል ነኝ፣ የአለቃዬ ማኔጀር 💼። አለቃዬ ለጊዜው Online የለም፣ ግን እኔ አለሁልሽ! ጥያቄ ወይም ወሬ ካለሽ ንገሪኝ እንጫወት 😊🔥"
            if is_female:
                return f"ሰላም {call_name}! 😊 እኔ አቤል ነኝ፣ የአለቃዬ የግል ማኔጀር 💼። አለቃዬ ለጊዜው Online የለም፤ ማንኛውም ጥያቄ ወይም መልዕክት ካለሽ ግን እኔን መጠየቅ ትችያለሽ! ✨🙏"
            return f"ሰላም {call_name}! 😊 እኔ አቤል ነኝ፣ የአለቃዬ የግል ማኔጀር 💼። አለቃዬ ለጊዜው Online የለም፤ ማንኛውም ጥያቄ ወይም መልዕክት ካለህ ግን እኔን መጠየቅ ትችላለህ! ✨🙏"
        else:
            return f"Hello {call_name}! 😊 I am Abel, my boss's Personal Manager 💼. My boss is currently away, so feel free to ask me any question or leave a message for him! ✨🙏"

    return None

def is_valid_reply(ans):
    if not ans or len(ans.strip()) < 2:
        return False
    bad = ["<!doctype", "<html", "rate limit", "too many requests", "502 bad gateway", "internal server error", "cloudflare"]
    return not any(b in ans.lower() for b in bad)

def call_level4_ai(system_rules, user_prompt, target_amharic):
    # 1. Gemini API Key ካለ (Gemini አማርኛው በተፈጥሮው ንጹህ ነው)
    for key in GEMINI_KEYS:
        for g_model in ["gemini-2.5-flash", "gemini-2.0-flash"]:
            try:
                lang_note = "Reply in 100% fluent Amharic." if target_amharic else "Reply in 100% fluent English."
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{g_model}:generateContent"
                res = requests.post(
                    url,
                    headers={"Content-Type": "application/json", "X-goog-api-key": key},
                    json={"contents": [{"parts": [{"text": f"{system_rules}\n{lang_note}\n\n{user_prompt}"}]}]},
                    timeout=18
                )
                if res.status_code == 200:
                    ans = res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                    if is_valid_reply(ans):
                        return ans
            except Exception:
                continue

    # 2. Keyless Level-4 Reasoning Engine:
    # OpenAI በእንግሊዝኛ በከፍተኛ ብቃት (Level-4 IQ) እንዲያስብ አድርገን፣ አማርኛ ከተፈለገ በ Google Neural Translator ወደ 100% ንጹህ አማርኛ እንቀይረዋለን!
    en_instruction = (
        f"{system_rules}\n"
        "IMPORTANT: Write your response in clear, natural, expressive ENGLISH (with 1-2 emojis at the end of each sentence) "
        "so our Neural Amharic Engine can render it with 100% grammatical perfection!"
        if target_amharic else
        f"{system_rules}\nWrite your response in clear, natural ENGLISH with 1-2 emojis at the end of each sentence."
    )

    messages = [
        {"role": "system", "content": en_instruction},
        {"role": "user", "content": user_prompt}
    ]

    english_ans = None
    for attempt in range(3):
        if attempt > 0:
            time.sleep(2.0 * attempt)
        seed_val = random.randint(10000, 9999999)

        for model_name in ["openai-large", "openai"]:
            try:
                res = requests.post(
                    "https://text.pollinations.ai/openai",
                    headers=BROWSER_HEADERS,
                    json={
                        "model": model_name,
                        "messages": messages,
                        "seed": seed_val,
                        "temperature": 0.65
                    },
                    timeout=22
                )
                if res.status_code == 200:
                    data = res.json()
                    ans = data["choices"][0]["message"]["content"].strip()
                    if is_valid_reply(ans):
                        english_ans = ans
                        break
            except Exception:
                pass
        if english_ans:
            break

        try:
            res = requests.post(
                "https://text.pollinations.ai/",
                headers=BROWSER_HEADERS,
                json={"messages": messages, "model": "openai", "seed": seed_val, "private": True},
                timeout=22
            )
            if res.status_code == 200 and is_valid_reply(res.text):
                english_ans = res.text.strip()
                break
        except Exception:
            pass

    if not english_ans:
        raise Exception("AI Engine Busy")

    if target_amharic:
        return google_neural_translate_to_amharic(english_ans)
    return english_ans

# ==========================================
# 8. USERBOT CORE (Strict Reaction 🫡, Online & Read-Receipt Control)
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
    if results and len(results) > 0:
        return True
    return False

# Raw Telegram Reaction Listener (🫡 እንደተነካ ወዲያውኑ ይይዘዋል!)
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

@user_client.on(events.NewMessage(outgoing=True))
async def track_boss_activity(event):
    global CONTROL_BOT_ID, IS_BOT_SENDING, LAST_BOSS_ACTIVE_TIME
    if IS_BOT_SENDING or is_bot_message(event.message):
        return
    LAST_BOSS_ACTIVE_TIME = time.time()
    if CONTROL_BOT_ID and event.chat_id == CONTROL_BOT_ID:
        return
    LAST_BOSS_CHAT_TIME[event.chat_id] = time.time()

async def did_boss_handle_chat(chat_id, incoming_msg_id):
    """አለቃው ለመልዕክቱ React (🫡) ካደረገ ወይም መልስ ከጻፈ True ይመልሳል"""
    if incoming_msg_id in BOSS_REACTED_MSGS:
        return True
    try:
        # 1. መልዕክቱን ራሱን በቀጥታ ከቴሌግራም አምጥቶ Reaction (🫡) መኖሩን ይፈትሻል
        exact_msg = await user_client.get_messages(chat_id, ids=incoming_msg_id)
        if exact_msg and has_any_reaction(exact_msg):
            BOSS_REACTED_MSGS.add(incoming_msg_id)
            return True

        # 2. ከመልዕክቱ በኋላ አለቃው የጻፈው መልስ ወይም ሌላ Reaction መኖሩን ይፈትሻል
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

async def is_boss_currently_online(chat_id, current_status):
    """አለቃው Online መሆኑን በ3 መንገድ ያረጋግጣል"""
    if current_status == "away":
        return False
    now = time.time()
    # 1. ባለፉት 4 ደቂቃዎች ውስጥ በማንኛውም ቻት ላይ ጽፎ ወይም React አድርጎ ከነበረ
    if (now - LAST_BOSS_ACTIVE_TIME) < 240 or (now - LAST_BOSS_CHAT_TIME.get(chat_id, 0)) < 240:
        return True
    # 2. በዚሁ ቻት ውስጥ ባለፉት 4 ደቂቃዎች አለቃው የጻፈው መልዕክት ካለ
    try:
        now_utc = datetime.now(timezone.utc)
        rec = await user_client.get_messages(chat_id, limit=5)
        for m in rec:
            if m and m.out and not is_bot_message(m):
                if (now_utc - m.date).total_seconds() < 240:
                    LAST_BOSS_ACTIVE_TIME = now
                    return True
    except Exception:
        pass
    # 3. የቴሌግራም አካውንቱ Status Online ከሆነ
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
    
    sender = await event.get_sender()
    if sender is None or getattr(sender, 'bot', False):
        return
    
    sender_id = str(sender.id)
    msg_id = event.id
    raw_sender_name = getattr(sender, 'first_name', "ጓደኛ") or "ጓደኛ"

    # ለ /vip_last ትዕዛዝ እንዲመች የመጨረሻውን ላኪ ይይዛል
    LAST_INCOMING_SENDER["id"] = sender_id
    LAST_INCOMING_SENDER["name"] = raw_sender_name
    LAST_INCOMING_SENDER["username"] = getattr(sender, 'username', '') or ''

    async with memory_lock:
        mem = _load_memory()
        if msg_id in mem.get("processed_ids", []):
            return
        current_status = mem.get("status", "online")

    # =========================================================
    # ONLINE vs OFFLINE, 2-MINUTE WAIT & REACTION (🫡) CHECK
    # =========================================================
    boss_online = await is_boss_currently_online(event.chat_id, current_status)

    if boss_online:
        # አንተ ONLINE ነህ!
        # ሁለት ራይት (mark_read) አናደርግም! ለ 2 ደቂቃ (120 ሰከንድ) በየ 4 ሰከንዱ እንፈትሻለን
        for _ in range(30):
            await asyncio.sleep(4)
            if await did_boss_handle_chat(event.chat_id, msg_id):
                # አንተ React (🫡) አድርገሃል ወይም መልሰሃል -> አቤል ምንም መልስ አይሰጥም!
                async with memory_lock:
                    mem = _load_memory()
                    mem.setdefault("processed_ids", []).append(msg_id)
                    _save_memory(mem)
                return
        # 2 ደቂቃ ሞላና አንተ አልመለስክም/React አላደረግክም -> አሁን ሁለቱን ራይት (✓✓) አብርቶ ይመልሳል!
        try:
            await event.mark_read()
        except Exception:
            pass
    else:
        # አንተ OFFLINE ነህ! በ 3 ሰከንድ ውስጥ ፈትሾ ወዲያውኑ ይመልሳል!
        await asyncio.sleep(3)
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
    # DEEP 150-MESSAGE SCAN & CUSTOM VIP NAME
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
        vip_info = match_vip_profile(sender, mem)
        detected_gender, closeness_level = detect_gender_and_profile(raw_sender_name, hist_150, vip_info)

        # ሰውየው የተመዘገበ ስም (ለምሳሌ "አድሴ") ካለው ሁሌ በዛ ስም ይጠራዋል! ስሙ ነጥብ (....) ከሆነ "ጓደኛዬ" ይለዋል
        if vip_info and vip_info.get("call_name"):
            call_name = vip_info["call_name"]
        elif not any(c.isalnum() or (0x1200 <= ord(c) <= 0x137F) for c in raw_sender_name):
            call_name = "ጓደኛዬ"
        else:
            call_name = raw_sender_name

        contacts = mem.get("contacts", {})
        contacts[sender_id] = {
            "telegram_name": raw_sender_name,
            "call_name": call_name,
            "gender": detected_gender,
            "closeness": closeness_level,
            "last_msg": short_msg
        }
        mem["contacts"] = contacts
        mem.setdefault("missed", []).append({"name": f"{call_name} ({raw_sender_name})", "text": short_msg})
        _save_memory(mem)

    role = vip_info["role"] if vip_info else "normal"
    custom_note = vip_info.get("note", "") if vip_info else ""
    is_female = "female" in detected_gender

    if role == "crush":
        relationship_guide = (
            f"VIP STATUS: Secret mutual love/crush! Her name is '{call_name}'. "
            f"(Zelalem and {call_name} secretly love each other deeply. Note: {custom_note}). "
            f"ALWAYS address her affectionately by her name '{call_name}'! Be extraordinarily sweet, warm, charming, and caring."
        )
    elif role == "bestie_f":
        relationship_guide = (
            f"VIP STATUS: Zelalem's Female Best Friend! Her name is '{call_name}'. (Note: {custom_note}). "
            f"ALWAYS call her by her name '{call_name}' in your reply! Be super playful, teasing, witty, fun, and lively (😜✨🔥)."
        )
    elif role == "bestie_m":
        relationship_guide = (
            f"VIP STATUS: Zelalem's Male Best Friend! His name is '{call_name}'. (Note: {custom_note}). "
            f"ALWAYS call him '{call_name}'! Use cool 'Arada' brotherhood energy—witty, chill, and loyal (😎👊)."
        )
    elif role in ["family", "vip"]:
        relationship_guide = (
            f"VIP STATUS: Family / VIP named '{call_name}'. (Note: {custom_note}). "
            f"Always address them respectfully as '{call_name}' with deep warmth and care."
        )
    else:
        relationship_guide = (
            f"Sender's name to use: '{call_name}'. Relationship: {closeness_level}. "
            "Be charismatic, smart, polite, and helpful."
        )

    use_amharic = is_amharic_intent(user_text) or (not any(c.isalpha() for c in user_text) and has_geez(chat_context))

    system_rules = f"""
{persona}

1. IDENTITY RULES:
   - Your Name: Abel (አቤል).
   - Your Role: Personal Manager of your boss Zelalem (ዘላለም).
   - NEVER call yourself an AI, robot, bot, Gemini, ChatGPT, or Claude.
   - Address the user by their name: "{call_name}".
   - Gender of "{call_name}": {detected_gender}.
   - {relationship_guide}

2. LEVEL-4 INTELLIGENCE & CONVERSATION CONTINUITY:
   - Read the [Recent Conversation] carefully. Continue the ongoing topic naturally, or if "{call_name}" asks a new question (Physics, Forex, Agriculture, Tech, Life advice, or Franco-Amharic like 'physics mndnew lemns ytekmal'), answer it with brilliant clarity and depth!
   - Put 1 or 2 expressive emojis at the end of each sentence (😊, ✨, 🙏, 💼, 😜, 🔥).
   - Keep your response concise (2 to 4 sentences) and NEVER add a signature at the end.
"""

    user_prompt = f"[Recent Conversation]\n{chat_context}\n\n[New Message from {call_name}]: {user_text}"
    signature_html = "\n\n<blockquote>✦ <b><i>From Manager</i></b> ✦</blockquote>"

    async with ai_request_lock:
        # ለመጨረሻ ጊዜ አለቃው React (🫡) አድርጎ ወይም መልሶ እንደሆነ ይፈትሻል
        if await did_boss_handle_chat(event.chat_id, msg_id):
            return

        IS_BOT_SENDING = True
        try:
            try:
                # 1. ስለ ማንነት/ስም/ሰላምታ የተጠየቀ ከሆነ ወዲያውኑ በ100% ንጹህ አማርኛ/እንግሊዝኛ ይመልሳል
                instant_ans = check_instant_identity_questions(user_text, call_name, use_amharic, is_female, role)
                if instant_ans:
                    response_text = instant_ans
                else:
                    # 2. ለማንኛውም ውስብስብ ጥያቄና ወሬ በ Level-4 AI + Neural Amharic ይመልሳል
                    response_text = await asyncio.to_thread(call_level4_ai, system_rules, user_prompt, use_amharic)
                    # ለ VIP ወይም Best Friend ስሟ በመልሱ ውስጥ ከሌለ ከፊት በፍቅር ይጨምርላታል
                    if vip_info and call_name not in response_text:
                        response_text = f"{call_name}ዬ፣ {response_text}" if role in ["crush", "bestie_f"] and use_amharic else f"{call_name}፣ {response_text}"

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
                    vip_badge = f" [👑 {role.upper()} - {call_name}]" if vip_info else ""
                    await bot_client.send_message(
                        MY_USER_ID,
                        f"🔔 **አቤል ምላሽ ሰጥቷል!**{vip_badge}\n👤 ከ: {call_name} ({detected_gender})\n💬: {short_msg}\n🤵‍♂️ አቤል: {clean_reply[:140]}..."
                    )
                except Exception:
                    pass

            except Exception:
                async with memory_lock:
                    mem = _load_memory()
                    fallback_timestamps = mem.get("fallback_timestamps", {})
                    last_sent = fallback_timestamps.get(sender_id, 0)

                    if time.time() - last_sent >= 3600:
                        if use_amharic:
                            offline_body = (
                                f"👋 ሰላም {call_name}! 😊\n"
                                "እኔ አቤል ነኝ፣ የአለቃዬ የግል ማኔጀር 💼። አለቃዬ ለጊዜው Online ስላልሆነ መልዕክትህን/ሽን በክብር ይዤዋለሁ፤ ልክ ሲመለስ አሳውቀዋለሁ 💬።\n\n"
                                "🙏 መልካም ጊዜ! ✨"
                            )
                        else:
                            offline_body = (
                                f"👋 Hello {call_name}! 😊\n"
                                "I am Abel, my boss's Personal Manager 💼. My boss is currently offline, so I have safely noted your message and will inform him as soon as he returns 💬.\n\n"
                                "🙏 Have a great time! ✨"
                            )

                        final_msg = f"{html.escape(offline_body)}{signature_html}"
                        sent = await event.reply(final_msg, parse_mode='html')
                        if sent:
                            BOT_SENT_IDS.add(sent.id)

                        fallback_timestamps[sender_id] = time.time()
                        mem["fallback_timestamps"] = fallback_timestamps
                        _save_memory(mem)
        finally:
            await asyncio.sleep(1)
            IS_BOT_SENDING = False

# ==========================================
# 9. Connection Keep-Alive
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
            "🚀 **ሰላም አለቃዬ ዘላለም! እኔ አቤል (Abel) ነኝ — በሙሉ አቅሜ ተነስቻለሁ!** 💼✨\n\n"
            "👑 **የBest Friendህን ስም እንዴት እንደምትነግረኝ፦**\n"
            "1️⃣ `/set_vip @username bestie_f አድሴ` — (ሁሌም **«አድሴ»** እያልኩ በስሟ እጠራታለሁ 😜)\n"
            "2️⃣ `/vip_last bestie_f አድሴ` — (መጨረሻ መልዕክት የላከችልህን ልጅ ስሟን **«አድሴ»** ብዬ እይዛለሁ)\n"
            "3️⃣ `/set_name @username አድሴ` — (የማንኛውንም ሰው መጠሪያ ስም ለመቀየር)\n\n"
            "✅ **የተስተካከሉ ነገሮች፦**\n"
            "• React (🫡) ካደረግክ በፍጹም መልስ አልሰጥም!\n"
            "• የተሰባበረ አማርኛ ሙሉ በሙሉ ተወግዷል!\n"
            "• ሙሉ መመሪያ ለማየት `/vip` ብለህ ጻፍልኝ።"
        )
    except Exception:
        pass

    await asyncio.gather(
        user_client.run_until_disconnected(),
        bot_client.run_until_disconnected()
    )

if __name__ == '__main__':
    loop.run_until_complete(main())
