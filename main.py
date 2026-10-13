import os
import asyncio
import json
import random
from threading import Thread
from flask import Flask
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.account import UpdateStatusRequest
from google import genai 
from datetime import datetime, timezone

# ==========================================
# 1. የአንተ መረጃዎች (Credentials)
# ==========================================
api_id = 32161103
api_hash = '953aae804429e8411062310048d0614c'
session_string = '1BJWap1sBu4jjQwjccL5Q3W771fJQDSQqw2zMmw_af4zYPwFnkbmsQsIYRZ17rKZ2xhJa_j6uQm1JgkwEBrxzKifom_XJa0bs8kHSf_ecshATsuyMEgWCvJ85d0yVD9EThzG5eWzB7W1dtOP6C9OdkaxhnGQLR3IfTkrpp_E4YUYpn89Dx90Tz-JxdW8x3QxnFKx3fnf-Dbnu0mgs7LgLl6r5P_h0fVz-NtWORdBVUoKOhlEETJVAo7-ziGobKX3put4IGzvtkgAxI7tVSB-XBiHWeeO3L3xMveSZ66HXikk-GPMKkrqe1dcoP1txQOMia4fKxewFDWZZ5CVsswrOyrZ2rAoZjzM='
my_username = "@zelalem" 

# ה Gemini API Keys (የሚፈራረቁ)
gemini_api_keys = [
    'AQ.Ab8RN6IDfLB40lZPSo3VPNqCmDe0C4ag0gWICY26oN0SxBc7-Q',
    'AQ.Ab8RN6JxItPI08S504E-pYPleAO1JNR_KcrVBNeHfYS7qYgdCA'
]

# ==========================================
# 2. የቦቱ ማስታወሻ ደብተር (Memory Manager)
# ==========================================
MEMORY_FILE = 'memory.json'

def init_memory():
    if not os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
            json.dump({"users": {}, "status": "active", "fallback_sent": []}, f)

def load_memory():
    try:
        with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if "fallback_sent" not in data:
                data["fallback_sent"] = []
            return data
    except Exception:
        return {"users": {}, "status": "active", "fallback_sent": []}

def save_memory(data):
    with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

init_memory()

# ==========================================
# 3. 24 ሰዓት እንዲሰራ የሚያደርገው Web Server
# ==========================================
app = Flask(__name__)
@app.route('/')
def home():
    return "AI MANAGER is running seamlessly!"

def run_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

Thread(target=run_server, daemon=True).start()

# ==========================================
# 4. ቴሌግራም ክላይንት እና ዋናው ሎጂክ
# ==========================================

# ⚠️ አዲሱ የተስተካከለው የክላውድ ሰርቨር ኮድ ⚠️
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
client = TelegramClient(StringSession(session_string), api_id, api_hash, loop=loop)

@client.on(events.NewMessage(incoming=True))
async def super_smart_manager(event):
    sender = await event.get_sender()
    if sender is None or getattr(sender, 'bot', False):
        return
        
    sender_id = str(sender.id)
    sender_name = getattr(sender, 'first_name', "ያልታወቀ ሰው") or "ያልታወቀ ሰው"
    user_message = event.text if event.text else "[ፎቶ ወይም ፋይል]"
    msg_lower = user_message.lower()
    
    memory = load_memory()
    bot_status = memory.get("status", "active")

    # የአለቃው ኮማንዶች (Commands)
    if getattr(event, 'out', False): 
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

    if bot_status == "paused":
        return

    # ግሩፕ ከሆነ (አለቃው ከተጠራ ብቻ)
    is_group = event.is_group or event.is_channel
    if is_group:
        if not ("zele" in msg_lower or "zelalem" in msg_lower or my_username.lower() in msg_lower):
            return
            
    # አለቃው መኖሩን ማጣራት (Online Check)
    if not getattr(event, 'out', False):
        try:
            history = await client.get_messages(event.chat_id, limit=10)
            now = datetime.now(timezone.utc)
            is_boss_active = any(msg.out and (now - msg.date).total_seconds() < 300 for msg in history if msg)
            
            if is_boss_active:
                await asyncio.sleep(120)
                new_history = await client.get_messages(event.chat_id, limit=5)
                if any(msg.out and msg.id > event.id for msg in new_history):
                    return 
            else:
                await asyncio.sleep(2)
                
            await process_and_reply(event, sender_id, sender_name, user_message, is_group, memory)
        except Exception as e:
            print(f"Chat History Error: {e}")

