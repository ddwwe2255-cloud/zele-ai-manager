import os
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
from telethon.tl.functions.account import UpdateStatusRequest
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError
from datetime import datetime, timezone
import pytz
from dotenv import load_dotenv

# ==========================================
# 1. Environment Variables (ከ Render የሚነበቡ)
# ==========================================
load_dotenv()

API_ID = int(os.getenv('API_ID', 0))
API_HASH = os.getenv('API_HASH', '')
SESSION_STRING = os.getenv('SESSION_STRING', '')
BOT_TOKEN = os.getenv('BOT_TOKEN', '')
MY_USERNAME = os.getenv('MY_USERNAME', '@zele1216')

# ==========================================
# 2. Memory & State Management
# ==========================================
MEMORY_FILE = 'memory.json'
memory_lock = asyncio.Lock()
LAST_BOSS_CHAT_TIME = {}
BOT_SENT_IDS = set()
IS_BOT_SENDING = False

DEFAULT_PERSONA = (
    "You are a highly intelligent, polite, and capable Personal AI Manager built and programmed by your boss. "
    "You have deep knowledge across all subjects (Agriculture, Forex, Science, Technology, Education, General Knowledge) "
    "and you converse naturally like a smart human executive assistant."
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
# 3. Flask Server (UptimeRobot 24/7 እንዲያነቃው)
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
        "👋 **ሰላም አለቃዬ! የ AI Manager (Non-Stop AI) ዝግጁ ነው።** 🤖✨\n\n"
        "⚙️ **መቆጣጠሪያ ትዕዛዞች:**\n"
        "🔴 `/away` - ቦቱ ሳይጠብቅ ወዲያውኑ እንዲመልስ\n"
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
    await event.reply("🔴 ሁኔታህ ወደ **AWAY** ተቀይሯል። ቦቱ ወዲያውኑ ይመልሳል! ⚡")

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
# 6. Non-Stop Keyless AI Engine (With Smart Retry & Backoff)
# ==========================================
BROWSER_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*"
}

def is_valid_ai_reply(ans):
    if not ans or len(ans) < 2:
        return False
    bad_markers = ["<!DOCTYPE", "<html", "Rate limit", "Too Many Requests", "502 Bad Gateway", "Internal Server Error"]
    return not any(b.lower() in ans.lower() for b in bad_markers)

