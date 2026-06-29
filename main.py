from __future__ import annotations
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

RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")
PORT = int(os.environ.get("PORT", 8080))

ADMIN_IDS = [6243248782, 8373828587]
GROUP_CHAT_ID = -1003929375047  

# ── Database ──────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
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
                    phone       TEXT,
                    admin_name  TEXT
                )
                """)
                try:
                    cur.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS admin_name TEXT")
                except Exception:
                    pass
                
                cur.execute("""
                CREATE TABLE IF NOT EXISTS scores (
                    username TEXT PRIMARY KEY,
                    score    INTEGER NOT NULL DEFAULT 0,
                    user_id  BIGINT
                )
                """)
                try:
                    cur.execute("ALTER TABLE scores ADD COLUMN IF NOT EXISTS user_id BIGINT")
                except Exception:
                    pass

                cur.execute("""
                CREATE TABLE IF NOT EXISTS counter (
                    id    INTEGER PRIMARY KEY DEFAULT 1,
                    value INTEGER NOT NULL DEFAULT 0
                )
                """)
                cur.execute("""
                INSERT INTO counter (id, value)
                VALUES (1, 0)
                ON CONFLICT (id) DO NOTHING
                """)
            conn.commit()
        print("✅ Database initialized safely")
    except Exception as e:
        print(f"⚠️ Error during database initialization: {e}")

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
            INSERT INTO orders (group_msg_id, number, text, time, taken, done, taken_by, taken_by_id, phone, admin_name)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (group_msg_id) DO UPDATE SET
                taken       = EXCLUDED.taken,
                done        = EXCLUDED.done,
                taken_by    = EXCLUDED.taken_by,
                taken_by_id = EXCLUDED.taken_by_id,
                phone       = EXCLUDED.phone,
                admin_name  = EXCLUDED.admin_name
            """, (
                group_msg_id,
                order["number"],
                order["text"],
                order["time"],
                order["taken"],
                order["done"],
                order["taken_by"],
                order["taken_by_id"],
                order.get("phone"),
                order.get("admin_name")
            ))
            conn.commit()

def db_get_order(group_msg_id: int):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM orders WHERE group_msg_id = %s", (group_msg_id,))
            row = cur.fetchone()
            return dict(row) if row else None

def db_get_order_by_number(number: int):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM orders WHERE number = %s", (number,))
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

def db_get_all_drivers_with_id() -> list[dict]:
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT username, user_id FROM scores WHERE user_id IS NOT NULL")
                return cur.fetchall()
    except Exception as e:
        print(f"Error fetching drivers: {e}")
        return []

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
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM orders")
                cur.execute("UPDATE scores SET score = 0")
                cur.execute("UPDATE counter SET value = 0 WHERE id = 1")
                conn.commit()
    except Exception as e:
        print(f"Error clearing db: {e}")

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

def build_only_take_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("خديتها 🚚", callback_data="take")]])

def build_drivers_keyboard(drivers_list: list, order_text: str):
    buttons = []
    for d in drivers_list:
        callback_data = f"assign_{d['user_id']}"
        buttons.append([InlineKeyboardButton(f"👤 {d['username']}", callback_data=callback_data)])
    return InlineKeyboardMarkup(buttons)

# ── Handlers ──────────────────────────────────────────────────────────────────

async def cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    admin_name = update.effective_user.first_name or "الأدمن"
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
            text=f"🔢 طلبية #{counter}\n🕒 {now}\n👤 بواسطة: {admin_name}\n\n📦 طلبية جديدة:\n\n{text}",
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
        "phone": phones_str,
        "admin_name": admin_name
    })

