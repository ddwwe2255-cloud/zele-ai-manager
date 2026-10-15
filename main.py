import os
import asyncio
import json
import time
import html
import urllib.parse
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

AI_MODELS = ['gemini-flash-latest', 'gemini-2.0-flash']
DEAD_KEYS = set()  # የሞቱ ቁልፎችን ድጋሚ እየሞከረ ጊዜ እንዳያጠፋ ይይዛቸዋል

# ==========================================
# 2. Memory & Settings
# ==========================================
MEMORY_FILE = 'memory.json'
memory_lock = asyncio.Lock()
LAST_BOSS_CHAT_TIME = {}
BOT_SENT_IDS = set()
IS_BOT_SENDING = False  # ቦቱ ራሱ ሲልክ አለቃው እንደጻፈ እንዳይቆጥር

DEFAULT_PERSONA = (
    "You are the intelligent, courteous, and professional Personal AI Manager of your boss. "
    "You have full knowledge of all general topics (such as Forex trading, Agriculture, Science, Education, Technology, etc.) "
    "and you answer users' questions directly, smartly, and concisely."
)

def _load_memory():
    default_data = {
        "status": "online",
        "personality": DEFAULT_PERSONA,
        "missed": [],
        "processed_ids": []
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
CONTROL_BOT_ID = None

# ==========================================
# 5. CONTROL BOT (@Manage1216bot)
# ==========================================
@bot_client.on(events.NewMessage(pattern='/start'))
async def bot_start(event):
    await event.reply(
        "👋 **ሰላም አለቃዬ! የ AI Manager ዝግጁ ነው።** 🤖✨\n\n"
        "⚙️ **መቆጣጠሪያ ትዕዛዞች:**\n"
        "🔴 `/away` - ቦቱ ሳይጠብቅ ወዲያውኑ (በ2 ሰከንድ) እንዲመልስ\n"
        "🟢 `/online` - አንተ በቻት ላይ ስትጽፍ 2 ደቂቃ እንዲጠብቅ\n"
        "🔄 `/reset_persona` - ፀባዩን ወደ ንጹህ ፕሮፌሽናል ለመመለስ\n"
        "📊 `/report` - ያልተመለሱ መልዕክቶችን ለማየት"
    )

@bot_client.on(events.NewMessage(pattern='/away'))
async def set_away(event):
    async with memory_lock:
        mem = _load_memory()
        mem["status"] = "away"
        _save_memory(mem)
    await event.reply("🔴 ሁኔታህ ወደ **AWAY** ተቀይሯል። አሁን በ 2 ሰከንድ ውስጥ ይመልሳል! ⚡")

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
# 6. Smart AI Engines (Gemini + Instant Keyless AI)
# ==========================================
def run_gemini_request(api_key, model_name, prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
    headers = {
        "Content-Type": "application/json",
        "X-goog-api-key": api_key.strip()
    }
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    res = requests.post(url, headers=headers, json=payload, timeout=6)
    if res.status_code == 200:
        data = res.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
    if res.status_code in [400, 401, 403]:
        DEAD_KEYS.add(api_key)  # የሞተውን ቁልፍ መዝግቦ ያስቀרהዋል
        raise ValueError("Invalid/Expired Key")
    raise Exception(f"Status {res.status_code}")

def run_free_backup_ai(system_instruction, user_prompt):
    """ያለ ምንም API Key ማንኛውንም ጥያቄ (Forex, Agriculture, ወዘተ) የሚመልስ ነፃ AI"""
    # 1. POST Method (OpenAI GPT-4o-mini endpoint)
    try:
        url = "https://text.pollinations.ai/"
        headers = {"Content-Type": "application/json"}
        payload = {
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_prompt}
            ],
            "model": "openai",
            "private": True
        }
        res = requests.post(url, headers=headers, json=payload, timeout=12)
        if res.status_code == 200 and res.text.strip():
            return res.text.strip()
    except Exception:
        pass

    # 2. GET Method Backup
    encoded = urllib.parse.quote(f"{system_instruction}\n\n{user_prompt}")
    res_get = requests.get(f"https://text.pollinations.ai/{encoded}?model=openai", timeout=12)
    if res_get.status_code == 200 and res_get.text.strip():
        return res_get.text.strip()

    raise Exception("Free AI temporarily unreachable")

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
    return any(0x1200 <= ord(c) <= 0x137F for c in text)