def call_keyless_ai_sync(system_rules, user_prompt):
    messages = [
        {"role": "system", "content": system_rules},
        {"role": "user", "content": user_prompt}
    ]
    models = ["openai", "openai-fast", "mistral", "deepseek"]

    # እስከ 3 ዙር ድረስ ትንሽ እያረፈ (Backoff) ይሞክራል — ወደ Offline እንዳይወድቅ!
    for attempt in range(3):
        if attempt > 0:
            time.sleep(2.0 * attempt)  # ተከታታይ መልዕክት ሲመጣ ሰርቨሩ እንዳያግደው 2-4 ሰከንድ ይጠብቃል

        for model_name in models:
            seed_val = random.randint(1000, 999999)

            # Method A: OpenAI-Compatible Endpoint
            try:
                res = requests.post(
                    "https://text.pollinations.ai/openai",
                    headers=BROWSER_HEADERS,
                    json={
                        "model": model_name,
                        "messages": messages,
                        "seed": seed_val,
                        "temperature": 0.7
                    },
                    timeout=20
                )
                if res.status_code == 200:
                    data = res.json()
                    ans = data["choices"][0]["message"]["content"].strip()
                    if is_valid_ai_reply(ans):
                        return ans
            except Exception:
                pass

            # Method B: Direct Root POST Endpoint
            try:
                res = requests.post(
                    "https://text.pollinations.ai/",
                    headers=BROWSER_HEADERS,
                    json={
                        "messages": messages,
                        "model": model_name,
                        "seed": seed_val,
                        "private": True
                    },
                    timeout=20
                )
                if res.status_code == 200:
                    ans = res.text.strip()
                    if is_valid_ai_reply(ans):
                        return ans
            except Exception:
                pass

            # በሞዴሎች መካከል 0.5 ሰከንድ እረፍት
            time.sleep(0.5)

        # Method C: Fast GET Endpoint
        try:
            short_q = urllib.parse.quote(f"{system_rules}\n\n{user_prompt}"[:1200])
            seed_val = random.randint(1000, 999999)
            res = requests.get(
                f"https://text.pollinations.ai/{short_q}?model=openai&seed={seed_val}",
                headers=BROWSER_HEADERS,
                timeout=15
            )
            if res.status_code == 200:
                ans = res.text.strip()
                if is_valid_ai_reply(ans):
                    return ans
        except Exception:
            pass

    raise Exception("All AI retries exhausted")

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

    # አንተ እራስህ በዛ ቻት ላይ ባለፉት 2 ደቂቃዎች ጽፈህ ከነበረ ብቻ 2 ደቂቃ ይጠብቃል
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

    # ያለፉትን 25 መልዕክቶች ማንበብ
    chat_context = ""
    try:
        hist = await user_client.get_messages(event.chat_id, limit=25)
        context_lines = []
        for m in reversed(hist):
            if not m or not m.text:
                continue
            speaker = "AI Manager" if is_bot_message(m) else ("Boss" if m.out else sender_name)
            clean_txt = m.text.replace("✦ From Manager ✦", "").replace("✦ 𝙁𝙧𝙤𝙢 𝙈𝙖𝙣𝙖𝙜𝙚𝙧 ✦", "").strip()
            if clean_txt:
                context_lines.append(f"{speaker}: {clean_txt}")
        chat_context = "\n".join(context_lines[-25:])
    except Exception:
        pass

    is_amharic_msg = has_amharic(user_text) or (not any(c.isalpha() for c in user_text) and has_amharic(chat_context))

    system_rules = f"""
{persona}

CRITICAL RULES:
1. LANGUAGE:
   - Reply in {"100% AMHARIC (አማርኛ)" if is_amharic_msg else "100% ENGLISH"}.
   - In Amharic, refer to your boss ONLY as "አለቃዬ".
   - In English, refer to your boss ONLY as "my boss".
   - NEVER mix languages and NEVER mention your boss's real name.

2. ANSWER EVERY QUESTION SMARTLY & DIRECTLY:
   - NEVER send generic "my boss is offline" templates when the user asks a question!
   - If the user asks about ANY knowledge topic (e.g., Agriculture, Forex, science, math, technology, advice):
     Explain and answer the question directly, clearly, and intelligently!
   - If the user asks "ማነው የሰራህ?" / "Who made you?":
     Proudly state that your boss ("አለቃዬ" / "my boss") built and programmed you himself!
   - If the user asks "አንተ ማነህ?" / "Who are you?":
     Politely say you are the Personal AI Manager of your boss ("የአለቃዬ የግል AI ማኔጀር"), assisting while he is away.
   - If the user asks a personal question about your boss (e.g., "ዛሬ ለምን ትምህርት ቀረህ?", "የት ነህ?"):
     Politely explain that your boss had an urgent personal matter today and is currently away, and offer to pass along any message to him.

3. STYLE:
   - Match the user's gender from chat history (አንተ/ወንድሜ for male, አንቺ/እህቴ for female).
   - Keep it natural, polite, and concise (2 to 4 sentences) with tasteful emojis (😊, 🙏, ✨).
   - Do NOT add any signature at the end.
"""

    user_prompt = f"[Recent Chat]\n{chat_context}\n\n[User ({sender_name}) says]: {user_text}"
    signature_html = "\n\n<blockquote>✦ <b><i>From Manager</i></b> ✦</blockquote>"

    IS_BOT_SENDING = True
    try:
        try:
            response_text = await asyncio.to_thread(call_keyless_ai_sync, system_rules, user_prompt)
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
                await bot_client.send_message(MY_USER_ID, f"🔔 **AI ምላሽ ሰጥቷል!**\n👤 ከ: {sender_name}\n💬: {short_msg}\n🤖: {clean_reply[:120]}...")
            except Exception:
                pass

        except Exception:
            async with memory_lock:
                mem = _load_memory()
                fallback_timestamps = mem.get("fallback_timestamps", {})
                last_sent = fallback_timestamps.get(sender_id, 0)

                if time.time() - last_sent >= 3600:
                    if is_amharic_msg:
                        offline_body = f"ሰላም {sender_name}! 😊\nአለቃዬ አሁን ለጊዜው Online የለም 💼። መልዕክትህን አስቀምጬለታለሁ፤ ልክ እንደመጣ አይቶ ራሱ ይመልስልሃል 🙏"
                    else:
                        offline_body = f"Hello {sender_name}! 😊\nMy boss is currently away right now 💼. I have noted your message and will notify him as soon as he is back! 🙏"

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
            "🚀 **AI MANAGER (Smart Auto-Retry) ተነስቷል!** ✨\n"
            "• ተከታታይ ጥያቄዎች ሲላኩ ወደ Offline ሳይወድቅ ታግሶ በ AI ይመልሳል!"
        )
    except Exception:
        pass

    await asyncio.gather(
        user_client.run_until_disconnected(),
        bot_client.run_until_disconnected()
    )

if __name__ == '__main__':
    loop.run_until_complete(main())
