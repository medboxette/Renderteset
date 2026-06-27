import os
import re
import psycopg2
import psycopg2.extras
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set")

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set")

# 🚨 الـ IDs ديال الأدمنز
ADMIN_IDS = [6243248782, 8373828587]

# 🌐 الـ ID ديال الجروب
GROUP_CHAT_ID = -1003929375047  

# ── Database ──────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            # 1. إنشاء جدول الطلبيات
            cur.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                group_msg_id BIGINT PRIMARY KEY,
                number      INTEGER NOT NULL,
                text        TEXT    NOT NULL,
                time        TEXT    NOT NULL,
                taken       BOOLEAN NOT NULL DEFAULT FALSE,
                done        BOOLEAN NOT NULL DEFAULT FALSE,
                taken_by    TEXT,
                taken_by_id BIGINT,
                phone       TEXT
            )
            """)
            
            # 2. إنشاء جدول النقاط
            cur.execute("""
            CREATE TABLE IF NOT EXISTS scores (
                username TEXT PRIMARY KEY,
                score    INTEGER NOT NULL DEFAULT 0,
                user_id  BIGINT
            )
            """)
            
            # فحص آمن لزيادة عمود user_id في جدول scores لتفادي أي كراش ف السيرفر
            try:
                cur.execute("ALTER TABLE scores ADD COLUMN IF NOT EXISTS user_id BIGINT")
                conn.commit()
            except Exception:
                conn.rollback()

            # 3. إنشاء العداد
            with conn.cursor() as cur2:
                cur2.execute("""
                CREATE TABLE IF NOT EXISTS counter (
                    id    INTEGER PRIMARY KEY DEFAULT 1,
                    value INTEGER NOT NULL DEFAULT 0
                )
                """)
                cur2.execute("""
                INSERT INTO counter (id, value)
                VALUES (1, 0)
                ON CONFLICT (id) DO NOTHING
                """)
                conn.commit()
    print("✅ Database initialized safely")

def db_increment_counter() -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE counter SET value = value + 1 WHERE id = 1 RETURNING value")
            value = cur.fetchone()[0]
            conn.commit()
            return value

def db_save_order(group_msg_id: int, order: dict):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO orders (group_msg_id, number, text, time, taken, done, taken_by, taken_by_id, phone)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (group_msg_id) DO UPDATE SET
                taken       = EXCLUDED.taken,
                done        = EXCLUDED.done,
                taken_by    = EXCLUDED.taken_by,
                taken_by_id = EXCLUDED.taken_by_id,
                phone       = EXCLUDED.phone
            """, (
                group_msg_id,
                order["number"],
                order["text"],
                order["time"],
                order["taken"],
                order["done"],
                order["taken_by"],
                order["taken_by_id"],
                order.get("phone")
            ))
            conn.commit()

def db_get_order(group_msg_id: int):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM orders WHERE group_msg_id = %s", (group_msg_id,))
            row = cur.fetchone()
            return dict(row) if row else None

