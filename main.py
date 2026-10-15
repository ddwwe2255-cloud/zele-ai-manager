import os
import asyncio
import json
import time
import html
import requests
from threading import Thread
from flask import Flask
from telethon import TelegramClient, events
from telethon.tl.functions.account import UpdateStatusRequest
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError
from datetime import datetime, timezone
import pytz
from dotenv import load_dotenv

# ==========================================
# 1. Environment Variables
# ==========================================
load_dotenv()

API_ID = int(os.getenv('API_ID', 32161103))
API_HASH = os.getenv('API_HASH', '953aae804429e8411062310048d0614c')
SESSION_STRING = os.getenv('SESSION_STRING', '')
BOT_TOKEN = os.getenv('BOT_TOKEN', '8645660799:AAFijTW2saTn4PyLEUmolm9r3__kmVoA8BA')
MY_USERNAME = os.getenv('MY_USERNAME', '@zele1216')

raw_keys = os.getenv('GEMINI_API_KEYS', '')
GEMINI_KEYS = [k.strip() for k in raw_keys.split(',') if k.strip()]

# በ curl ትዕዛዝህ መሰረት የሚሰሩት ሞዴሎች
AI_MODELS = ['gemini-flash-latest', 'gemini-2.5-flash', 'gemini-2.0-flash']

# ==========================================
# 2. Memory & Settings
# ==========================================
MEMORY_FILE = 'memory.json'
memory_lock = asyncio.Lock()
LAST_BOSS_ACTION_TIME = 0
BOT_SENT_IDS = set()

DEFAULT_PERSONA = (
    "You are the authentic, highly intelligent, courteous, and professional Personal AI Manager of your boss. "
    "You respond directly, naturally, and warmly without giving repetitive or robotic disclaimers."
)

