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
BOT_SENT_IDS = set()  # ቦቱ ራሱ የላካቸውን መልዕክቶች ከአለቃው መልዕክት ለመለየት

DEFAULT_PERSONA = (
    "አንተ በጣም ትሁት፣ ፕሮፌሽናል፣ ተግባቢ እና ብልህ የአለቃዬ AI ማኔጀር (Personal Assistant) ነህ። "
    "ሁልጊዜ ለሰዎች ክብር ያለው፣ ግልጽ እና ተስፋ ሰጪ ምላሽ ከተገቢ ኢሞጂዎች (😊, 🙏, 🤖, 💬, ✨) ጋር ትሰጣለህ።"
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
        "👋 **ሰላም አለቃዬ! የ Cloud AI Manager በሙሉ አቅሙ እየሰራ ነው።** 🤖✨\n\n"
        "⚙️ **መቆጣጠሪያ ትዕዛዞች:**\n"
        "🔴 `/away` - እኔ የለሁም (ቦቱ ሳይጠብቅ ወዲያውኑ በ1 ሰከንድ እንዲመልስ)\n"
        "🟢 `/online` - መደበኛ ሁኔታ (አንተ በቻት ላይ እየጻፍክ ከሆነ 2 ደቂቃ ይጠብቅሃል)\n"
        "📝 `/set_persona [ጽሁፍ]` - የ AIውን ፀባይ ለመቀየር\n"
        "🔄 `/reset_persona` - የ AIውን ፀባይ ወደ ዋናው (Default) ለመመለስ\n"
        "📊 `/report` - ያልተመለሱ መልዕክቶችን ሪፖርት ለማየት"
    )

@bot_client.on(events.NewMessage(pattern='/away'))
async def set_away(event):
    async with memory_lock:
        mem = _load_memory()
        mem["status"] = "away"
        _save_memory(mem)
    await event.reply("🔴 ሁኔታህ ወደ **AWAY** ተቀይሯል። ቦቱ ምንም ሳይጠብቅ ወዲያውኑ መልስ ይሰጣል! ⚡")

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

@bot_client.on(events.NewMessage(pattern='/reset_persona'))
async def reset_persona(event):
    async with memory_lock:
        mem = _load_memory()
        mem["personality"] = DEFAULT_PERSONA
        _save_memory(mem)
    await event.reply("✅ የ AI ፀባይ ወደ ፕሮፌሽናል እና ትሁት ማኔጀር ተመልሷል!")

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
# 6. Fast AI Request Handler
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
    res = requests.post(url, headers=headers, json=payload, timeout=12)
    if res.status_code == 200:
        data = res.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
    
    # የቁልፍ ስህተት (400/401/403) ከሆነ ጊዜ ሳያጠፋ ወዲያው ወደ ቀጣዩ ቁልፍ እንዲያልፍ
    if res.status_code in [400, 401, 403]:
        raise ValueError(f"Key Auth Error {res.status_code}")

    client_ai = genai.Client(api_key=api_key.strip())
    sdk_res = client_ai.models.generate_content(model=model_name, contents=prompt)
    if sdk_res and sdk_res.text:
        return sdk_res.text
    
    raise Exception(f"HTTP {res.status_code}")

# ==========================================
# 7. USERBOT CORE (Fast & Context-Aware)
# ==========================================
def is_bot_message(msg):
    """መልዕክቱ የቦቱ መሆኑን ወይስ የአለቃው መሆኑን ይለያል"""
    if not msg:
        return False
    if msg.id in BOT_SENT_IDS:
        return True
    txt = msg.text or ""
    return "𝙁𝙧𝙤𝙢 𝙈𝙖𝙣𝙖𝙜𝙚𝙧" in txt or "AI Manager" in txt

