import os
import asyncio
import json
import time
import uuid
import html
from threading import Thread
from flask import Flask
from telethon import TelegramClient, events
from telethon.tl.functions.account import UpdateStatusRequest
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError
from google import genai
from google.genai.errors import APIError
from datetime import datetime, timezone
import pytz
from dotenv import load_dotenv

# 1. መረጃዎችን ከ Environment Variables ማንበብ (ክላውድ ላይ ስለሚጫን)
load_dotenv()

API_ID = int(os.getenv('API_ID', 0))
API_HASH = os.getenv('API_HASH', '')
SESSION_STRING = os.getenv('SESSION_STRING', '')
BOT_TOKEN = os.getenv('BOT_TOKEN', '')
MY_USERNAME = os.getenv('MY_USERNAME', '@zele1216')
raw_keys = os.getenv('GEMINI_API_KEYS', '')
GEMINI_KEYS = [k.strip() for k in raw_keys.split(',')] if raw_keys else []

AI_MODELS = ['gemini-2.5-flash', 'gemini-1.5-flash']

# 2. Cloud-Safe Memory Management
MEMORY_FILE = 'memory.json'
memory_lock = asyncio.Lock()

def _load_memory():
    default_data = {"status": "online", "personality": "አንተ ትሁት የ AI ማኔጀር ነህ።", "missed": [], "processed_ids": []}
    if not os.path.exists(MEMORY_FILE):
        return default_data
    try:
        with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            data.setdefault("status", "online")
            data.setdefault("personality", "አንተ ትሁት የ AI ማኔጀር ነህ።")
            data.setdefault("missed", [])
            data.setdefault("processed_ids", [])
            return data
    except:
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

# 3. Flask Server (UptimeRobot እንዲያገኘው)
app = Flask(__name__)
@app.route('/')
def home():
    return "AI MANAGER is Running on the Cloud!", 200

def run_server():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)), use_reloader=False)

Thread(target=run_server, daemon=True).start()

# 4. Telegram Clients (Cloud-Safe StringSessions for BOTH)
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

user_client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH, loop=loop)
# Control bot also uses StringSession('') to prevent file errors on ephemeral disks
bot_client = TelegramClient(StringSession(''), API_ID, API_HASH, loop=loop)
ethiopia_tz = pytz.timezone('Africa/Addis_Ababa')

MY_USER_ID = None

# ==========================================
# 5. CONTROL BOT (@Manage1216bot)
# ==========================================
@bot_client.on(events.NewMessage(pattern='/start'))
async def bot_start(event):
    await event.reply("👋 ሰላም ዘላለም! የ Cloud AI Manager ተነስቷል።\n\n"
                      "**ትዕዛዞች:**\n"
                      "🔴 `/away` - ቦቱን ወደ Away Mode ለመቀየር\n"
                      "🟢 `/online` - ወደ መደበኛ ስራ ለመመለስ\n"
                      "📝 `/set_persona [ጽሁፍ]` - የ AIውን ፀባይ ለመቀየር\n"
                      "📊 `/report` - ያልተመለሱ መልዕክቶችን ለማየት")

@bot_client.on(events.NewMessage(pattern='/away'))
async def set_away(event):
    async with memory_lock:
        mem = _load_memory()
        mem["status"] = "away"
        _save_memory(mem)
    await event.reply("🔴 ሁኔታህ ወደ 'AWAY' ተቀይሯል።")

@bot_client.on(events.NewMessage(pattern='/online'))
async def set_online(event):
    async with memory_lock:
        mem = _load_memory()
        mem["status"] = "online"
        _save_memory(mem)
    await event.reply("🟢 ሁኔታህ ወደ 'ONLINE' ተመልሷል።")

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
        await event.reply("በአሁኑ ሰዓት ሪፖርት የለም።")
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
# 6. USERBOT CORE (Auto-healing Logic)
# ==========================================
def run_ai_request(api_key, model_name, prompt):
    client_ai = genai.Client(api_key=api_key)
    return client_ai.models.generate_content(model=model_name, contents=prompt)