def db_get_all_orders() -> list[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM orders ORDER BY number")
            return [dict(r) for r in cur.fetchall()]

def db_add_score(username: str, delta: int, user_id: int = None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            if user_id:
                cur.execute("""
                INSERT INTO scores (username, score, user_id) VALUES (%s, %s, %s)
                ON CONFLICT (username) DO UPDATE SET 
                    score = GREATEST(scores.score + %s, 0),
                    user_id = EXCLUDED.user_id
                """, (username, max(delta, 0), user_id, delta))
            else:
                cur.execute("""
                INSERT INTO scores (username, score) VALUES (%s, %s)
                ON CONFLICT (username) DO UPDATE SET score = GREATEST(scores.score + %s, 0)
                """, (username, max(delta, 0), delta))
            conn.commit()

def db_get_scores() -> list[tuple[str, int]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT username, score FROM scores ORDER BY score DESC")
            return cur.fetchall()

def db_get_user_id_by_username(username: str) -> int:
    username = username.replace("@", "").strip()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM scores WHERE username = %s OR username ILIKE %s", (username, username))
            row = cur.fetchone()
            return row[0] if row and row[0] else None

def db_get_stats() -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM orders")
            total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM orders WHERE done = TRUE")
            done = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM orders WHERE taken = TRUE AND done = FALSE")
            in_progress = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM orders WHERE taken = FALSE AND done = FALSE")
            waiting = cur.fetchone()[0]
            return {"total": total, "done": done, "in_progress": in_progress, "waiting": waiting}

def db_clear_all():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM orders")
            cur.execute("DELETE FROM scores")
            cur.execute("UPDATE counter SET value = 0 WHERE id = 1")
            conn.commit()

def db_clear_specific_order(group_msg_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM orders WHERE group_msg_id = %s", (group_msg_id,))
            conn.commit()

# ── Keyboards ─────────────────────────────────────────────────────────────────

def build_keyboard(taken: bool):
    if not taken:
        return InlineKeyboardMarkup([[InlineKeyboardButton("خديتها 🚚", callback_data="take")]])
        
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏁 تليفرات", callback_data="done"),
            InlineKeyboardButton("❌ لغيتها", callback_data="cancel"),
        ]
    ])

# ── Handlers ──────────────────────────────────────────────────────────────────

async def cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    text = update.message.text.strip()
    if text.startswith("/cmd"):
        text = text[4:].strip()

    if not text:
        await update.message.reply_text("⚠️ خاصك تكتب معلومات الطلبية بعد /cmd")
        return

    found_phones = re.findall(r'(?:\+212|0)[ \-_]*[567](?:[ \-_]*\d){8}', text)
    phones_str = ",".join(found_phones) if found_phones else None

    counter = db_increment_counter()  
    now = datetime.now().strftime("%H:%M")  

    try:
        group_msg = await context.bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=f"🔢 طلبية #{counter}\n🕒 {now}\n\n📦 طلبية جديدة:\n\n{text}",
            reply_markup=build_keyboard(taken=False),
        )
    except Exception as e:
        await update.message.reply_text(f"❌ فشل إرسال الطلبية للجروب.\nError: {e}")
        return

    await update.message.reply_text(f"✅ تم إرسال الطلبية #{counter} بنجاح إلى الجروب.")

    db_save_order(group_msg.message_id, {  
        "number": counter,  
        "text": text,  
        "time": now,  
        "taken": False,  
        "done": False,  
        "taken_by": None,  
        "taken_by_id": None,  
        "phone": phones_str
    })