def _load_memory():
    default_data = {
        "status": "online",
        "personality": DEFAULT_PERSONA,
        "missed": [],
        "processed_ids": [],
        "fallback_timestamps": {}
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
        if len(data.get("processed_ids", [])) > 500:
            data["processed_ids"] = data["processed_ids"][-500:]
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
    return "AI MANAGER is Running 24/7!", 200

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

# ==========================================
# 5. CONTROL BOT (@Manage1216bot)
# ==========================================
@bot_client.on(events.NewMessage(pattern='/start'))
async def bot_start(event):
    await event.reply(
        "👋 **ሰላም አለቃዬ! የ AI Manager ዝግጁ ነው።** 🤖✨\n\n"
        "⚙️ **መቆጣጠሪያ ትዕዛዞች:**\n"
        "🔴 `/away` - ቦቱ ወዲያውኑ እንዲመልስ\n"
        "🟢 `/online` - አንተ በቻት ላይ ንቁ ስትሆን 2 ደቂቃ እንዲጠብቅ\n"
        "🔄 `/reset_persona` - ፀባዩን ወደ ንጹህ ፕሮፌሽናል ለመመለስ\n"
        "📊 `/report` - ያልተመለሱ መልዕክቶችን ለማየት"
    )

@bot_client.on(events.NewMessage(pattern='/away'))
async def set_away(event):
    async with memory_lock:
        mem = _load_memory()
        mem["status"] = "away"
        _save_memory(mem)
    await event.reply("🔴 ሁኔታህ ወደ **AWAY** ተቀይሯል።")

@bot_client.on(events.NewMessage(pattern='/online'))
async def set_online(event):
    async with memory_lock:
        mem = _load_memory()
        mem["status"] = "online"
        _save_memory(mem)
    await event.reply("🟢 ሁኔታህ ወደ **ONLINE** ተመልሷል።")

@bot_client.on(events.NewMessage(pattern='/reset_persona'))
async def reset_persona(event):
    async with memory_lock:
        mem = _load_memory()
        mem["personality"] = DEFAULT_PERSONA
        _save_memory(mem)
    await event.reply("✅ የ AI ፀባይ ተስተካክሏል!")

@bot_client.on(events.NewMessage(pattern='/report'))
async def get_report(event):
    async with memory_lock:
        mem = _load_memory()
        missed = mem.get("missed", [])
    if not missed:
        await event.reply("📭 በአሁኑ ሰዓት አዲስ ሪፖርት የለም።")
        return
    text = "📊 **ያልታዩ መልዕክቶች:**\n\n"
    for m in missed:
        text += f"👤 **{m['name']}**: {m['text']}\n"
    await event.reply(text[:4000])
    async with memory_lock:
        mem = _load_memory()
        mem["missed"] = []
        _save_memory(mem)

# ==========================================
# 6. ትክክለኛው የ Google curl አሰራር
# ==========================================
def run_ai_request(api_key, model_name, prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
    headers = {
        "Content-Type": "application/json",
        "X-goog-api-key": api_key.strip()
    }
    payload = {
        "contents": [
            {
                "parts": [{"text": prompt}]
            }
        ]
    }
    res = requests.post(url, headers=headers, json=payload, timeout=12)
    if res.status_code == 200:
        data = res.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
    raise Exception(f"API Error {res.status_code}: {res.text}")

# ==========================================
# 7. USERBOT CORE
# ==========================================
def is_bot_message(msg):
    if not msg:
        return False
    if msg.id in BOT_SENT_IDS:
        return True
    txt = msg.text or ""
    return "From Manager" in txt or "𝙁𝙧𝙤𝙢 𝙈𝙖𝙣𝙖𝙜𝙚𝙧" in txt

def has_amharic(text):
    """መልዕክቱ ውስጥ የአማርኛ ፊደል መኖሩን ያረጋግጣል"""
    return any(0x1200 <= ord(c) <= 0x137F for c in text)

@user_client.on(events.NewMessage(outgoing=True))
async def track_boss_activity(event):
    global LAST_BOSS_ACTION_TIME
    if not is_bot_message(event.message):
        LAST_BOSS_ACTION_TIME = time.time()

@user_client.on(events.NewMessage(incoming=True))
async def auto_manager(event):
    global MY_USER_ID, LAST_BOSS_ACTION_TIME
    if not event.is_private:
        return
    
    sender = await event.get_sender()
    if sender is None or getattr(sender, 'bot', False):
        return
    
    sender_id = str(sender.id)
    msg_id = event.id
    sender_name = getattr(sender, 'first_name', "ጓደኛ") or "ጓደኛ"

    async with memory_lock:
        mem = _load_memory()
        if msg_id in mem.get("processed_ids", []):
            return
        current_status = mem.get("status", "online")

    try:
        await event.mark_read()
    except Exception:
        pass

    # አንተ ራስህ እየጻፍክ ከነበረ ብቻ 2 ደቂቃ ይጠብቃል፣ ካልሆነ በ 2 ሰከንድ ይመልሳል
    boss_is_actively_chatting = (time.time() - LAST_BOSS_ACTION_TIME) < 120

    if current_status == "online" and boss_is_actively_chatting:
        await asyncio.sleep(120)
        try:
            after_msgs = await user_client.get_messages(event.chat_id, limit=5)
            if any(m.out and not is_bot_message(m) and m.id > msg_id for m in after_msgs if m):
                return
        except Exception:
            pass
    else:
        await asyncio.sleep(2)

    async with memory_lock:
        mem = _load_memory()
        if msg_id in mem.get("processed_ids", []):
            return
        persona = mem.get("personality", DEFAULT_PERSONA)
        mem.setdefault("processed_ids", []).append(msg_id)
        _save_memory(mem)

    user_text = event.text or "[ፎቶ/ሚዲያ]"
    short_msg = (user_text[:40] + '..') if len(user_text) > 40 else user_text

    async with memory_lock:
        mem = _load_memory()
        mem.setdefault("missed", []).append({"name": sender_name, "text": short_msg})
        _save_memory(mem)

    # ያለፉትን 60 መልዕክቶች ማንበብ
    chat_context = ""
    try:
        hist = await user_client.get_messages(event.chat_id, limit=60)
        context_lines = []
        for m in reversed(hist):
            if not m or not m.text:
                continue
            speaker = "AI ማኔጀር" if is_bot_message(m) else ("አለቃዬ" if m.out else sender_name)
            clean_txt = m.text.replace("✦ From Manager ✦", "").replace("✦ 𝙁𝙧𝙤𝙢 𝙈𝙖𝙣𝙖𝙜𝙚𝙧 ✦", "").strip()
            if clean_txt:
                context_lines.append(f"{speaker}: {clean_txt}")
        chat_context = "\n".join(context_lines[-60:])
    except Exception as e:
        print(f"History scan error: {e}")

    is_amharic_user = has_amharic(user_text) or has_amharic(chat_context)

    prompt = f"""
{persona}

[Chat History (Last 60 Messages)]
{chat_context}

[Current Message]
Sender: {sender_name}
Message: "{user_text}"

STRICT INSTRUCTIONS:
1. LANGUAGE:
   - User language detected: {"AMHARIC" if is_amharic_user else "ENGLISH"}.
   - If AMHARIC: Reply 100% in natural Amharic. Call your boss ONLY "አለቃዬ".
   - If ENGLISH: Reply 100% in fluent English. Call your boss ONLY "my boss". NEVER mix English and Amharic.
2. ANSWER DIRECTLY:
   - If the user asks "አንተ ማነህ?" (Who are you?): Explain politely that you are his Personal AI Manager managing his chat while he is away.
   - If the user asks why he missed school/work ("ትምህርት ለምን ቀረህ?"): State that he had an important personal matter today and is currently away, and that you will deliver any message to him as soon as he is online.
   - Do NOT output repetitive templates or endless disclaimers. Speak like a real assistant.
3. SIGNATURE:
   - Do NOT add any signature. The system will append it.
"""

    response_text = None
    success = False

    for key in GEMINI_KEYS:
        if success:
            break
        for model in AI_MODELS:
            try:
                response_text = await asyncio.to_thread(run_ai_request, key, model, prompt)
                if response_text and response_text.strip():
                    success = True
                    break
            except Exception as e:
                print(f"Failed {model} on key: {e}")
                continue

    signature_html = "\n\n<blockquote>✦ <b><i>From Manager</i></b> ✦</blockquote>"

    if not success or not response_text:
        # AI ካልመለሰ Cooldown ተጠቅሞ አንድ ጊዜ ብቻ አጭር ምላሽ ይሰጣል
        async with memory_lock:
            mem = _load_memory()
            fallback_timestamps = mem.get("fallback_timestamps", {})
            last_sent = fallback_timestamps.get(sender_id, 0)

            if time.time() - last_sent >= 1800:  # በ 30 ደቂቃ አንዴ ብቻ
                if is_amharic_user:
                    offline_body = f"ሰላም {sender_name}! 😊\nአለቃዬ አሁን ስራ ስለበዛበት ለጊዜው Online የለም 💼። መልዕክትህን አስቀምጬለታለሁ፤ ልክ እንደመጣ አይቶ ራሱ ይመልስልሃል 🙏"
                else:
                    offline_body = f"Hello {sender_name}! 😊\nMy boss is currently away and not online right now 💼. I have noted your message and will notify him as soon as he is back! 🙏"

                final_msg = f"{html.escape(offline_body)}{signature_html}"
                try:
                    sent = await event.reply(final_msg, parse_mode='html')
                    if sent:
                        BOT_SENT_IDS.add(sent.id)
                except Exception:
                    sent = await event.reply(offline_body + "\n\n✦ From Manager ✦")
                    if sent:
                        BOT_SENT_IDS.add(sent.id)

                fallback_timestamps[sender_id] = time.time()
                mem["fallback_timestamps"] = fallback_timestamps
                _save_memory(mem)
    else:
        safe_ai_text = html.escape(response_text.strip())
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
            sent = await event.reply(response_text.strip() + "\n\n✦ From Manager ✦")
            if sent:
                BOT_SENT_IDS.add(sent.id)

        try:
            await bot_client.send_message(MY_USER_ID, f"🔔 **AI ምላሽ ሰጥቷል!**\n👤 ከ: {sender_name}\n💬: {short_msg}\n🤖: {response_text[:80]}...")
        except Exception:
            pass

# ==========================================
# 8. Always Online Loop & Startup
# ==========================================
async def keep_online():
    while True:
        try:
            await user_client(UpdateStatusRequest(offline=False))
        except Exception:
            pass
        await asyncio.sleep(60)

async def main():
    global MY_USER_ID
    print("Zelalem super-smart coder started")
    await user_client.start()
    MY_USER_ID = (await user_client.get_me()).id

    await bot_client.start(bot_token=BOT_TOKEN)
    asyncio.create_task(keep_online())

    try:
        await bot_client.send_message(
            MY_USER_ID,
            "🚀 **AI MANAGER ሙሉ በሙሉ ተስተካክሎ ተነስቷል!** ✨"
        )
    except Exception:
        pass

    await asyncio.gather(
        user_client.run_until_disconnected(),
        bot_client.run_until_disconnected()
    )

if __name__ == '__main__':
    loop.run_until_complete(main())