async def cmd_to(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ هاد الأمر مخصص للأدمن فقط.")
        return

    full_text = update.message.text.strip()
    if full_text.startswith("/cmd_to"):
        full_text = full_text[7:].strip()

    if not full_text:
        await update.message.reply_text("⚠️ الطريقة الصحيحة:\n`/cmd_to تفاصيل الطلبية هنا...`")
        return

    drivers = db_get_all_drivers_with_id()
    if not drivers:
        await update.message.reply_text("❌ ما كاين حتى ليفرور مسجل ف قاعدة البيانات حالياً.")
        return

    context.user_data['pending_order_text'] = full_text

    await update.message.reply_text(
        text="🚚 اختر الليفرور اللي بغيتي تصيفط ليه هاد الطلبية ديريكت:",
        reply_markup=build_drivers_keyboard(drivers, full_text)
    )

async def delete_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ هاد الأمر مخصص للأدمن فقط.")
        return

    if not context.args:
        await update.message.reply_text("⚠️ الطريقة الصحيحة: `/delete رقم_الطلبية`\nمثال: `/delete 5`")
        return

    try:
        order_num = int(context.args[0])
    except ValueError:
        await update.message.reply_text("⚠️ يرجى إدخال رقم طلبية صحيح.")
        return

    order = db_get_order_by_number(order_num)
    if not order:
        await update.message.reply_text(f"⚠️ الطلبية #{order_num} ما كايناش ف السيستم.")
        return

    msg_id = order["group_msg_id"]
    db_clear_specific_order(msg_id)

    try:
        await context.bot.delete_message(chat_id=GROUP_CHAT_ID, message_id=msg_id)
        await update.message.reply_text(f"🗑️ تم مسح الطلبية #{order_num} بنجاح من قاعدة البيانات ومن الجروب.")
    except Exception:
        await update.message.reply_text(f"🗑️ تم مسح الطلبية #{order_num} من قاعدة البيانات (رسالة الجروب قد تكون ممسوحة مسبقاً).")

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user.first_name or query.from_user.username or "ليفرور"
    user_id = query.from_user.id
    msg_id = query.message.message_id  
    data = query.data

    db_add_score(user, 0, user_id=user_id)

    if data.startswith("assign_"):
        if user_id not in ADMIN_IDS:
            await query.answer("❌ أنت لست الأدمن", show_alert=True)
            return

        admin_name = query.from_user.first_name or "الأدمن"
        target_id = int(data.split("_")[1])
        order_text = context.user_data.get('pending_order_text')

        if not order_text:
            await query.edit_message_text("⚠️ انتهت صلاحية الجلسة، عاود اكتب الأمر من جديد.")
            return

        drivers = db_get_all_drivers_with_id()
        driver_name = next((d['username'] for d in drivers if d['user_id'] == target_id), "ليفرور")

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

        # 🚀 التعديل هنا: الطلبية الموجهة للخاص تطلع بـ زر "خديتها 🚚" الأول بحال الجروب
        final_text = f"🎯 طلبية موجهة ليك ديريكت من الأدمن: {admin_name}\n🔢 طلبية #{counter}\n🕒 {now}\n\n📦 تفاصيل الطلبية:\n\n{formatted_text}"

        try:
            private_msg = await context.bot.send_message(
                chat_id=target_id,
                text=final_text,
                reply_markup=build_keyboard(taken=False) # 🔙 كتبدا بـ زر واحد "خديتها 🚚"
            )
        except Exception as e:
            await query.edit_message_text(f"❌ فشل إرسال الطلبية لـ {driver_name}.\nError: {e}")
            return

        await query.edit_message_text(f"🚀 تم إرسال الطلبية #{counter} مباشرة إلى خاص ({driver_name}) بنجاح.")

        db_save_order(private_msg.message_id, {  
            "number": counter,  
            "text": order_text,  
            "time": now,  
            "taken": False, # كتدخل خاوية حتى يبرك هو على الزر ف الخاص ديالو
            "done": False,  
            "taken_by": None,  
            "taken_by_id": None,  
            "phone": phones_str,
            "admin_name": admin_name
        })
        
        if 'pending_order_text' in context.user_data:
            del context.user_data['pending_order_text']
        return

    order = db_get_order(msg_id)
    if not order:  
        await query.answer("⚠️ هاد الطلبية ما كايناش ف السيستم أو قديمة", show_alert=True)  
        return  

    if data == "take":  
        if order["taken"]:  
            await query.answer("❌ هاد الطلبية خداها شي واحد آخر", show_alert=True)  
            return  

        order["taken"] = True  
        order["taken_by"] = user  
        order["taken_by_id"] = user_id
        db_add_score(user, +1, user_id=user_id)

        formatted_text = order['text']
        raw_phones = re.findall(r'(?:\+212|0)[ \-_]*[567](?:[ \-_]*\d){8}', formatted_text)
        for p in raw_phones:
            clean_digits = re.sub(r'[\s\-_]', '', p)
            if clean_digits.startswith('+212'):
                clean_digits = '0' + clean_digits[4:]
            if len(clean_digits) == 10 and clean_digits.startswith('0'):
                international_phone = "+212" + clean_digits[1:]
                formatted_text = formatted_text.replace(p, international_phone)

        origin_admin = order.get("admin_name") or "الأدمن"
        
        # 🚀 التعديل هنا: إذا كانت الميساج موجه أصلاً ف الخاص، غادي يدير ليه تعديل ويطلع الأزرار بجوج
        if update.effective_chat.type == "private":
            final_text = f"✅ خديتيها بنجاح:\n🔢 طلبية #{order['number']}\n🕒 {order['time']}\n👤 بواسطة: {origin_admin}\n\n📦 تفاصيل الطلبية:\n\n{formatted_text}"
        else:
            final_text = f"✅ خديتيها بنجاح:\n🔢 طلبية #{order['number']}\n🕒 {order['time']}\n👤 بواسطة: {origin_admin}\n\n📦 تفاصيل الطلبية:\n\n{formatted_text}"

        await query.edit_message_text(
            text=final_text,
            reply_markup=build_keyboard(taken=True) # 🚀 دابا غادي تولي تبان "تليفرات" و "لغيتها" ف البلاصة
        )
        
        db_save_order(msg_id, order)
        await query.answer("✅ خديتي الطلبية!")  

        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"🚚 إشعار جديد:\nالليفرور {user} خدا الطلبية #{order['number']} (بواسطة: {origin_admin})\n\n📦 الطلبية: {order['text']}",
                )
            except Exception as e:
                print(f"Error sending admin notification: {e}")

    elif data == "done":  
        if order["taken_by_id"] != user_id and user_id not in ADMIN_IDS:  
            await query.answer("❌ غير اللي خدا الطلبية هو اللي يقدر يدير تليفرات", show_alert=True)  
            return  

        order["done"] = True  
        db_save_order(msg_id, order)  

        try:
            start_time = datetime.strptime(order["time"], "%H:%M")
            now_time = datetime.now()
            start_time = start_time.replace(year=now_time.year, month=now_time.month, day=now_time.day)
            
            duration = now_time - start_time
            duration_minutes = int(duration.total_seconds() / 60)
            
            if duration_minutes < 60:
                time_taken_str = f"{duration_minutes} دقيقة"
            else:
                hours = duration_minutes // 60
                mins = duration_minutes % 60
                time_taken_str = f"{hours} ساعة و {mins} دقيقة"
        except Exception:
            time_taken_str = "غير محدد"

        origin_admin = order.get("admin_name") or "الأدمن"
        
        await query.edit_message_text(  
            text=f"🏁 تليفرات بواسطة: {order['taken_by']}\n🔢 طلبية #{order['number']}\n🕒 {order['time']}\n👤 بواسطة: {origin_admin}\n\n📦 الطلبية:\n\n{order['text']}\n⏱️ الوقت المستغرق: {time_taken_str}"  
        )  
        await query.answer("✅ تم تأكيد التوصيل")  

        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"🏁 إشعار جديد:\nالطلبية #{order['number']} تليفرات بنجاح بواسطة {order['taken_by']}! 🎉\n⏱️ الوقت المستغرق: {time_taken_str}",
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

        origin_admin = order.get("admin_name") or "الأدمن"
        
        await query.edit_message_text(
            text=f"🔄 (رجعات خاوية) طلبية #{order['number']}\n🕒 {order['time']}\n👤 بواسطة: {origin_admin}\n\n📦 الطلبية:\n\n{order['text']}",
            reply_markup=build_keyboard(taken=False)
        )
        
        db_save_order(msg_id, order)
        await query.answer("❌ تم الإلغاء، الطلبية رجعات خاوية.")

        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"❌ إشعار جديد:\nالطلبية #{order['number']} تلغات من طرف {taken_by} ورجعات خاوية.",
                )
            except Exception as e:
                print(f"Error sending admin notification: {e}")


