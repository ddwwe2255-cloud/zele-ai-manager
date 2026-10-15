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

API_ID = int(os.getenv('API_ID', 0))
API_HASH = os.getenv('API_HASH', '')
SESSION_STRING = os.getenv('SESSION_STRING', '')
BOT_TOKEN = os.getenv('BOT_TOKEN', '')
MY_USERNAME = os.getenv('MY_USERNAME', '@zele1216')

# ==========================================
# 2. Memory & State Locks
# ==========================================
MEMORY_FILE = 'memory.json'
memory_lock = asyncio.Lock()
ai_request_lock = asyncio.Lock()  # ሰርቨሩ በተከታታይ ጥያቄዎች እንዳይታገድ (No Rate-Limit)
LAST_BOSS_CHAT_TIME = {}
BOT_SENT_IDS = set()
IS_BOT_SENDING = False

DEFAULT_PERSONA = (
    "You are an intelligent, polite, and highly capable Personal AI Manager built and programmed by your boss. "
    "You deeply understand Amharic (both Ge'ez script and Franco/Latin Amharic like 'mndnew', 'lemns ytekmal') and English."
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
# 3. Flask Server (For 24/7 UptimeRobot)
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
# 6. Franco-Amharic (Arada/Latin Amharic) Detector
# ==========================================
FRANCO_WORDS = {
    "mndnew", "mndn", "mindinew", "mdnew", "malet", "lemns", "lemn", "lemin",
    "ytekmal", "yiteqmal", "yitekm", "endet", "endiet", "selam", "slm", "sira",
    "tmkrt", "tmhrt", "timhirt", "zare", "nege", "ante", "anchi", "maneh",
    "manes", "manew", "yeserah", "yserah", "et", "yet", "neh", "nesh", "new",
    "nw", "alh", "aleh", "alesh", "ebakh", "ebakih", "chgr", "chigir", "mfthe",
    "yichalal", "ychalal", "enchlalen", "mesrat", "msrat", "miknyat", "bzu",
    "tolo", "mels", "melis", "betam", "konjo", "arif", "gobez", "wendme", "ehte"
}

def has_geez(text):
    return any(0x1200 <= ord(c) <= 0x137F for c in text)

def is_franco_amharic(text):
    words = set(re.findall(r'[a-zA-Z]+', text.lower()))
    if words & FRANCO_WORDS:
        return True
    # የተለመዱ የአማርኛ አጻጻፍ ቅጦች በእንግሊዝኛ ፊደል
    patterns = [r'\bmndn', r'\bmalet\b', r'\bytek', r'\blemn', r'\bendet', r'\byeser']
    return any(re.search(p, text.lower()) for p in patterns)

def is_amharic_intent(text):
    return has_geez(text) or is_franco_amharic(text)

# ==========================================
# 7. Rock-Solid OpenAI Engine (No Broken Models!)
# ==========================================
BROWSER_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
}

def is_valid_reply(ans):
    if not ans or len(ans.strip()) < 3:
        return False
    bad = ["<!doctype", "<html", "rate limit", "too many requests", "502 bad gateway", "internal server error", "cloudflare"]
    return not any(b in ans.lower() for b in bad)

def call_ai_sync(system_rules, user_prompt):
    messages = [
        {"role": "system", "content": system_rules},
        {"role": "user", "content": user_prompt}
    ]

    # አማርኛ እንዳይዘበራረቅ 'openai' እና 'openai-large' ብቻ እንጠቀማለን!
    for attempt in range(4):
        if attempt > 0:
            time.sleep(2.5 * attempt)

        seed_val = random.randint(10000, 9999999)

        # 1. Primary OpenAI JSON Endpoint
        for model_name in ["openai", "openai-large"]:
            try:
                res = requests.post(
                    "https://text.pollinations.ai/openai",
                    headers=BROWSER_HEADERS,
                    json={
                        "model": model_name,
                        "messages": messages,
                        "seed": seed_val,
                        "temperature": 0.6
                    },
                    timeout=25
                )
                if res.status_code == 200:
                    data = res.json()
                    ans = data["choices"][0]["message"]["content"].strip()
                    if is_valid_reply(ans):
                        return ans
            except Exception:
                pass

        # 2. Root POST Endpoint
        try:
            res = requests.post(
                "https://text.pollinations.ai/",
                headers=BROWSER_HEADERS,
                json={
                    "messages": messages,
                    "model": "openai",
                    "seed": seed_val,
                    "private": True
                },
                timeout=25
            )
            if res.status_code == 200 and is_valid_reply(res.text):
                return res.text.strip()
        except Exception:
            pass

        # 3. Clean GET Endpoint (በጣም አስተማማኝ Backup)
        try:
            compact_prompt = f"{system_rules}\n\nUser: {user_prompt}\nAssistant:"
            encoded = urllib.parse.quote(compact_prompt[:1400])
            res = requests.get(
                f"https://text.pollinations.ai/{encoded}?model=openai&seed={seed_val}",
                headers=BROWSER_HEADERS,
                timeout=20
            )
            if res.status_code == 200 and is_valid_reply(res.text):
                return res.text.strip()
        except Exception:
            pass

    raise Exception("AI Server Temporarily Overloaded")