async def cmd_to(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ هاد الأمر مخصص للأدمن فقط.")
        return

    full_text = update.message.text.strip()
    if full_text.startswith("/cmd_to"):
        full_text = full_text[7:].strip()

    parts = full_text.split(maxsplit=1)
    if len(parts) < 2:
        await update.message.reply_text("⚠️ الطريقة الصحيحة:\n`/cmd_to username_أو_id تفاصيل الطلبية`", parse_mode="Markdown")
        return

    target_driver = parts[0].strip()
    order_text = parts[1].strip()

    target_id = None
    driver_name = target_driver

    if target_driver.isdigit():
        target_id = int(target_driver)
    else:
        target_id = db_get_user_id_by_username(target_driver)

    if not target_id:
        await update.message.reply_text(f"❌ ما لقيتش هاد الليفرور ({target_driver}) ف السيستم.\nخاص يكون ديجا تفاعل مع البوت وضغط على /start أو خدا شي طلبية قبل.")
        return

    found_phones = re.findall(r'(?:\+212|0)[ \-_]*[567](?:[ \-_]*\d){8}', order_text)
    phones_str = ",".join(found_phones) if found_phones else None

    counter = db_increment_counter()  
    now = datetime.now().strftime("%H:%M")  

    formatted_text = order_text
    raw_phones = re.findall(r'(?:\+212|0)[ \-_]*[567](?:[ \-_]*\d){8}', formatted_text)
    for p in raw_phones:
        clean_digits = re.sub(r'[\s\-_]', '', p)
        if clean_digits.startswith('+212'):
            clean_digits = '0' + clean_digits[4:]
        if len(clean_digits) == 10 and clean_digits.startswith('0'):
            international_phone = "+212" + clean_digits[1:]
            formatted_text = formatted_text.replace(p, international_phone)

    final_text = f"🎯 طلبية موجهة ليك ديريكت من الأدمن:\n🔢 طلبية #{counter}\n🕒 {now}\n\n📦 تفاصيل الطلبية:\n\n{formatted_text}"

    try:
        private_msg = await context.bot.send_message(
            chat_id=target_id,
            text=final_text,
            reply_markup=build_keyboard(taken=True)
        )
    except Exception as e:
        await update.message.reply_text(f"❌ فشل إرسال الطلبية لخاص الليفرور.\nError: {e}")
        return

    await update.message.reply_text(f"🚀 تم إرسال الطلبية #{counter} مباشرة إلى خاص الليفرور بنجاح.")

    db_save_order(private_msg.message_id, {  
        "number": counter,  
        "text": order_text,  
        "time": now,  
        "taken": True,  
        "done": False,  
        "taken_by": driver_name,  
        "taken_by_id": target_id,  
        "phone": phones_str
    })
    db_add_score(driver_name, +1, user_id=target_id)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user.first_name or query.from_user.username or "ليفرور"
    user_id = query.from_user.id
    msg_id = query.message.message_id  
    data = query.data

    order = db_get_order(msg_id)

    if not order:  
        await query.answer("⚠️ هاد الطلبية ما كايناش ف السيستم أو قديمة", show_alert=True)  
        return  

    db_add_score(user, 0, user_id=user_id)

    if data == "take":  
        if order["taken"]:  
            await query.answer("❌ هاد الطلبية خداها شي واحد آخر", show_alert=True)  
            return  

        formatted_text = order['text']
        raw_phones = re.findall(r'(?:\+212|0)[ \-_]*[567](?:[ \-_]*\d){8}', formatted_text)
        
        for p in raw_phones:
            clean_digits = re.sub(r'[\s\-_]', '', p)
            if clean_digits.startswith('+212'):
                clean_digits = '0' + clean_digits[4:]
                
            if len(clean_digits) == 10 and clean_digits.startswith('0'):
                international_phone = "+212" + clean_digits[1:]
                formatted_text = formatted_text.replace(p, international_phone)

        final_text = f"✅ خديتيها بنجاح:\n🔢 طلبية #{order['number']}\n🕒 {order['time']}\n\n📦 تفاصيل الطلبية:\n\n{formatted_text}"

        try:
            private_msg = await context.bot.send_message(
                chat_id=user_id,
                text=final_text,
                reply_markup=build_keyboard(taken=True)
            )
        except Exception as e:
            print(f"Error sending private message: {e}")
            await query.answer("⚠️ خاصك ضروري تدخل عند البوت ف الخاص ودير /start عاد تقدر تاخد الطلبيات!", show_alert=True)
            return

        order["taken"] = True  
        order["taken_by"] = user  
        order["taken_by_id"] = user_id  
        
        db_clear_specific_order(msg_id)
        db_save_order(private_msg.message_id, order)
        db_add_score(user, +1, user_id=user_id)  

        try:
            await context.bot.delete_message(chat_id=GROUP_CHAT_ID, message_id=msg_id)
        except Exception as e:
            print(f"Error deleting group message: {e}")

        await query.answer("✅ خديتي الطلبية! شوف الخاص ديالك.")  

        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"🚚 إشعار جديد:\nالليفرور {user} خدا الطلبية #{order['number']}\n\n📦 الطلبية: {order['text']}",
                )
            except Exception as e:
                print(f"Error sending admin notification: {e}")

    elif data == "done":  
        if order["taken_by_id"] != user_id and user_id not in ADMIN_IDS:  
            await query.answer("❌ غير اللي خدا الطلبية هو اللي يقدر يدير تليفرات", show_alert=True)  
            return  

        order["done"] = True  
        db_save_order(msg_id, order)  

        await query.edit_message_text(  
            text=f"🏁 تليفرات بواسطة: {order['taken_by']}\n🔢 طلبية #{order['number']}\n🕒 {order['time']}\n\n📦 الطلبية:\n\n{order['text']}"  
        )  
        await query.answer("✅ تم تأكيد التوصيل")  

        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"🏁 إشعار جديد:\nالطلبية #{order['number']} تليفرات بنجاح بواسطة {order['taken_by']}! 🎉",
                )
            except Exception as e:
                print(f"Error sending admin notification: {e}")

    elif data == "cancel":  
        if order["taken_by_id"] != user_id and user_id not in ADMIN_IDS:  
            await query.answer("❌ غير اللي خدا الطلبية يقدر يلغيها", show_alert=True)  
            return  

        taken_by = order["taken_by"]  
        if taken_by:  
            db_add_score(taken_by, -1, user_id=user_id)  

        order["taken"] = False  
        order["taken_by"] = None  
        order["taken_by_id"] = None  

        try:
            new_group_msg = await context.bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text=f"🔄 (رجعات خاوية) طلبية #{order['number']}\n🕒 {order['time']}\n\n📦 الطلبية:\n\n{order['text']}",
                reply_markup=build_keyboard(taken=False)
            )
            db_clear_specific_order(msg_id)
            db_save_order(new_group_msg.message_id, order)
        except Exception as e:
            print(f"Error re-sending to group: {e}")

        await query.message.delete()
        await query.answer("❌ تم الإلغاء، الطلبية رجعات للجروب.")

        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"❌ إشعار جديد:\nالطلبية #{order['number']} تلغات من طرف {taken_by} ورجعات للجروب خاوية.",
                )
            except Exception as e:
                print(f"Error sending admin notification: {e}")

