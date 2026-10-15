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
from google import genai
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

raw_keys = os.getenv('GEMINI_API_KEYS', '')
GEMINI_KEYS = [k.strip() for k in raw_keys.split(',') if k.strip()]

AI_MODELS = ['gemini-flash-latest', 'gemini-2.5-flash', 'gemini-2.0-flash']

# ==========================================
# 2. Memory & Activity Tracker
# ==========================================
MEMORY_FILE = 'memory.json'
memory_lock = asyncio.Lock()
LAST_BOSS_ACTIVE_TIME = 0

def _load_memory():
    default_data = {
        "status": "online",
        "personality": "አንተ ትሁት፣ ብልህ እና ፕሮፌሽናል የአለቃ ፈዬ AI ማኔጀር ነህ።",
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
    return "AI MANAGER is Running 24/7 on the Cloud!", 200

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
        "👋 **ሰላም አለቃ ፈዬ! የ Cloud AI Manager ዝግጁ ነው።**\n\n"
        "⚙️ **መቆጣጠሪያ ትዕዛዞች:**\n"
        "🔴 `/away` - እኔ የለሁም (ቦቱ 2 ደቂቃ ሳይጠብቅ ወዲያው እንዲመልስ)\n"
        "🟢 `/online` - መደበኛ ሁኔታ (አክቲቭ ስትሆን 2 ደቂቃ ይጠብቅሃል)\n"
        "📝 `/set_persona [ጽሁፍ]` - የ AIውን ፀባይ ለመቀየር\n"
        "📊 `/report` - ያልተመለሱ መልዕክቶችን ሪፖርት ለማየት"
    )

@bot_client.on(events.NewMessage(pattern='/away'))
async def set_away(event):
    async with memory_lock:
        mem = _load_memory()
        mem["status"] = "away"
        _save_memory(mem)
    await event.reply("🔴 ሁኔታህ ወደ **AWAY** ተቀይሯል። ቦቱ 2 ደቂቃ ሳይጠብቅ ወዲያውኑ መልስ ይሰጣል።")

@bot_client.on(events.NewMessage(pattern='/online'))
async def set_online(event):
    async with memory_lock:
        mem = _load_memory()
        mem["status"] = "online"
        _save_memory(mem)
    await event.reply("🟢 ሁኔታህ ወደ **ONLINE** ተመልሷል። አንተ አክቲቭ ስትሆን 2 ደቂቃ ይጠብቅሃል።")

@bot_client.on(events.NewMessage(pattern=r'/set_persona (.*)'))
async def set_persona(event):
    new_persona = event.pattern_match.group(1)
    async with memory_lock:
        mem = _load_memory()
        mem["personality"] = new_persona
        _save_memory(mem)
    await event.reply(f"✅ የ AI ፀባይ ተቀይሯል:\n_{new_persona}_")

@bot_client.on(events.NewMessage(pattern='/report'))
async def get_report(event):
    async with memory_lock:
        mem = _load_memory()
        missed = mem.get("missed", [])
    if not missed:
        await event.reply("📭 በአሁኑ ሰዓት አዲስ ሪፖርት የለም።")
        return
    text = "📊 **በሌለህበት ሰዓት የመጡ መልዕክቶች:**\n\n"
    for m in missed:
        text += f"👤 **{m['name']}**: {m['text']}\n"
    await event.reply(text[:4000])
    async with memory_lock:
        mem = _load_memory()
        mem["missed"] = []
        _save_memory(mem)

# ==========================================
# 6. AI Request Handler (REST + SDK Backup)
# ==========================================
def run_ai_request(api_key, model_name, prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
    headers = {
        "Content-Type": "application/json",
        "X-goog-api-key": api_key.strip()
    }
    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }
    res = requests.post(url, headers=headers, json=payload, timeout=20)
    if res.status_code == 200:
        data = res.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
    
    client_ai = genai.Client(api_key=api_key.strip())
    sdk_res = client_ai.models.generate_content(model=model_name, contents=prompt)
    if sdk_res and sdk_res.text:
        return sdk_res.text
    
    raise Exception(f"HTTP {res.status_code}: {res.text}")

# ==========================================
# 7. USERBOT CORE
# ==========================================
@user_client.on(events.NewMessage(outgoing=True))
async def track_boss_activity(event):
    global LAST_BOSS_ACTIVE_TIME
    if event.text and "𝙁𝙧𝙤𝙢 𝙈𝙖𝙣𝙖𝙜𝙚𝙧" not in event.text:
        LAST_BOSS_ACTIVE_TIME = time.time()

