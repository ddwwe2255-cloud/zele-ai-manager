import os
import asyncio
import json
import time
import random
from threading import Thread
from flask import Flask
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.account import UpdateStatusRequest
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
from google import genai 
from google.genai import types
from datetime import datetime, timezone
import pytz

# ==========================================
# 1. የአንተ መረጃዎች (Credentials)
# ==========================================
api_id = 32161103
api_hash = '953aae804429e8411062310048d0614c'
session_string = '1BJWap1sBu4jjQwjccL5Q3W771fJQDSQqw2zMmw_af4zYPwFnkbmsQsIYRZ17rKZ2xhJa_j6uQm1JgkwEBrxzKifom_XJa0bs8kHSf_ecshATsuyMEgWCvJ85d0yVD9EThzG5eWzB7W1dtOP6C9OdkaxhnGQLR3IfTkrpp_E4YUYpn89Dx90Tz-JxdW8x3QxnFKx3fnf-Dbnu0mgs7LgLl6r5P_h0fVz-NtWORdBVUoKOhlEETJVAo7-ziGobKX3put4IGzvtkgAxI7tVSB-XBiHWeeO3L3xMveSZ66HXikk-GPMKkrqe1dcoP1txQOMia4fKxewFDWZZ5CVsswrOyrZ2rAoZjzM='
my_username = "@zelalem" 

# አዲሶቹ የ Gemini API Keys (በ AQ. የሚጀምሩት)
gemini_api_keys = [
    'AQ.Ab8RN6IDfLB40lZPSo3VPNqCmDe0C4ag0gWICY26oN0SxBc7-Q',
    'AQ.Ab8RN6IKVfXMm2eqWzN1btEiTSTZJ338Q_M...' # እዚህ ጋር ፎቶው ላይ ያለውን ሙሉ ሁለተኛ ቁልፍህን አስገባ
]

# ==========================================
# 2. የቦቱ ማስታወሻ ደብተር (Memory Manager)
# ==========================================
MEMORY_FILE = 'memory.json'

def init_memory():
    if not os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
            json.dump({"users": {}, "status": "active", "fallback_timestamps": {}, "missed_messages": []}, f)

def load_memory():
    try:
        with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if "fallback_timestamps" not in data: data["fallback_timestamps"] = {}
            if "missed_messages" not in data: data["missed_messages"] = []
            return data
    except Exception:
        return {"users": {}, "status": "active", "fallback_timestamps": {}, "missed_messages": []}

def save_memory(data):
    with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

init_memory()

app = Flask(__name__)
@app.route('/')
def home():
    return "AI MANAGER is running with Advanced Features & AQ. Key Support!"

def run_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

Thread(target=run_server, daemon=True).start()

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
client = TelegramClient(StringSession(session_string), api_id, api_hash, loop=loop)
ethiopia_tz = pytz.timezone('Africa/Addis_Ababa')

@client.on(events.NewMessage(incoming=True))
async def super_smart_manager(event):
    sender = await event.get_sender()
    if sender is None or getattr(sender, 'bot', False): return
        
    sender_id = str(sender.id)
    sender_name = getattr(sender, 'first_name', "ያልታወቀ ሰው") or "ያልታወቀ ሰው"
    msg_lower = event.text.lower() if event.text else ""
    
    memory = load_memory()
    bot_status = memory.get("status", "active")

    if getattr(event, 'out', False) or sender_id == str((await client.get_me()).id): 
        if msg_lower.startswith(".summary"):
            await send_summary(event, memory)
            return
        commands = {".vip": "vip", ".gf": "gf", ".normal": "normal"}
        for cmd, status in commands.items():
            if msg_lower.startswith(cmd):
                memory["users"][str(event.chat_id)] = status
                save_memory(memory)
                await event.delete()
                return
        if msg_lower.startswith(".pause"):
            memory["status"] = "paused"
            save_memory(memory)
            await event.delete()
            return
        elif msg_lower.startswith(".resume"):
            memory["status"] = "active"
            save_memory(memory)
            await event.delete()
            return

    if bot_status == "paused": return

    is_group = event.is_group or event.is_channel
    if is_group:
        if not ("zele" in msg_lower or "zelalem" in msg_lower or my_username.lower() in msg_lower): return
            
    if not getattr(event, 'out', False):
        try:
            history = await client.get_messages(event.chat_id, limit=10)
            now = datetime.now(timezone.utc)
            is_boss_active = any(msg.out and (now - msg.date).total_seconds() < 300 for msg in history if msg)
            if is_boss_active:
                await asyncio.sleep(120)
                new_history = await client.get_messages(event.chat_id, limit=5)
                if any(msg.out and msg.id > event.id for msg in new_history): return 
            else:
                await asyncio.sleep(2)
            await process_and_reply(event, sender_id, sender_name, memory, is_group)
        except Exception as e:
            pass

async def send_summary(event, memory):
    missed_msgs = memory.get("missed_messages", [])
    if not missed_msgs:
        await event.reply("በሌለህበት ሰዓት አዲስ የመጣ መልእክት የለም።", parse_mode='html')
        return
    summary_text = "<b>📊 የሌለህበት ሰዓት ሪፖርት (Summary)</b>\n\n"
    for msg in missed_msgs:
        summary_text += f"👤 <b>{msg['name']}</b>: {msg['text']}\n"
    memory["missed_messages"] = []
    save_memory(memory)
    await event.reply(summary_text, parse_mode='html')

