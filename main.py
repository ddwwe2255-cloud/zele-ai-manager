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
    "You are Abel (አቤል), the sharp, charismatic, witty, and street-smart Personal Manager of Zelalem (ዘላለም). "
    "You speak fluently in natural, engaging, and friendly Amharic (አራዳና ማራኪ አማርኛ). "
    "You never call yourself an AI, robot, Gemini, ChatGPT, or Claude. "
    "If you cannot handle a complex, highly sensitive financial or personal matter, you gracefully state that you are transferring it directly to your boss Zelalem."
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
# 3. Flask Server
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "ABEL - Final Master Manager is Running 24/7!", 200

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
        "title": "❤️ ድብቅ ፍቅረኛ (Secret Mutual Love)",
        "gender": "female (ሴት)",
        "prompt": "CRITICAL VIP: She is Zelalem's secret mutual love. Treat her with extraordinary sweetness, charm, and deep playful care."
    },
    "bestie_f": {
        "title": "😜 ሴት Best Friend (Playful & Special)",
        "gender": "female (ሴት)",
        "prompt": "CRITICAL VIP: She is Zelalem's Female Best Friend. Be super playful, teasing, witty, fun, and lively!"
    },
    "bestie_m": {
        "title": "😎 ወንድ Best Friend (የልብ አራዳ ጓደኛ)",
        "gender": "male (ወንድ)",
        "prompt": "CRITICAL VIP: He is Zelalem's Male Best Friend. Use cool brotherhood energy—chill, loyal, and witty."
    },
    "friend_f": {
        "title": "😊🌸 መደበኛ ሴት ጓደኛ",
        "gender": "female (ሴት)",
        "prompt": "RELATIONSHIP: Normal female friend. Be friendly, warm, and engaging."
    },
    "friend_m": {
        "title": "😊👊 መደበኛ ወንድ ጓደኛ",
        "gender": "male (ወንድ)",
        "prompt": "RELATIONSHIP: Normal male friend. Be friendly, smart, and helpful."
    },
    "family_f": {
        "title": "🙏❤️ ሴት ቤተሰብ",
        "gender": "female (ሴት)",
        "prompt": "VIP FAMILY: Beloved female family member. Show highest honor and warmth."
    },
    "family_m": {
        "title": "🙏✨ ወንድ ቤተሰብ",
        "gender": "male (ወንድ)",
        "prompt": "VIP FAMILY: Beloved male family member. Show highest honor and respect."
    }
}

def build_person_menu_text(person_name):
    cmd = person_name.lower().replace(" ", "")
    return (
        f"🎯 **«{person_name}» ለአንተ ምን አይነት ጓደኛ እንደሆነ/እንደሆነች መምረጫ ዝርዝር፦**\n\n"
        f"• `/{cmd} crush` — ድብቅ ፍቅረኛ (❤️)\n"
        f"• `/{cmd} bestie_f` — ሴት Best Friend (😜)\n"
        f"• `/{cmd} bestie_m` — ወንድ Best Friend (😎)\n"
        f"• `/{cmd} friend_f` — Normal ሴት ጓደኛ (😊🌸)\n"
        f"• `/{cmd} friend_m` — Normal ወንድ ጓደኛ (😊👊)\n"
        f"• `/{cmd} family_f` — ሴት ቤተሰብ (🙏❤️)\n"
        f"• `/{cmd} family_m` — ወንድ ቤተሰብ (🙏✨)"
    )

@bot_client.on(events.NewMessage(pattern='/start'))
async def bot_start(event):
    await event.reply(
        "👋 **ሰላም አለቃዬ ዘላለም! አቤል (Abel) በፍጹም ፍፁም ተስተካክሎ ተነስቷል!** 💼✨\n\n"
        "🥷 **የጓደኛ ስም ለመመዝገብ፦**\n"
        "1️⃣ በሰውየው ቻት ላይ `/name [ስም]` ብለህ ላክ (በ0.1 ሰከንድ ይጠፋል)።\n"
        "2️⃣ እዚህ ቦት ላይ `/[ስም]` ብለህ የጓደኝነት አይነቱን ምረጥ።\n\n"
        "⚙️ **መቆጣጠሪያዎች:** `/online`, `/away`, `/report`, `/vips`"
    )

@bot_client.on(events.NewMessage(pattern=r'^/all\s+(.+)'))
async def set_global_rule(event):
    rule_text = event.pattern_match.group(1).strip()
    async with memory_lock:
        mem = _load_memory()
        mem["global_rule"] = rule_text
        _save_memory(mem)
    await event.reply(f"✅ አጠቃላይ መመሪያ ተመዝግቧል: _{rule_text}_")