# ── باقي الأوامر الإحصائية ──────────────────────────────────────────────────────

async def list_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    all_orders = db_get_all_orders()
    if not all_orders:
        await update.message.reply_text("📋 ما كاين حتى طلبية دابا!")
        return

    msg = "📋 لائحة الطلبيات اليومية\n━━━━━━━━━━━━━━━\n"
    for i, o in enumerate(all_orders):  
        status_line = f"🟩 [#{o['number']}] 🕒 {o['time']}" if o["done"] else (f"🟦 [#{o['number']}] 🕒 {o['time']} 👤 قيد التوصيل ({o['taken_by']})" if o["taken"] else f"🟧 [#{o['number']}] 🕒 {o['time']}")
        msg += f"{status_line}\n📝 {o['text']}\n"
        if i < len(all_orders) - 1:
            msg += "────────────────\n"
    msg += "━━━━━━━━━━━━━━━"  
    await update.message.reply_text(msg)

async def my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name

    all_orders = db_get_all_orders()  
    mine = [o for o in all_orders if o["taken_by_id"] == user_id]  

    if not mine:  
        await update.message.reply_text("📭 ما واخد حتى طلبية دابا.")  
        return  

    msg = f"📦 الطلبيات ديال {user_name}:\n\n"  
    for o in mine:  
        msg += f"#{o['number']} [{o['time']}] {'🏁 تليفرات' if o['done'] else '✅ قيد التوصيل'} — {o['text']}\n"  

    await update.message.reply_text(msg)

async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    all_scores = db_get_scores()
    if not all_scores:
        await update.message.reply_text("🏆 ما كاين حتى واحد خدا شي طلبية!")
        return

    msg = "🏆 لائحة المتصدرين:\n\n"  
    medals = ["🥇", "🥈", "🥉"]  
    for i, (username, score) in enumerate(all_scores):  
        msg += f"{medals[i] if i < 3 else f'{i+1}.'} {username} — {score} طلبية\n"  

    await update.message.reply_text(msg)

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = db_get_stats()
    today = datetime.now().strftime("%d/%m/%Y")
    msg = f"📊 إحصائيات الطلبيات — {today}\n\n📦 المجموع: {s['total']}\n🏁 تليفرات: {s['done']}\n✅ جارية: {s['in_progress']}\n⏳ مازال ما تشدات: {s['waiting']}"
    await update.message.reply_text(msg)

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ هاد الأمر مخصص للأدمن فقط.")
        return

    db_clear_all()  
    await update.message.reply_text("🗑️ تم تصفير الطلبيات والنقاط بنجاح.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or update.effective_user.username or "ليفرور"
    db_add_score(user_name, 0, user_id=user_id)
    await update.message.reply_text("👋 أهلاً بيك ف بوت إدارة الطلبيات!")

# ── Main ──────────────────────────────────────────────────────────────────────

init_db()

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("cmd", cmd))
app.add_handler(CommandHandler("cmd_to", cmd_to))
app.add_handler(CommandHandler("list", list_orders))
app.add_handler(CommandHandler("myorders", my_orders))
app.add_handler(CommandHandler("top", top))
app.add_handler(CommandHandler("stats", stats))
app.add_handler(CommandHandler("clear", clear))
app.add_handler(CallbackQueryHandler(button))

print("✅ Bot running...")
PORT = int(os.environ.get("PORT", 8080))
app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN, webhook_url=f"https://renderteset-1.onrender.com/{TOKEN}")