async def process_and_reply(event, sender_id, sender_name, memory, is_group):
    user_status = memory["users"].get(sender_id, "unknown")
    current_time_et = datetime.now(ethiopia_tz)
    hour = current_time_et.hour
    is_night_time = hour >= 22 or hour < 6 
    
    user_message = event.text if event.text else ""
    media_file_path = None
    media_type = None

    if event.media:
        if isinstance(event.media, MessageMediaPhoto):
            media_type = "image/jpeg"
            media_file_path = await client.download_media(event.media, file="temp_image.jpg")
            user_message += " [ፎቶ ተልኳል]"
        elif isinstance(event.media, MessageMediaDocument) and getattr(event.document, 'mime_type', '').startswith('audio/'):
            media_type = event.document.mime_type
            media_file_path = await client.download_media(event.media, file="temp_audio.ogg")
            user_message += " [የድምጽ መልእክት ተልኳል]"

    if not event.is_group:
        short_msg = (user_message[:30] + '...') if len(user_message) > 30 else user_message
        memory.setdefault("missed_messages", []).append({"name": sender_name, "text": short_msg})
        save_memory(memory)

    try:
        history = await client.get_messages(event.chat_id, limit=10) 
        chat_context = "\n".join([f"{'እኔ(አለቃዬ)' if msg.out else 'ሰውየው'}: {msg.text}" for msg in reversed(history) if msg and msg.text])
    except Exception:
        chat_context = ""

    prompt = f"""
    አንተ ስምህ 'AI MANAGER' ነው። አንተ አለቃህ (ዘላለም) ራሱ የጻፈህ የግል ማኔጀሩ ነህ።
    
    [የቀድሞ ቻት ሂስትሪ]
    {chat_context}

    አዲስ መልእክት ከ {sender_name}: '{user_message}'
    የተላከበት ሰዓት: የኢትዮጵያ ሰዓት {hour}:00

    የአንተ ተግባር:
    1. ተፈጥሯዊ ጭውውት፦ ቋንቋውን አይተህ በዛው ቋንቋ መልስ።
    2. ፆታ ለይ፦ ስሙን ወይም አነጋገሩን አይተህ ወንድ ወይም ሴት መሆኑን ለይተህ አናግር።
    3. ማሳሰቢያ፦ {"አለቃዬ አሁን ተኝቷል 😴 ጠዋት ሲነሳ ይነግርሃል" if is_night_time else "አለቃዬ አሁን የለም፤ ልክ ሲመጣ አደርስለታለሁ"} ብለህ በመሃል ጣል አድርግ።
    4. ሚዲያ፦ ፎቶ ወይም ድምጽ ካለ፣ ያየኸውን/የሰማኸውን ተረድተህ መልስ ስጥ።
    5. ስሙን አትጥራ፣ 'አለቃዬ' ብቻ በል።
    """
    
    signature = "<blockquote><i>🤖 𝗙𝗿𝗼𝗺 𝗠𝗮𝗻𝗮𝗴𝗲𝗿 ✨</i></blockquote>"
    response = None
    
    try:
        # ስህተቴን አርሜዋለሁ፡ የ `AQ.` ቁልፎችንም ይቀበላል!
        for index, key in enumerate(gemini_api_keys):
            try:
                ai_client = genai.Client(api_key=key)
                if media_file_path:
                    uploaded_file = ai_client.files.upload(file=media_file_path, mime_type=media_type)
                    response = ai_client.models.generate_content(
                        model='gemini-1.5-flash', 
                        contents=[uploaded_file, prompt]
                    )
                    if os.path.exists(media_file_path): os.remove(media_file_path)
                else:
                    response = ai_client.models.generate_content(
                        model='gemini-1.5-flash', 
                        contents=prompt
                    )
                break 
            except Exception as e:
                print(f"Key {index+1} Error: {e}")
                continue 
                
        if response is None:
            raise Exception("API Keys failed.")

        final_reply = f"{response.text}\n\n{signature}"
        await event.reply(final_reply, reply_to=event.id, parse_mode='html')
        
        if "fallback_timestamps" in memory and sender_id in memory["fallback_timestamps"]:
            del memory["fallback_timestamps"][sender_id]
            save_memory(memory)
            
    except Exception as ai_error:
        print(f"Offline Mode Triggered: {ai_error}")
        fallback_data = memory.get("fallback_timestamps", {})
        last_sent_time = fallback_data.get(sender_id, 0)
        current_time = time.time()
        
        if current_time - last_sent_time >= 43200:
            if is_night_time:
                fallback_msg = f"ሰላም {sender_name}! አለቃዬ አሁን ተኝቷል 😴 ማታ ስለሆነ ማረፍ ይፈልጋል። ጠዋት ሲነሳ አደርስለታለሁ።\n\n" + signature
            else:
                fallback_msg = f"ሰላም {sender_name}! አለቃዬ አሁን ኦንላይን የለም። መልእክትህን አስቀምጥልኝ፣ ልክ ሲመጣ አደርስለታለሁ እሱ ራሱ ይመልስልሃል።\n\n" + signature
                    
            await event.reply(fallback_msg, reply_to=event.id, parse_mode='html')
            
            fallback_data[sender_id] = current_time
            memory["fallback_timestamps"] = fallback_data
            save_memory(memory)
        else:
            print("🤐 [SILENT] ሰውየው ቀድሞ ተነግሮታል፣ ላለፉት 12 ሰዓታት ዝም እላለሁ።")

async def keep_online():
    while True:
        try:
            await client(UpdateStatusRequest(offline=False))
            await asyncio.sleep(60) 
        except Exception:
            await asyncio.sleep(60)

print("AI MANAGER (Fixed Key Version) Started successfully!")
client.start()
client.loop.create_task(keep_online())
client.run_until_disconnected()