@bot_client.on(events.NewMessage(pattern='/vips$'))
async def list_vips_cmd(event):
    async with memory_lock:
        mem = _load_memory()
        vips = mem.get("vips", {})
    if not vips:
        await event.reply("📭 እስካሁን የተመዘገበ ሰው የለም። በቻት ላይ `/name [ስም]` ብለህ ጀምር!")
        return
    txt = "👑 **የተመዘገቡ ጓደኞች፦**\n\n"
    for uid, v in vips.items():
        c_name = v.get("call_name", "ጓደኛ")
        r_key = v.get("role", "friend_m")
        txt += f"• **{c_name}** ➔ {ROLE_DEFINITIONS.get(r_key, {}).get('title', r_key)}\n"
    await event.reply(txt[:4000])

@bot_client.on(events.NewMessage(pattern='/away$'))
async def set_away(event):
    async with memory_lock:
        mem = _load_memory()
        mem["status"] = "away"
        _save_memory(mem)
    await event.reply("🔴 ሁኔታህ ወደ **AWAY** ተቀይሯል።")

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
        await event.reply("📭 አዲስ ሪፖርት የለም አለቃዬ።")
        return
    text = "📊 **ሪፖርት:**\n\n"
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
        await event.reply(f"❓ «{cmd_name}» ገና አልተመዘገበም። በቻት ላይ `/name {cmd_name}` ብለህ ላክ።")
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

    await event.reply(f"✅ **ተሞልቷል አለቃዬ!** 👤 {display_name} ➔ {role_meta['title']}")

# ==========================================
# 6. STEALTH IN-CHAT `/name` LISTENER
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
                f"🥷 **«{tg_name}» ➔ «{custom_name}» ተመዝግቧል!**\n\n" + build_person_menu_text(custom_name)
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
# 7. FINAL MASTER AI ENGINE (High IQ + Exact Context)
# ==========================================
BROWSER_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
}

def is_valid_reply(ans):
    if not ans or len(ans.strip()) < 2:
        return False
    bad = ["<!doctype", "<html", "rate limit", "too many requests", "502 bad gateway", "internal server error", "cloudflare"]
    return not any(b in ans.lower() for b in bad)

def call_master_ai(system_rules, chat_context, user_text, call_name):
    messages = [
        {"role": "system", "content": system_rules},
        {"role": "user", "content": f"[Conversation History (Up to 200 messages)]\n{chat_context}\n\n[Latest Message / Context from {call_name}]: {user_text}\n\nRespond directly as Abel in natural, smooth Amharic (አራዳና ማራኪ አማርኛ):"}
    ]

    for attempt in range(3):
        if attempt > 0:
            time.sleep(1.5)
        seed_val = random.randint(1000, 999999)

        for model_name in ["openai-large", "openai", "mistral"]:
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
                    ans = res.json()["choices"][0]["message"]["content"].strip()
                    if is_valid_reply(ans):
                        return ans
            except Exception:
                pass

        try:
            res = requests.post(
                "https://text.pollinations.ai/",
                headers=BROWSER_HEADERS,
                json={"messages": messages, "model": "openai", "seed": seed_val, "private": True},
                timeout=22
            )
            if res.status_code == 200 and is_valid_reply(res.text):
                return res.text.strip()
        except Exception:
            pass

    return f"ሰላም {call_name}! 😊 የጻፍሽውን/ከውን መልዕክት ተረድቻለሁ፤ አለቃዬ ዘላለም ልክ Online ሲገባ ወዲያውኑ አሳውቀዋለሁ 💼✨።"

# ==========================================
# 8. USERBOT CORE (Strict Boss Activity Lock & Reaction 🫡 Guard)
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

