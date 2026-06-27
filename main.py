import os
import re
import psycopg2
import psycopg2.extras
import requests
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

# ── Config ──────────────────────────────────────────────────────────────────
TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY") 
ADMIN_IDS = [6243248782, 8373828587]
GROUP_CHAT_ID = -1003929375047  

# ── AI Refiner (الإضافة الجديدة) ──────────────────────────────────────────────
def refine_text_with_ai(raw_text: str) -> str:
    if not GROQ_API_KEY: return raw_text
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "llama3-8b-8192",
        "messages": [
            {"role": "system", "content": "أنت خبير في تصحيح الدارجة المغربية. مهمتك تصحيح النص إملائياً وتنسيقه لطلب توصيل. أجب فقط بالنص المصحح."},
            {"role": "user", "content": f"صحح هذا النص: {raw_text}"}
        ]
    }
    try:
        response = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload)
        return response.json()['choices'][0]['message']['content'].strip()
    except: return raw_text

# ── Database (نفس الكود ديالك) ───────────────────────────────────────────────
def get_conn(): return psycopg2.connect(DATABASE_URL)

def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS orders (group_msg_id BIGINT PRIMARY KEY, number INTEGER NOT NULL, text TEXT NOT NULL, time TEXT NOT NULL, taken BOOLEAN NOT NULL DEFAULT FALSE, done BOOLEAN NOT NULL DEFAULT FALSE, taken_by TEXT, taken_by_id BIGINT, phone TEXT)")
            cur.execute("CREATE TABLE IF NOT EXISTS scores (username TEXT PRIMARY KEY, score INTEGER NOT NULL DEFAULT 0)")
            cur.execute("CREATE TABLE IF NOT EXISTS counter (id INTEGER PRIMARY KEY DEFAULT 1, value INTEGER NOT NULL DEFAULT 0)")
            cur.execute("INSERT INTO counter (id, value) VALUES (1, 0) ON CONFLICT (id) DO NOTHING")
            conn.commit()

# (باقي دوال الداتابيز خليتها كيف ما هي في كودك)
def db_increment_counter():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE counter SET value = value + 1 WHERE id = 1 RETURNING value")
            return cur.fetchone()[0]

def db_save_order(msg_id, order):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO orders (group_msg_id, number, text, time, taken, done, taken_by, taken_by_id, phone) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (group_msg_id) DO UPDATE SET taken=EXCLUDED.taken, done=EXCLUDED.done, taken_by=EXCLUDED.taken_by, taken_by_id=EXCLUDED.taken_by_id", (msg_id, order["number"], order["text"], order["time"], order["taken"], order["done"], order["taken_by"], order["taken_by_id"], order.get("phone")))
            conn.commit()

# ── Handlers ──────────────────────────────────────────────────────────────────
# زدنا هاد الدالة باش تخدم معاها التصحيح الصوتي
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("🎙️ جاري المعالجة...")
    file = await context.bot.get_file(update.message.voice.file_id)
    await file.download_to_drive("voice.ogg")
    
    with open("voice.ogg", "rb") as f:
        resp = requests.post("https://api.groq.com/openai/v1/audio/transcriptions", headers={"Authorization": f"Bearer {GROQ_API_KEY}"}, files={"file": ("voice.ogg", f, "audio/ogg"), "model": (None, "whisper-large-v3")})
    
    raw_text = resp.json().get("text", "")
    refined_text = refine_text_with_ai(raw_text)
    
    # دابا كمل الخدمة العادية ديالك باستعمال refined_text
    # (يمكن ليك تعيط لدالة cmd أو تنسخ المنطق ديالها هنا)
    await status_msg.edit_text(f"✅ تم التصحيح:\n{refined_text}")
    os.remove("voice.ogg")

# ── Main Setup ────────────────────────────────────────────────────────────────
init_db()
app = ApplicationBuilder().token(TOKEN).build()
# هنا كتزيد الهاندلرز ديالك كاملين (cmd, list, top, stats, etc...)
app.add_handler(MessageHandler(filters.VOICE, handle_voice))
# ... أضف باقي الـ Handlers الخاصة بك هنا ...

app.run_webhook(listen="0.0.0.0", port=int(os.environ.get("PORT", 8080)), url_path=TOKEN, webhook_url=f"https://renderteset-1.onrender.com/{TOKEN}")