@user_client.on(events.NewMessage(incoming=True))
async def auto_manager(event):
    global MY_USER_ID, LAST_BOSS_ACTIVE_TIME
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

    # አንተ Online (Active) ከሆንክ 2 ደቂቃ ይጠብቃል፣ ካልሆንክ ወዲያው ይመልሳል
    now_utc = datetime.now(timezone.utc)
    boss_recently_active = (time.time() - LAST_BOSS_ACTIVE_TIME) < 300

    try:
        recent_msgs = await user_client.get_messages(event.chat_id, limit=6)
        if any(m.out and (now_utc - m.date).total_seconds() < 300 for m in recent_msgs if m):
            boss_recently_active = True
    except Exception:
        pass

    if current_status == "online" and boss_recently_active:
        await asyncio.sleep(120)
    else:
        await asyncio.sleep(2)

    try:
        history = await user_client.get_messages(event.chat_id, limit=10)
        if any(msg.out and msg.id > msg_id for msg in history if msg):
            return
    except FloodWaitError as e:
        await asyncio.sleep(e.seconds)
        return
    except Exception:
        pass

    async with memory_lock:
        mem = _load_memory()
        if msg_id in mem.get("processed_ids", []):
            return
        current_status = mem.get("status", "online")
        persona = mem.get("personality", "አንተ ትሁት የአለቃ ፈዬ AI ማኔጀር ነህ።")
        mem.setdefault("processed_ids", []).append(msg_id)
        _save_memory(mem)

    user_text = event.text or "[ፎቶ/ሚዲያ ተልኳል]"
    short_msg = (user_text[:40] + '..') if len(user_text) > 40 else user_text

    async with memory_lock:
        mem = _load_memory()
        mem.setdefault("missed", []).append({"name": sender_name, "text": short_msg})
        _save_memory(mem)

    try:
        hist = await user_client.get_messages(event.chat_id, limit=6)
        chat_context = "\n".join([f"{'አለቃ ፈዬ' if m.out else sender_name}: {m.text}" for m in reversed(hist) if m and m.text])
    except Exception:
        chat_context = ""

    hour = datetime.now(ethiopia_tz).hour
    is_night = hour >= 22 or hour < 6

    if is_night:
        status_note = "አለቃ ፈዬ አሁን ተኝቷል (ሌሊት ስለሆነ)፤ ጠዋት ሲነሳ መልዕክቱን እንደምታደርስለት ንገረው።"
    elif current_status == "away" or not boss_recently_active:
        status_note = "አለቃ ፈዬ ለጊዜው Online ስላልሆነ፣ መልዕክቱን መዝግበህ እንደያዝክለትና ሲመለስ እንደሚያናግረው በትህትና ንገረው።"
    else:
        status_note = "አለቃ ፈዬ አሁን ትንሽ ስራ ስለበዛበት እኔ የሱ AI ማኔጀር እያነጋገርኩህ ነው በለው።"

    prompt = f"""
{persona}

[የቀድሞ ውይይት]
{chat_context}

አሁን የመጣ መልዕክት ከ {sender_name}: "{user_text}"

መመሪያዎች:
1. ተፈጥሯዊ፣ ተግባቢ እና አጭር ምላሽ በተጠቃሚው ቋንቋ (አማርኛ ወይም እንግሊዝኛ) ስጥ።
2. አለቃህን ሁሌም 'አለቃ ፈዬ' ወይም 'አለቃዬ' ብቻ ብለህ ጥራው (ሌላ ስም በፍጹም አትጥራ)።
3. {status_note}
4. በመልስህ መጨረሻ ላይ ፊርማ አትጨምር።
"""

    response_text = None
    success = False

    for key in GEMINI_KEYS:
        if success:
            break
        for model in AI_MODELS:
            try:
                response_text = await asyncio.to_thread(run_ai_request, key, model, prompt)
                if response_text:
                    success = True
                    break
            except Exception as e:
                print(f"Key/Model Retry ({model}): {e}")
                continue

    signature_html = "\n\n<blockquote>✦ <b><i>𝙁𝙧𝙤𝙢 𝙈𝙖𝙣𝙖𝙜𝙚𝙧</i></b> ✦</blockquote>"

    if not success or not response_text:
        async with memory_lock:
            mem = _load_memory()
            fallback_timestamps = mem.get("fallback_timestamps", {})
            last_sent = fallback_timestamps.get(sender_id, 0)

            if time.time() - last_sent >= 3600:
                offline_body = (
                    "👋 ሰላም! 😊\n"
                    "🤖 እኔ AI Manager ነኝ። አለቃዬ ለጊዜው Online ስላልሆነ፣ መልዕክት ካለዎት እዚህ መተው ይችላሉ። "
                    "ሲመለስ አሳውቀዋለሁ፤ እሱም በራሱ ይመልስልዎታል። 💬\n\n"
                    "🙏 መልካም ጊዜ!"
                )
                final_offline_msg = f"{html.escape(offline_body)}{signature_html}"

                try:
                    await event.reply(final_offline_msg, parse_mode='html')
                except Exception:
                    await event.reply(offline_body + "\n\n✦ 𝙁𝙧𝙤𝙢 𝙈𝙖𝙣𝙖𝙜𝙚𝙧 ✦")

                fallback_timestamps[sender_id] = time.time()
                mem["fallback_timestamps"] = fallback_timestamps
                _save_memory(mem)

                try:
                    await bot_client.send_message(MY_USER_ID, f"🔔 **Offline ምላሽ ተሰጥቷል!**\n👤 ከ: {sender_name}\n💬: {short_msg}")
                except Exception:
                    pass
    else:
        safe_ai_text = html.escape(response_text.strip())
        final_reply = f"{safe_ai_text}{signature_html}"

        try:
            await event.reply(final_reply, parse_mode='html')
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
            await event.reply(final_reply, parse_mode='html')
        except Exception:
            await event.reply(response_text.strip() + "\n\n✦ 𝙁𝙧𝙤𝙢 𝙈𝙖𝙣𝙖𝙜𝙚𝙧 ✦")

        try:
            await bot_client.send_message(MY_USER_ID, f"🔔 **AI ምላሽ ሰጥቷል!**\n👤 ከ: {sender_name}\n💬: {short_msg}\n🤖: {response_text[:60]}...")
        except Exception:
            pass

        async with memory_lock:
            mem = _load_memory()
            if sender_id in mem.get("fallback_timestamps", {}):
                del mem["fallback_timestamps"][sender_id]
                _save_memory(mem)

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
        await bot_client.send_message(MY_USER_ID, "🚀 **AI MANAGER ተነስቷል!**\n`/start` ብለህ ሞክረው።")
    except Exception:
        pass

    await asyncio.gather(
        user_client.run_until_disconnected(),
        bot_client.run_until_disconnected()
    )

if __name__ == '__main__':
    loop.run_until_complete(main())