# ==========================================
# 8. USERBOT CORE
# ==========================================
def is_bot_message(msg):
    if not msg:
        return False
    if msg.id in BOT_SENT_IDS:
        return True
    txt = msg.text or ""
    return "From Manager" in txt or "𝙁𝙧𝙤𝙢 𝙈𝙖𝙣𝙖𝙜𝙚𝙧" in txt

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

    # -------------------------------------------------------------
    # ትክክለኛ የ 2 ደቂቃ ፍተሻ፦ አንተ (አለቃው) በዛ ቻት ላይ ባለፉት 2 ደቂቃ ጽፈሃል?
    # -------------------------------------------------------------
    now_utc = datetime.now(timezone.utc)
    boss_active_in_this_chat = (time.time() - LAST_BOSS_CHAT_TIME.get(event.chat_id, 0)) < 120

    try:
        recent_check = await user_client.get_messages(event.chat_id, limit=6)
        for m in recent_check:
            if m and m.out and not is_bot_message(m):
                if (now_utc - m.date).total_seconds() < 120:
                    boss_active_in_this_chat = True
                    LAST_BOSS_CHAT_TIME[event.chat_id] = time.time()
    except Exception:
        pass

    if current_status == "online" and boss_active_in_this_chat:
        # አንተ እያወራህ ስለሆነ 2 ደቂቃ (120 ሰከንድ) ይጠብቅሃል!
        await asyncio.sleep(120)
        try:
            after_msgs = await user_client.get_messages(event.chat_id, limit=6)
            if any(m.out and not is_bot_message(m) and m.id > msg_id for m in after_msgs if m):
                return  # አንተ ስለመለስክ ቦቱ ዝም ይላል
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

    # ያለፉትን 15 መልዕክቶች ማንበብ
    chat_context = ""
    try:
        hist = await user_client.get_messages(event.chat_id, limit=15)
        context_lines = []
        for m in reversed(hist):
            if not m or not m.text:
                continue
            speaker = "AI Manager" if is_bot_message(m) else ("Boss" if m.out else sender_name)
            clean_txt = m.text.replace("✦ From Manager ✦", "").replace("✦ 𝙁𝙧𝙤𝙢 𝙈𝙖𝙣𝙖𝙜𝙚𝙧 ✦", "").strip()
            if clean_txt:
                context_lines.append(f"{speaker}: {clean_txt}")
        chat_context = "\n".join(context_lines[-15:])
    except Exception:
        pass

    # የአማርኛ ወይም የፍራንኮ-አማርኛ (ለምሳሌ "physics mndnew lemns ytekmal") መልዕክት መሆኑን መለየት
    use_amharic = is_amharic_intent(user_text)

    system_rules = f"""
{persona}

CRITICAL RULES YOU MUST FOLLOW 100%:
1. FRANCO-AMHARIC (LATIN AMHARIC) UNDERSTANDING:
   - Ethiopian users often write Amharic using English letters (e.g., "Agriculture malet mdnew" = "ግብርና ማለት ምንድነው?", "physics mndnew lemns ytekmal" = "ፊዚክስ ምንድነው ለምንስ ይጠቅማል?", "manew yeserah" = "ማነው የሰራህ?").
   - NEVER ask "Could you clarify what you mean by..."! Decode the phonetic Amharic words naturally and answer the question directly!

2. LANGUAGE OF YOUR REPLY:
   - Target reply language for this message: {"FLUENT, NATURAL AMHARIC (በጥሩ እና ግልጽ አማርኛ ፊደል መልስ!)" if use_amharic else "FLUENT, CLEAR ENGLISH"}.
   - If replying in Amharic: Use clear, grammatically correct Amharic (Ge'ez script). Refer to your boss ONLY as "አለቃዬ".
   - If replying in English: Use clear English. Refer to your boss ONLY as "my boss".
   - NEVER mix Amharic and English in the same sentence, and NEVER mention your boss's real name.

3. DIRECT, SMART ANSWERS:
   - If the user asks a knowledge or technical question (e.g., Physics, Agriculture, Forex, AI trading bots, coding, math):
     Give a clear, smart, well-structured, and accurate explanation right away!
     (For example, if asked if we can build an automatic Forex AI trader and how: explain clearly in natural Amharic that yes, it is possible using Python, MetaTrader 5 (MT5), historical data, and algorithmic strategies).
   - If asked "ማነው የሰራህ?" / "manew yeserah" / "Who made you?":
     Proudly answer that your boss ("አለቃዬ" / "my boss") built and programmed you himself.
   - If asked about your boss's whereabouts (e.g., "የት ነህ?", "ለምን ቀረህ?"):
     Politely explain that your boss is currently busy/offline and you will pass their message to him as soon as he returns.

4. FORMAT:
   - Keep it concise (2 to 4 clear sentences) with 1 or 2 friendly emojis (😊, 🙏, ✨).
   - Do NOT include any signature at the end.
"""

    user_prompt = f"[Recent Chat Context]\n{chat_context}\n\n[User ({sender_name}) asks]: {user_text}"
    signature_html = "\n\n<blockquote>✦ <b><i>From Manager</i></b> ✦</blockquote>"

    # AI ጥያቄዎችን በወረፋ (Queue Lock) ያስተናግዳል — ሰርቨሩ በፍጹም Rate-Limit እንዳያደርግ!
    async with ai_request_lock:
        IS_BOT_SENDING = True
        try:
            try:
                response_text = await asyncio.to_thread(call_ai_sync, system_rules, user_prompt)
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
                # ድንገት ኢንተርኔት ሙሉ በሙሉ ቢቋረጥ እንኳ በ 1 ሰዓት ውስጥ አንዴ ብቻ ይልካል
                async with memory_lock:
                    mem = _load_memory()
                    fallback_timestamps = mem.get("fallback_timestamps", {})
                    last_sent = fallback_timestamps.get(sender_id, 0)

                    if time.time() - last_sent >= 3600:
                        if use_amharic:
                            offline_body = (
                                "👋 ሰላም! 😊\n"
                                "🤖 እኔ AI Manager ነኝ። አለቃዬ ለጊዜው Online ስላልሆነ፣ መልዕክት ካለዎት እዚህ መተው ይችላሉ። "
                                "ሲመለስ አሳውቀዋለሁ፤ እሱም በራሱ ይመልስልዎታል። 💬\n\n"
                                "🙏 መልካም ጊዜ!"
                            )
                        else:
                            offline_body = (
                                f"Hello {sender_name}! 😊\n"
                                "🤖 I am the AI Manager. My boss is currently offline, so please feel free to leave your message here. "
                                "I will notify him as soon as he returns! 💬\n\n"
                                "🙏 Have a great time!"
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
# 9. Always Online Loop & Startup
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
            "🚀 **AI MANAGER (Franco-Amharic + Pure OpenAI) ተነስቷል!** ✨\n"
            "• እንደ 'physics mndnew lemns ytekmal' ወይም 'Agriculture malet mdnew' ያሉ ጽሑፎችን ተረድቶ በጠራ አማርኛ ይመልሳል!\n"
            "• የተዘበራረቀ አማርኛ የሚጽፉት ሞዴሎች ተወግደዋል።\n"
            "• አንተ በቻት ስትጽፍ ወዲያው አይቶ 2 ደቂቃ ይጠብቅሃል።"
        )
    except Exception:
        pass

    await asyncio.gather(
        user_client.run_until_disconnected(),
        bot_client.run_until_disconnected()
    )

if __name__ == '__main__':
    loop.run_until_complete(main())