@user_client.on(events.NewMessage(outgoing=True))
async def track_boss_activity(event):
    global CONTROL_BOT_ID, IS_BOT_SENDING
    # ቦቱ እራሱ መልስ እየላከ ከሆነ እንደ አለቃው አይቆጥረውም!
    if IS_BOT_SENDING or is_bot_message(event.message):
        return
    if CONTROL_BOT_ID and event.chat_id == CONTROL_BOT_ID:
        return
    LAST_BOSS_CHAT_TIME[event.chat_id] = time.time()

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

    # አንተ እራስህ በዛ ቻት ላይ ባለፉት 2 ደቂቃዎች ጽፈህ ከነበረ ብቻ ይጠብቃል
    last_chat_activity = LAST_BOSS_CHAT_TIME.get(event.chat_id, 0)
    boss_active_in_this_chat = (time.time() - last_chat_activity) < 120

    if current_status == "online" and boss_active_in_this_chat:
        await asyncio.sleep(120)
        try:
            after_msgs = await user_client.get_messages(event.chat_id, limit=5)
            if any(m.out and not is_bot_message(m) and m.id > msg_id for m in after_msgs if m):
                return
        except Exception:
            pass
    else:
        # ምንም ሳይጠብቅ በ 1 ሰከንድ ውስጥ ስራ ይጀምራል!
        await asyncio.sleep(1)

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

    # ያለፉትን 40 መልዕክቶች ማንበብ
    chat_context = ""
    try:
        hist = await user_client.get_messages(event.chat_id, limit=40)
        context_lines = []
        for m in reversed(hist):
            if not m or not m.text:
                continue
            speaker = "AI Manager" if is_bot_message(m) else ("Boss" if m.out else sender_name)
            clean_txt = m.text.replace("✦ From Manager ✦", "").replace("✦ 𝙁𝙧𝙤𝙢 𝙈𝙖𝙣𝙖𝙜𝙚𝙧 ✦", "").strip()
            if clean_txt:
                context_lines.append(f"{speaker}: {clean_txt}")
        chat_context = "\n".join(context_lines[-40:])
    except Exception:
        pass

    is_amharic_msg = has_amharic(user_text)

    system_rules = f"""
{persona}
STRICT RULES:
1. Reply language: {"100% AMHARIC (አማርኛ)" if is_amharic_msg else "100% ENGLISH"}.
2. In Amharic, ALWAYS call your boss "አለቃዬ". In English, ALWAYS call your boss "my boss". NEVER mix languages and NEVER say his real name.
3. GENERAL QUESTIONS: If the user asks about ANY knowledge topic (e.g., Forex, Agriculture, Science, Math, History, Advice), explain it clearly, smartly, and accurately!
4. PERSONAL QUESTIONS:
   - If asked "አንተ ማነህ?" (Who are you?): Say "ሰላም! 😊 እኔ የአለቃዬ የግል AI ማኔጀር ነኝ። አለቃዬ አሁን ኦንላይን ስለሌለ እሱን ወክዬ እያነጋገርኩህ ነው፤ ማንኛውንም ጥያቄ መጠየቅ ትችላለህ! 🙏"
   - If asked why boss is absent/missed school ("ለምን ትምህርት ቀረህ?"): Say "ሰላም ወንድሜ! 😊 አለቃዬ ዛሬ አስቸኳይ የግል ጉዳይ ስለገጠመው ነው ያልመጣው። ለእሱ የሚተላለፍ መልዕክት ካለህ ንገረኝ ሲመጣ አደርስለታለሁ 🙏"
5. Keep responses natural, polite, and concise (2 to 4 sentences). Do NOT add a signature.
"""

    user_prompt = f"[Recent Chat History]\n{chat_context}\n\n[New Message from {sender_name}]: {user_text}"

    response_text = None
    success = False

    # 1. ህያው የሆኑ የ Gemini ቁልፎች ካሉ ብቻ ይሞክራል (የሞቱትን ወዲያው ይዘላል)
    active_keys = [k for k in GEMINI_KEYS if k not in DEAD_KEYS]
    for key in active_keys:
        if success:
            break
        for model in AI_MODELS:
            try:
                response_text = await asyncio.to_thread(run_gemini_request, key, model, f"{system_rules}\n\n{user_prompt}")
                if response_text and response_text.strip():
                    success = True
                    break
            except ValueError:
                break
            except Exception:
                continue

    # 2. ወዲያውኑ በነፃው Keyless AI ይመልሳል (Forex, Agriculture, ሁሉንም ያውቃል!)
    if not success or not response_text:
        try:
            response_text = await asyncio.to_thread(run_free_backup_ai, system_rules, user_prompt)
            if response_text and response_text.strip():
                success = True
        except Exception as e:
            print(f"Backup AI Error: {e}")

    signature_html = "\n\n<blockquote>✦ <b><i>From Manager</i></b> ✦</blockquote>"

    IS_BOT_SENDING = True
    try:
        if success and response_text:
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
                await bot_client.send_message(MY_USER_ID, f"🔔 **AI ምላሽ ሰጥቷል!**\n👤 ከ: {sender_name}\n💬: {short_msg}\n🤖: {clean_reply[:100]}...")
            except Exception:
                pass
        else:
            if is_amharic_msg:
                offline_body = f"ሰላም {sender_name}! 😊\nአለቃዬ አሁን ለጊዜው Online የለም 💼። መልዕክትህን አስቀምጬለታለሁ፤ ልክ እንደመጣ አይቶ ራሱ ይመልስልሃል 🙏"
            else:
                offline_body = f"Hello {sender_name}! 😊\nMy boss is currently away right now 💼. I have noted your message and will notify him as soon as he is back! 🙏"

            final_msg = f"{html.escape(offline_body)}{signature_html}"
            sent = await event.reply(final_msg, parse_mode='html')
            if sent:
                BOT_SENT_IDS.add(sent.id)
    finally:
        await asyncio.sleep(1)
        IS_BOT_SENDING = False

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
    global MY_USER_ID, CONTROL_BOT_ID
    print("Zelalem super-smart coder started")
    await user_client.start()
    MY_USER_ID = (await user_client.get_me()).id

    await bot_client.start(bot_token=BOT_TOKEN)
    CONTROL_BOT_ID = (await bot_client.get_me()).id
    asyncio.create_task(keep_online())

    try:
        await bot_client.send_message(
            MY_USER_ID,
            "🚀 **AI MANAGER (Fast Keyless AI) ተነስቷል!** ✨\n"
            "• የሞቱ ቁልፎችን በመዝለል በ 2 ሰከንድ ውስጥ ይመልሳል!\n"
            "• Forex፣ Agriculture እና ማንኛውንም ጥያቄ መጠየቅ ትችላለህ።"
        )
    except Exception:
        pass

    await asyncio.gather(
        user_client.run_until_disconnected(),
        bot_client.run_until_disconnected()
    )

if __name__ == '__main__':
    loop.run_until_complete(main())