async def process_and_reply(event, sender_id, sender_name, user_message, is_group, memory):
    user_status = memory["users"].get(sender_id, "unknown")
    
    try:
        history = await client.get_messages(event.chat_id, limit=15) 
        chat_context = "\n".join([f"{'እኔ(አለቃዬ)' if msg.out else 'ሰውየው'}: {msg.text}" for msg in reversed(history) if msg and msg.text])
    except Exception:
        chat_context = ""

    group_rule = "ይሄ ከግሩፕ ነው። አጭር እና ትክክለኛ መልስ ስጥ።" if is_group else ""
    
    # የቦቱ ፀባይ (Persona)
    persona_rules = {
        "gf": "ይቺ ልጅ የአለቃዬ ፍቅረኛ ነች። በፍቅር፣ በቀልድ እና በጥንቃቄ አናግራት።",
        "vip": "ይሄ ሰው የአለቃዬ ቅርብ ጓደኛ ነው። ሞቅ አድርገህና ተግባብተህ አናግረው።",
        "normal": "ይሄ ሰው አዲስ ወይም መደበኛ ሰው ነው። ፕሮፌሽናል እና ትሁት ሁን።",
        "unknown": "ይሄ ሰው አዲስ ወይም መደበኛ ሰው ነው። ፕሮፌሽናል እና ትሁት ሁን።"
    }
    persona_rule = persona_rules.get(user_status, persona_rules["unknown"])

    prompt = f"""
    አንተ ስምህ 'AI MANAGER' ነው። አንተ አለቃህ ራሱ የጻፈህ የግል ማኔጀሩ ነህ።
    
    [የቀድሞ ቻት ሂስትሪ]
    {chat_context}

    አዲስ መልእክት ከ {sender_name}: '{user_message}'

    የአንተ ተግባር:
    1. ተፈጥሯዊ ጭውውት፦ ማሽን አትምሰል! የሰውየውን መልእክት ተረድተህ አውራው።
    2. ማሳሰቢያ፦ ካወራኸው በኋላ በመሃል ወይም በመጨረሻ ላይ "በነገራችን ላይ አለቃዬ አሁን የለም፤ ልክ ሲመጣ መልእክትህን አደርስለታለሁ" ብለህ ጣል አድርግ።
    3. ፆታ ለይ፦ ከቻቱ አይተህ ወንድ ወይም ሴት መሆኑን ለይተህ አናግር።
    4. ስም ማጥፋት፦ ስሙን በፍጹም አትጥራ፣ ሁልጊዜም 'አለቃዬ' ብቻ በል።
    
    {group_rule}
    {persona_rule}
    """
    
    signature = "<blockquote><i>🤖 𝗙𝗿𝗼𝗺 𝗠𝗮𝗻𝗮𝗴𝗲𝗿 ✨</i></blockquote>"
    
    try:
        # API Keys Rotating Logic
        response = None
        last_error = None
        
        for key in gemini_api_keys:
            try:
                ai_client = genai.Client(api_key=key)
                response = ai_client.models.generate_content(
                    model='gemini-3.8-flash', 
                    contents=prompt
                )
                break 
            except Exception as e:
                last_error = e
                continue 
                
        if response is None:
            raise last_error 

        final_reply = f"{response.text}\n\n{signature}"
        await event.reply(final_reply, reply_to=event.id, parse_mode='html')
        
        # API ሰርቷል፣ Offline ማስታወሻዎችን እናጥፋለን
        if "fallback_sent" in memory and sender_id in memory["fallback_sent"]:
            memory["fallback_sent"].remove(sender_id)
            save_memory(memory)
            
    except Exception as ai_error:
        print(f"\n❌ [API ERROR OR OFFLINE] {ai_error}\n")
        
        fallback_sent_list = memory.get("fallback_sent", [])
        if sender_id not in fallback_sent_list:
            fallback_msg = f"ሰላም {sender_name}! አለቃዬ አሁን ኦንላይን የለም። መልእክትህን አስቀምጥልኝ፣ ልክ ሲመጣ አደርስለታለሁ እሱ ራሱ ይመልስልሃል።\n\n" + signature
            await event.reply(fallback_msg, reply_to=event.id, parse_mode='html')
            
            # ሰውየው Offline መልእክት እንደደረሰው መመዝገብ (ደጋግሞ እንዳይላክበት)
            memory.setdefault("fallback_sent", []).append(sender_id)
            save_memory(memory)
        else:
            print(f"🤐 [SILENT MODE] የኮታ ችግር አለ፣ ለ {sender_name} ግን ቀድሞ ስለተነገረው ዝም ብያለሁ።")

# ==========================================
# 5. Online ማሳያ
# ==========================================
async def keep_online():
    while True:
        try:
            await client(UpdateStatusRequest(offline=False))
            await asyncio.sleep(180) 
        except Exception:
            pass

print("AI MANAGER Final Version Started successfully!")
client.start()
client.loop.create_task(keep_online())
client.run_until_disconnected()