@user_client.on(events.NewMessage(outgoing=True))
async def track_boss_activity(event):
    global LAST_BOSS_ACTIVE_TIME
    # ቦቱ ራሱ የላከው መልዕክት ካልሆነ ብቻ አለቃው Online መሆኑን ይመዘግባል
    if not is_bot_message(event.message):
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

    # ---------------------------------------------------------
    # ፈጣን የጥበቃ ሎጂክ (አለቃው በራሱ እጅ እየጻፈ ካልሆነ ወዲያው ይመልሳል!)
    # ---------------------------------------------------------
    now_utc = datetime.now(timezone.utc)
    boss_actively_typing = (time.time() - LAST_BOSS_ACTIVE_TIME) < 120

    try:
        recent_msgs = await user_client.get_messages(event.chat_id, limit=5)
        for m in recent_msgs:
            if m and m.out and not is_bot_message(m):
                if (now_utc - m.date).total_seconds() < 120:
                    boss_actively_typing = True
    except Exception:
        pass

    if current_status == "online" and boss_actively_typing:
        # አለቃው አሁን እየተጠቀመ ስለሆነ 2 ደቂቃ ይጠብቃል
        await asyncio.sleep(120)
        try:
            after_msgs = await user_client.get_messages(event.chat_id, limit=8)
            if any(m.out and not is_bot_message(m) and m.id > msg_id for m in after_msgs if m):
                return  # አለቃው መልሷል፣ ቦቱ ዝም ይላል
        except Exception:
            pass
    else:
        # አለቃው Online የለም! ወዲያውኑ በ 1 ሰከንድ ውስጥ ስራ ይጀምራል
        await asyncio.sleep(1)

    async with memory_lock:
        mem = _load_memory()
        if msg_id in mem.get("processed_ids", []):
            return
        persona = mem.get("personality", DEFAULT_PERSONA)
        mem.setdefault("processed_ids", []).append(msg_id)
        _save_memory(mem)

    user_text = event.text or "[ፎቶ/ሚዲያ ተልኳል]"
    short_msg = (user_text[:40] + '..') if len(user_text) > 40 else user_text

    async with memory_lock:
        mem = _load_memory()
        mem.setdefault("missed", []).append({"name": sender_name, "text": short_msg})
        _save_memory(mem)

    # ---------------------------------------------------------
    # ያለፉትን 60 መልዕክቶች Scan ማድረግ (ለፆታ እና ለውይይት አውድ)
    # ---------------------------------------------------------
    chat_context = ""
    try:
        hist = await user_client.get_messages(event.chat_id, limit=60)
        context_lines = []
        for m in reversed(hist):
            if not m or not m.text:
                continue
            if m.out:
                speaker = "AI ማኔጀር" if is_bot_message(m) else "አለቃዬ"
            else:
                speaker = sender_name
            clean_txt = m.text.replace("✦ 𝙁𝙧𝙤𝙢 𝙈𝙖𝙣𝙖𝙜𝙚𝙧 ✦", "").strip()
            if clean_txt:
                context_lines.append(f"{speaker}: {clean_txt}")
        chat_context = "\n".join(context_lines[-60:])
    except Exception as e:
        print(f"History scan error: {e}")

    hour = datetime.now(ethiopia_tz).hour
    is_night = hour >= 22 or hour < 6

    time_reason = (
        "አለቃዬ አሁን ተኝቷል (ሌሊት ስለሆነ) 😴"
        if is_night
        else "አለቃዬ አሁን ስራ ስለበዛበት Online የለም 💼"
    )

    prompt = f"""
{persona}

[ያለፉት 60 መልዕክቶች ውይይት (Chat History)]
{chat_context}

[አሁን የመጣ አዲስ መልዕክት]
የላኪው ስም: {sender_name}
መልዕክቱ: "{user_text}"

በጣም ጥብቅ መመሪያዎች (በትክክል ተግብር):
1. **ስም**: አለቃህን ሁልጊዜ **"አለቃዬ"** ብቻ ብለህ ጥራው። የአለቃህን ትክክለኛ ስም ወይም ሌላ ስም በፍጹም አትጥራ!
2. **ፆታ መለየት (Gender Detection)**: ከላይ ያለውን የ60 መልዕክቶች ሂስትሪ እና የላኪውን ስም ({sender_name}) በጥንቃቄ መርምር። አለቃዬ ከዚህ በፊት ሰውየውን እንዴት እንዳናገረው (ለምሳሌ ለሴት፦ አንቺ/ሽ/የኔ ቆንጆ/እህቴ፣ ለወንድ፦ አንተ/ህ/ወንድሜ/ጓደኛዬ) ወይም ሰውየው ስለራሱ የጻፈውን አይተህ ፆታውን በትክክል ለይተህ በዛው ፆታ አናግር። (ፍንጭ ካጣህ ብቻ በአክብሮት አናግር)።
3. **ትህትና፣ ፕሮፌሽናሊዝም እና Emoji**: በጣም ትሁት፣ ተግባቢ እና ፕሮፌሽናል ሁን። በመልስህ ውስጥ ተስማሚ ኢሞጂዎችን (😊, 🙏, 🤖, 💬, ✨, 💼) በተፈጥሯዊ መንገድ ተጠቀም።
4. **ዋና መልዕክት እና የጥያቄ አመላለስ**:
   - መጀመሪያ {time_reason} ብለህ በትህትና አሳውቅ።
   - "ጥያቄ ወይም ጉዳይ ካለህ/ካለሽ እኔን መጠየቅ ትችላለህ/ትችያለሽ፤ የማውቀው ከሆነ ወዲያው እመልስልሃለሁ/እመልስልሻለሁ። ካላወቅኩት ወይም የግል (Private) ጉዳይ ከሆነ ደግሞ አለቃዬ ልክ እንደመጣ አሳውቀውና እሱ ራሱ ይመልስልሃል/ይመልስልሻል" የሚለውን ሀሳብ በተፈጥሯዊና ማራኪ አገላለጽ አካትት።
   - ሰውየው አሁን በላከው መልዕክት ውስጥ ጥያቄ ጠይቆ ከሆነ፦ ከ60 መልዕክቶች ሂስትሪ ወይም ከአጠቃላይ እውቀትህ የምታውቀው ከሆነ ወዲያውኑ በትህትና መልስለት። የማታውቀው ወይም የአለቃዬ የግል (Private) ጉዳይ ከሆነ ግን "ይህንን ጉዳይ አለቃዬ ልክ ሲመጣ አሳውቀውና እሱ ራሱ ይመልስልሃል/ሻል 🙏" በለው።
5. **ቋንቋ**: ሰውየው በጻፈበት ቋንቋ (አማርኛ ወይም እንግሊዝኛ) መልስ።
6. በመልስህ መጨረሻ ላይ ፊርማ አትጨምር (ሲስተሙ ራሱ ይጨምራል)።
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
            except ValueError as ve:
                # ቁልፉ የማይሰራ ከሆነ ሌሎች ሞዴሎችን በዚሁ ቁልፍ ሳይሞክር ወደ ቀጣዩ ቁልፍ ይዘላል
                print(f"Skipping bad key: {ve}")
                break
            except Exception as e:
                print(f"Model Retry ({model}): {e}")
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
                    "🤖 እኔ የአለቃዬ AI Manager ነኝ። አለቃዬ አሁን ስራ ስለበዛበት ለጊዜው Online የለም 💼። "
                    "ጥያቄ ወይም መልዕክት ካለዎት እዚህ መተው ይችላሉ፤ አለቃዬ ልክ ሲመለስ አሳውቀውና እሱ ራሱ ይመልስልዎታል። 💬\n\n"
                    "🙏 መልካም ጊዜ! ✨"
                )
                final_offline_msg = f"{html.escape(offline_body)}{signature_html}"

                try:
                    sent = await event.reply(final_offline_msg, parse_mode='html')
                    if sent:
                        BOT_SENT_IDS.add(sent.id)
                except Exception:
                    sent = await event.reply(offline_body + "\n\n✦ 𝙁𝙧𝙤𝙢 𝙈𝙖𝙣𝙖𝙜𝙚𝙧 ✦")
                    if sent:
                        BOT_SENT_IDS.add(sent.id)

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
            sent = await event.reply(final_reply, parse_mode='html')
            if sent:
                BOT_SENT_IDS.add(sent.id)
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
            sent = await event.reply(final_reply, parse_mode='html')
            if sent:
                BOT_SENT_IDS.add(sent.id)
        except Exception:
            sent = await event.reply(response_text.strip() + "\n\n✦ 𝙁𝙧𝙤𝙢 𝙈𝙖𝙣𝙖𝙜𝙚𝙧 ✦")
            if sent:
                BOT_SENT_IDS.add(sent.id)

        try:
            await bot_client.send_message(MY_USER_ID, f"🔔 **AI ምላሽ ሰጥቷል!**\n👤 ከ: {sender_name}\n💬: {short_msg}\n🤖: {response_text[:80]}...")
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
        await bot_client.send_message(
            MY_USER_ID,
            "🚀 **AI MANAGER አፕዴት ተደርጎ ተነስቷል!** ✨\n"
            "• 60 መልዕክቶችን Scan ያደርጋል (ፆታ ይለያል)\n"
            "• ሳይዘገይ በፍጥነት ይመልሳል\n"
            "• የድሮ ፀባይ ተይዞ ከነበረ ለማጽዳት `/reset_persona` ብለህ ጻፍልኝ።"
        )
    except Exception:
        pass

    await asyncio.gather(
        user_client.run_until_disconnected(),
        bot_client.run_until_disconnected()
    )

if __name__ == '__main__':
    loop.run_until_complete(main())