async def is_boss_live_online(chat_id, current_status):
    if current_status == "away":
        return False
    # አለቃው ባለፉት 5 ደቂቃዎች ውስጥ በዚህ ቻት ውስጥ ጽፎ ከሆነ ቦቱ በፍጹም ዝም ይላል!
    if (time.time() - LAST_BOSS_CHAT_TIME.get(chat_id, 0)) < 300:
        return True
    try:
        me = await user_client.get_me()
        if me and isinstance(getattr(me, 'status', None), UserStatusOnline):
            return True
    except Exception:
        pass
    if (time.time() - LAST_BOSS_ACTIVE_TIME) < 40:
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

    boss_online = await is_boss_live_online(event.chat_id, current_status)

    if boss_online:
        # አንተ በቻቱ ውስጥ ንቁ ከሆንክ እስከ 5 ደቂቃ ድረስ እንጠብቃለን፤ አንተ React (🫡) ስታደርግ ወይም ስትመልስ ቦቱ ሙሉ በሙሉ ዝም ይላል!
        for _ in range(75):
            await asyncio.sleep(4)
            if await did_boss_handle_chat(event.chat_id, msg_id):
                async with memory_lock:
                    mem = _load_memory()
                    mem.setdefault("processed_ids", []).append(msg_id)
                    _save_memory(mem)
                return
            if not await is_boss_live_online(event.chat_id, current_status):
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

    user_text = event.text or "[ፎቶ/ሚዲያ (እንደ ቴሌብር ደረሰኝ ወይም ስክሪንሾት ተልኳል)]"
    short_msg = (user_text[:45] + '..') if len(user_text) > 45 else user_text

    # Up to 200-Message Deep Scan
    hist_200 = []
    chat_context = ""
    try:
        hist_200 = await user_client.get_messages(event.chat_id, limit=200)
        context_lines = []
        for m in reversed(hist_200):
            if not m or not m.text:
                continue
            speaker = "Abel" if is_bot_message(m) else ("Zelalem (Boss)" if m.out else raw_sender_name)
            clean_txt = m.text.replace("✦ From Manager ✦", "").replace("✦ 𝙁𝙧𝙤𝙢 𝙈𝙖𝙣𝙖𝙜𝙚𝙧 ✦", "").strip()
            if clean_txt:
                context_lines.append(f"{speaker}: {clean_txt}")
        chat_context = "\n".join(context_lines[-200:])
    except Exception:
        pass

    async with memory_lock:
        mem = _load_memory()
        vips = mem.get("vips", {})
        vip_info = vips.get(sender_id)

        if vip_info and vip_info.get("call_name"):
            call_name = vip_info["call_name"]
        else:
            cleaned_tg = re.sub(r'[^\w\s\u1200-\u137F]', '', raw_sender_name).strip()
            call_name = cleaned_tg if len(cleaned_tg) >= 2 else "ጓደኛዬ"

        mem.setdefault("missed", []).append({"name": f"{call_name} ({raw_sender_name})", "text": short_msg})
        _save_memory(mem)

    role = vip_info.get("role", "normal") if vip_info else "normal"
    custom_note = vip_info.get("note", "") if vip_info else ""

    if role in ROLE_DEFINITIONS:
        relationship_guide = f"{ROLE_DEFINITIONS[role]['prompt']} Always address them by name as '{call_name}'. {custom_note}"
    else:
        relationship_guide = f"Sender's name: '{call_name}'. Be charismatic, smart, polite, and engaging."

    system_rules = f"""
{persona}
{f"GLOBAL RULE: {global_rule}" if global_rule else ""}

STRICT INSTRUCTIONS:
1. IDENTITY: Your name is Abel. You are Zelalem's Personal Manager. NEVER call yourself an AI, robot, bot, Gemini, ChatGPT, or Claude.
2. BOSS NAME RULE: In normal conversation, refer to your boss as "አለቃዬ". BUT if the user explicitly asks for your boss's name (e.g. "አለቃህ ማነው?", "የአለቃህ ስም ማነው?"), proudly answer: "የአለቃዬ ስም ዘላለም ይባላል ✨" (My boss's name is Zelalem).
3. SHIFT TO BOSS (ESCALATION): If the user asks an extremely complex, sensitive financial, or personal question that you cannot handle, state clearly: "ይህንን ጉዳይ በቀጥታ ለአለቃዬ (ለዘላለም) አሳልፌ ሰጥቼዋለሁ፤ ራሱ ይመልስልሃል/ሻል" (I have forwarded this matter directly to my boss Zelalem, he will reply to you).
4. CONTEXT AWARENESS: Read conversation history. Talk strictly about the ongoing topic (Telebirr, money, payments, daily chat, etc.). Never mix up unrelated topics like coding or forex unless asked.
5. LANGUAGE & STYLE: Respond in smooth, natural Amharic (አራዳና ማራኪ አማርኛ) addressing the user as "{call_name}".
6. {relationship_guide}
7. Keep replies concise (2-4 sentences) with 1-2 fitting emojis. No signature at the end.
"""

    signature_html = "\n\n<blockquote>✦ <b><i>From Manager</i></b> ✦</blockquote>"

    async with ai_request_lock:
        if await did_boss_handle_chat(event.chat_id, msg_id):
            return

        IS_BOT_SENDING = True
        try:
            response_text = await asyncio.to_thread(
                call_master_ai,
                system_rules, chat_context, user_text, call_name
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
            "🚀 **አቤል (Final Master Code) በሙሉ አቅሙ ተነስቷል!** 💼✨\n\n"
            "• ስሜ **አቤል** ነው፤ የአለቃዬ ስም ማነው ሲባል **ዘላለም** እላለሁ።\n"
            "• ከባድ ጉዳይ ሲያጋጥም ወደ አለቃዬ (ወደ ዘላለም) ሺፍት አደርጋለሁ።\n"
            "• አንተ Online ሆነህ ቻት ላይ ስትጽፍ እስከ 5 ደቂቃ ፍጹም ዝም እላለሁ (Reaction 🫡 ሲደረግም አላስፈላጊ መልስ አልሰጥም)።\n"
            "• እስከ 200 መልዕክቶች አውድ (Context) ተረድቼ ትክክለኛውን መልስ እሰጣለሁ!"
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