@user_client.on(events.NewMessage(incoming=True))
async def auto_manager(event):
    global MY_USER_ID
    if not event.is_private: return 
    
    sender = await event.get_sender()
    if sender is None or getattr(sender, 'bot', False): return
    
    sender_id = str(sender.id)
    msg_id = event.id
    sender_name = getattr(sender, 'first_name', "ጓደኛ") or "ጓደኛ"

    async with memory_lock:
        mem = _load_memory()
        if msg_id in mem.get("processed_ids", []): return

    await asyncio.sleep(120)

    try:
        history = await user_client.get_messages(event.chat_id, limit=10)
        if any(msg.out and msg.id > msg_id for msg in history): return
    except FloodWaitError as e:
        await asyncio.sleep(e.seconds)
        return
    except: pass 

    async with memory_lock:
        mem = _load_memory()
        current_status = mem.get("status", "online")
        persona = mem.get("personality", "አንተ ትሁት የ AI ማኔጀር ነህ።")
        mem.setdefault("processed_ids", []).append(msg_id)
        _save_memory(mem)

    hour = datetime.now(ethiopia_tz).hour
    is_night = hour >= 22 or hour < 6
    user_text = event.text or "[ፎቶ/ፋይል ተልኳል]"

    short_msg = (user_text[:40] + '..') if len(user_text) > 40 else user_text
    async with memory_lock:
        mem = _load_memory()
        mem.setdefault("missed", []).append({"name": sender_name, "text": short_msg})
        _save_memory(mem)
        
    status_instruction = "ማሳሰቢያ: አለቃዬ የለም (ወይም ተኝቷል)። መልዕክትህን አስቀምጫለሁ በለው።" if current_status == "away" or is_night else "አለቃዬ ቢዚ ስለሆነ፣ እኔ የሱ ማኔጀር እያነጋገርኩህ ነው በለው።"

    prompt = f"""
    {persona}
    የተጠቃሚው ስም: {sender_name}
    የላከው መልዕክት: "{user_text}"
    ህግ:
    1. ተፈጥሯዊ እና አጭር አማርኛ ተጠቀም።
    2. {status_instruction}
    3. እራስህን 'የዘላለም AI ማኔጀር' ብለህ አስተዋውቅ።
    """

    response_text = None
    success = False

    for key in GEMINI_KEYS:
        if success: break
        for model in AI_MODELS:
            try:
                response = await asyncio.to_thread(run_ai_request, key, model, prompt)
                if response and response.text:
                    response_text = response.text
                    success = True
                    break 
            except:
                continue

    signature = "\n\n🤖 *AI Manager*"
    if not success or not response_text:
        final_reply = f"ሰላም {sender_name}! እኔ የዘላለም AI ረዳት ነኝ። አለቃዬ አሁን ኦንላይን ስላልሆነ፣ መልዕክትህን መዝግቤ ይዤዋለሁ። ሲመጣ ራሱ ያናግርሃል። {signature}"
    else:
        final_reply = f"{response_text}{signature}"

    try:
        await event.reply(final_reply, parse_mode='md')
        try:
            await bot_client.send_message(MY_USER_ID, f"🔔 **ምላሽ ተሰጥቷል!**\n👤 ከ: {sender_name}\n💬: {short_msg}\n🤖: {final_reply[:50]}...")
        except: pass
    except FloodWaitError as e:
        print(f"Sleeping for {e.seconds}s due to FloodWait")
        await asyncio.sleep(e.seconds)
        await event.reply(final_reply)
    except:
        try: await event.reply(final_reply)
        except: pass

async def keep_online():
    while True:
        try: await user_client(UpdateStatusRequest(offline=False))
        except: pass
        await asyncio.sleep(60)

async def main():
    global MY_USER_ID
    print("Starting Clients on Cloud...")
    await user_client.start()
    MY_USER_ID = (await user_client.get_me()).id
    
    await bot_client.start(bot_token=BOT_TOKEN)
    asyncio.create_task(keep_online())
    
    print("✅ CLOUD AI MANAGER IS LIVE!")
    try: await bot_client.send_message(MY_USER_ID, "🚀 **Cloud AI MANAGER በርቷል!**\n/start ጻፍ።")
    except: pass
    
    await asyncio.gather(user_client.run_until_disconnected(), bot_client.run_until_disconnected())

if __name__ == '__main__':
    loop.run_until_complete(main())
