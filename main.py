import os
import threading
import psycopg2
import psycopg2.extras
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler
)

# --- 1. إعداد الاتصال بقاعدة البيانات ---
def get_conn():
    # كيقرا رابط قاعدة البيانات تلقائياً وبأمان من Render
    return psycopg2.connect(os.environ.get("DATABASE_URL"))

# دالة التحقق من الأدمن (يمكنك تعديلها حسب رغبتك)
def is_admin(update: Update) -> bool:
    # هنا كتحط الأي دي ديال الأدمن أو كتخليها ترجع True مؤقتاً
    # مثلاً: return update.effective_user.id == 12345678
    return True 

# --- 2. كاع دالات الأوامر (القديمة والجديدة) ---

async def cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 أهلاً بك! هدا أمر /cmd خدام بنجاح.")

async def list_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📋 هنا غاتبان قائمة الكوماندات.")

async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏆 هنا غايبان ترتيب أفضل الموصلين.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📊 حالة البوت وقاعدة البيانات مستقرة.")

async def reopen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 تم إعادة فتح الكوماند.")

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text=f"اضغط على الزر: {query.data}")

# الأوامر اللي صيفطتي ليا كاملة ومقادة:

async def done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("🚫 ما عندكش الصلاحية تستعمل هاد الأمر.")
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("⚠️ حدد رقم الكوماند. مثال: /done 3")
        return
    num = int(context.args[0])
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("SELECT * FROM orders WHERE number = %s", (num,))
            row = cur.fetchone()
            if not row:
                await update.message.reply_text(f"❌ ما لقيناش كوماند رقم #{num}.")
                return
            if row["done"]:
                await update.message.reply_text(f"⚠️ كوماند #{num} راه خلص من قبل.")
                return
            cur.execute("UPDATE orders SET done = TRUE WHERE number = %s", (num,))
    taker = row["taker"] or "مجهول"
    await update.message.reply_text(
        f"📦 تم التوصيل!\n\n🔢 كوماند #{num}\n🚚 {row['text']}\n👤 وصلها: {taker}"
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("🚫 ما عندكش الصلاحية تستعمل هاد الأمر.")
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("⚠️ حدد رقم الكوماند. مثال: /cancel 3")
        return
    num = int(context.args[0])
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("SELECT * FROM orders WHERE number = %s", (num,))
            row = cur.fetchone()
            if not row:
                await update.message.reply_text(f"❌ ما لقيناش كوماند رقم #{num}.")
                return
            cur.execute("DELETE FROM orders WHERE number = %s", (num,))
    await update.message.reply_text(f"🗑 تم إلغاء كوماند #{num}:\n\n{row['text']}")

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("🚫 ما عندكش الصلاحية تستعمل هاد الأمر.")
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM orders")
            cur.execute("DELETE FROM scores")
    await update.message.reply_text("🔄 تم الريسيت! الكوماندات والنقاط محذوفين من قاعدة البيانات.")


# --- 3. سيرفر Flask باش Render ما يطفيش البوت ---
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "Bot is Alive and Running!"

def run_flask():
    # Render كيعطي الـ Port تلقائياً ف هاد المتغير، وإلا مالقاهش كيخدم بـ 10000
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)


# --- 4. تشغيل السيرفر والبوت معاً ---
if __name__ == "__main__":
    # 1. كاري التوكن من البيئة بأمان
    TOKEN = os.environ.get("TOKEN")
    if not TOKEN:
        print("❌ خطأ: لم يتم العثور على متغير TOKEN ف إعدادات البيئة!")
        exit(1)

    # 2. بناء تطبيق التليجرام
    app = ApplicationBuilder().token(TOKEN).build()

    # 3. ربط الأوامر بالدوال ديالها
    app.add_handler(CommandHandler("cmd", cmd))
    app.add_handler(CommandHandler("list", list_orders))
    app.add_handler(CommandHandler("top", top))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("done", done))
    app.add_handler(CommandHandler("reopen", reopen))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CallbackQueryHandler(button))

    print("✅ Starting Flask server f الخلفية...")
    # تشغيل Flask ف خلفية البرنامج باش ما يحبسش البوت (Polling)
    threading.Thread(target=run_flask, daemon=True).start()
    
    print("✅ Telegram Bot is polling now...")
    app.run_polling()
