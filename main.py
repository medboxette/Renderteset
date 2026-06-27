import os, requests, psycopg2, psycopg2.extras
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters

# ── Config ──────────────────────────────────────────────────────────────────
TOKEN = os.environ.get("BOT_TOKEN")
DB_URL = os.environ.get("DATABASE_URL")
GROQ_KEY = os.environ.get("GROQ_API_KEY") 
ADMIN_IDS = [6243248782, 8373828587]
GROUP_ID = -1003929375047  

# ── Database ──────────────────────────────────────────────────────────────────
def get_db(): return psycopg2.connect(DB_URL)

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS orders (group_msg_id BIGINT PRIMARY KEY, number INTEGER, text TEXT, time TEXT, taken BOOLEAN DEFAULT FALSE, done BOOLEAN DEFAULT FALSE, taken_by TEXT, taken_by_id BIGINT)")
    cur.execute("CREATE TABLE IF NOT EXISTS counter (id INTEGER PRIMARY KEY DEFAULT 1, value INTEGER DEFAULT 0)")
    cur.execute("INSERT INTO counter (id, value) VALUES (1, 0) ON CONFLICT (id) DO NOTHING")
    conn.commit()
    conn.close()

# ── AI Refiner ────────────────────────────────────────────────────────────────
def refine(text):
    payload = {"model": "llama3-8b-8192", "messages": [{"role": "user", "content": f"صحح هاد الدارجة المغربية لطلب توصيل: {text}"}]}
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {GROQ_KEY}"}, json=payload)
        return r.json()['choices'][0]['message']['content']
    except: return text

# ── Handlers ──────────────────────────────────────────────────────────────────
async def handle_voice(u, c):
    msg = await u.message.reply_text("🎙️ جاري السمع والتصحيح...")
    file = await c.bot.get_file(u.message.voice.file_id)
    await file.download_to_drive("v.ogg")
    
    r = requests.post("https://api.groq.com/openai/v1/audio/transcriptions", headers={"Authorization": f"Bearer {GROQ_KEY}"}, files={"file": ("v.ogg", open("v.ogg", "rb")), "model": (None, "whisper-large-v3")})
    text = refine(r.json().get("text", ""))
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE counter SET value = value + 1 WHERE id = 1 RETURNING value")
    num = cur.fetchone()[0]
    conn.commit()
    
    group_msg = await c.bot.send_message(GROUP_ID, f"🔢 طلبية #{num}\n📦 {text}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("خديتها 🚚", callback_data="take")]]))
    cur.execute("INSERT INTO orders (group_msg_id, number, text) VALUES (%s, %s, %s)", (group_msg.message_id, num, text))
    conn.commit()
    
    await msg.edit_text(f"✅ تم الإرسال للجروب.")
    os.remove("v.ogg")

async def cmd(u, c):
    text = u.message.text.replace("/cmd", "").strip()
    if text: await process_manual_order(text, c, u.message)

async def process_manual_order(text, c, msg):
    # نفس منطق handle_voice (العداد والارسال)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE counter SET value = value + 1 WHERE id = 1 RETURNING value")
    num = cur.fetchone()[0]
    conn.commit()
    await c.bot.send_message(GROUP_ID, f"🔢 طلبية #{num}\n📦 {text}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("خديتها 🚚", callback_data="take")]]))
    await msg.reply_text("✅ تم!")

async def list_orders(u, c):
    cur = get_db().cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM orders WHERE done = FALSE")
    orders = cur.fetchall()
    await u.message.reply_text("\n".join([f"#{o['number']} - {o['text']}" for o in orders]) or "لا توجد طلبات.")

# ── Main ──────────────────────────────────────────────────────────────────────
init_db()
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(MessageHandler(filters.VOICE, handle_voice))
app.add_handler(CommandHandler("cmd", cmd))
app.add_handler(CommandHandler("list", list_orders))
app.add_handler(CommandHandler("clear", lambda u, c: get_db().cursor().execute("DELETE FROM orders;") or u.message.reply_text("🗑️")))
app.run_webhook(listen="0.0.0.0", port=int(os.environ.get("PORT", 8080)), url_path=TOKEN, webhook_url=f"https://renderteset-1.onrender.com/{TOKEN}")