async def list_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    all_orders = db_get_all_orders()
    if not all_orders:
        await update.message.reply_text("📋 ما كاين حتى طلبية دابا!")
        return

    msg = "📋 لائحة الطلبيات اليومية\n━━━━━━━━━━━━━━━\n"
    for i, o in enumerate(all_orders):  
        origin_admin = o.get("admin_name") or "الأدمن"
        status_line = f"🟩 [#{o['number']}] 🕒 {o['time']} (👤 {origin_admin})" if o["done"] else (f"🟦 [#{o['number']}] 🕒 {o['time']} 👤 قيد التوصيل ({o['taken_by']}) [من {origin_admin}]" if o["taken"] else f"🟧 [#{o['number']}] 🕒 {o['time']} (👤 {origin_admin})")
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
        origin_admin = o.get("admin_name") or "الأدمن"
        msg += f"#{o['number']} [{o['time']}] (من: {origin_admin}) {'🏁 تليفرات' if o['done'] else '✅ قيد التوصيل'} — {o['text']}\n"  

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
    await update.message.reply_text("🗑️ تم تصفير الطلبيات والسكورات بنجاح، واللوافريا بقاو مسجلين ف السيستم!")

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
app.add_handler(CommandHandler("delete", delete_order))
app.add_handler(CommandHandler("list", list_orders))
app.add_handler(CommandHandler("myorders", my_orders))
app.add_handler(CommandHandler("top", top))
app.add_handler(CommandHandler("stats", stats))
app.add_handler(CommandHandler("clear", clear))
app.add_handler(CallbackQueryHandler(button))

if RENDER_EXTERNAL_URL:
    print(f"🌐 Running with Webhook on port {PORT}...")
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        secret_token="MySuperSecretToken123",
        webhook_url=f"{RENDER_EXTERNAL_URL}/"
    )
else:
    print("💻 RENDER_EXTERNAL_URL not found, running with Polling...")
    app.run_polling()
