import os
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

ADMIN_IDS = [6243248782]


# ── Database ──────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    msg_id      INTEGER PRIMARY KEY,
                    number      INTEGER NOT NULL,
                    text        TEXT    NOT NULL,
                    time        TEXT    NOT NULL,
                    taken       BOOLEAN NOT NULL DEFAULT FALSE,
                    done        BOOLEAN NOT NULL DEFAULT FALSE,
                    taken_by    TEXT,
                    taken_by_id BIGINT,
                    reply_to_id INTEGER
                )
            """)
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='orders'")
            columns = [row[0] for row in cur.fetchall()]
            if 'reply_to_id' not in columns:
                cur.execute("ALTER TABLE orders ADD COLUMN reply_to_id INTEGER;")
                
            cur.execute("""
                CREATE TABLE IF NOT EXISTS scores (
                    username TEXT PRIMARY KEY,
                    score    INTEGER NOT NULL DEFAULT 0
                )
            """)
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
    print("✅ Database initialized")


def db_get_counter() -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM counter WHERE id = 1")
            row = cur.fetchone()
            return row[0] if row else 0


def db_increment_counter() -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE counter SET value = value + 1 WHERE id = 1
                RETURNING value
            """)
            value = cur.fetchone()[0]
        conn.commit()
    return value


def db_save_order(msg_id: int, order: dict):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO orders (msg_id, number, text, time, taken, done, taken_by, taken_by_id, reply_to_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (msg_id) DO UPDATE SET
                    taken       = EXCLUDED.taken,
                    done        = EXCLUDED.done,
                    taken_by    = EXCLUDED.taken_by,
                    taken_by_id = EXCLUDED.taken_by_id,
                    reply_to_id = EXCLUDED.reply_to_id
            """, (
                msg_id,
                order["number"],
                order["text"],
                order["time"],
                order["taken"],
                order["done"],
                order["taken_by"],
                order["taken_by_id"],
                order.get("reply_to_id"),
            ))
        conn.commit()


def db_update_order(msg_id: int, order: dict):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE orders SET
                    taken       = %s,
                    done        = %s,
                    taken_by    = %s,
                    taken_by_id = %s,
                    reply_to_id = %s
                WHERE msg_id = %s
            """, (
                order["taken"],
                order["done"],
                order["taken_by"],
                order["taken_by_id"],
                order.get("reply_to_id"),
                msg_id,
            ))
        conn.commit()


def db_get_order(msg_id: int):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM orders WHERE msg_id = %s", (msg_id,))
            row = cur.fetchone()
            return dict(row) if row else None


def db_get_all_orders() -> list[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM orders ORDER BY number")
            return [dict(r) for r in cur.fetchall()]


def db_add_score(username: str, delta: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
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


# ── Keyboards ─────────────────────────────────────────────────────────────────

def build_keyboard(taken: bool):
    if not taken:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("قبطتها 🚚", callback_data="take")]
        ])
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏁 تليفرات", callback_data="done"),
            InlineKeyboardButton("❌ لغيتها", callback_data="cancel"),
        ]
    ])


# ── Handlers ──────────────────────────────────────────────────────────────────

async def cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == 'private':
        if len(context.args) < 2:
            await update.message.reply_text(
                "⚠️ طريقة كتابة الأمر فالخاص خاصها تكون هكا:\n"
                "`/cmd [chat_id] [نص الكوموند]`"
            )
            return
        
        try:
            livreur_chat_id = int(context.args[0])
            text = " ".join(context.args[1:])
            
            counter = db_increment_counter()
            now = datetime.now().strftime("%H:%M")
            
            msg = await context.bot.send_message(
                chat_id=livreur_chat_id,
                text=f"🔢 طلبية #{counter}\n🕒 {now}\n\n📦 طلبية جديدة خاصة بك:\n\n{text}",
                reply_markup=build_keyboard(taken=False)
            )
            
            db_save_order(msg.message_id, {
                "number": counter,
                "text": text,
                "time": now,
                "taken": False,
                "done": False,
                "taken_by": None,
                "taken_by_id": None,
                "reply_to_id": update.message.message_id
            })
            
            await update.message.reply_text(f"✅ تم إرسال الطلبية #{counter} بنجاح إلى الليفرور فالخاص.")
            
        except ValueError:
            await update.message.reply_text("❌ خطأ: الـ Chat ID خاصو يكون أرقام فقط!")
        except Exception as e:
            await update.message.reply_text(f"❌ وقع خطأ: {e}\n(تأكد بلي الليفرور ديجا دار /start للبوت)")
            
    else:
        text = " ".join(context.args)
        if not text:
            await update.message.reply_text("⚠️ خاصك تكتب معلومات الطلبية بعد /cmd")
            return

        counter = db_increment_counter()
        now = datetime.now().strftime("%H:%M")

        msg = await update.message.reply_text(
            f"🔢 طلبية #{counter}\n🕒 {now}\n\n📦 طلبية جديدة:\n\n{text}",
            reply_markup=build_keyboard(taken=False),
        )

        db_save_order(msg.message_id, {
            "number": counter,
            "text": text,
            "time": now,
            "taken": False,
            "done": False,
            "taken_by": None,
            "taken_by_id": None,
            "reply_to_id": update.message.message_id
        })


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user.first_name
    user_id = query.from_user.id
    msg_id = query.message.message_id
    chat_id = query.message.chat_id
    chat_type = query.message.chat.type
    data = query.data

    order = db_get_order(msg_id)
    if not order:
        await query.answer("⚠️ هاد الطلبية ما كايناش")
        return

    if data == "take":
        if order["taken"]:
            await query.answer("❌ هاد الطلبية قبطها شي واحد آخر", show_alert=True)
            return

        order["taken"] = True
        order["taken_by"] = user
        order["taken_by_id"] = user_id
        db_update_order(msg_id, order)
        db_add_score(user, +1)

        if chat_type != 'private':
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                if order.get("reply_to_id"):
                    await context.bot.delete_message(chat_id=chat_id, message_id=order["reply_to_id"])
                await query.answer("✅ قبطتيها! وتم الحذف من الكروب.", show_alert=True)
            except Exception as e:
                await query.answer(f"⚠️ تقبطات ولكن وقع مشكل فالحذف: {e}", show_alert=True)
        else:
            await query.edit_message_text(
                f"✅ قبطها: {user}\n🔢 طلبية #{order['number']}\n🕒 {order['time']}\n\n📦 طلبية جديدة:\n\n{order['text']}",
                reply_markup=build_keyboard(taken=True),
            )
            await query.answer()

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"📦 *تفاصيل الطلبية اللي قبطتي:*\n\n"
                     f"🔢 طلبية *#{order['number']}*\n"
                     f"🕒 الوقت: {order['time']}\n\n"
                     f"📋 *معلومات الشحن:*\n{order['text']}",
                reply_markup=build_keyboard(taken=True),
                parse_mode="Markdown"
            )
        except Exception:
            pass

        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"🔔 *إشعار تحديث الطلب:*\n\n"
                         f"🚚 الليفرور *{user}* قبط الطلبية *#{order['number']}*\n"
                         f"📋 نص الكوموند:\n_{order['text']}_",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

    elif data == "done":
        if order["taken_by_id"] != user_id and user_id not in ADMIN_IDS:
            await query.answer("❌ غير اللي قبط الطلبية هو اللي يقدر يدير تليفرات", show_alert=True)
            return

        order["done"] = True
        db_update_order(msg_id, order)

        try:
            await query.edit_message_text(
                f"🏁 تليفرات بواسطة: {order['taken_by']}\n🔢 طلبية #{order['number']}\n🕒 {order['time']}\n\n📦 طلبية جديدة:\n\n{order['text']}"
            )
        except Exception:
            pass
        await query.answer("✅ تم تأكيد التوصيل")

        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"🏁 *إشعار توصيل:*\n\n"
                         f"الكوموند *#{order['number']}* تليفرات بنجاح بواسطة *{order['taken_by']}* 🎉",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

    elif data == "cancel":
        if order["taken_by_id"] != user_id and user_id not in ADMIN_IDS:
            await query.answer("❌ غير اللي قبط الطلبية يقدر يلغيها", show_alert=True)
            return

        taken_by = order["taken_by"]
        if taken_by:
            db_add_score(taken_by, -1)

        order["taken"] = False
        order["taken_by"] = None
        order["taken_by_id"] = None
        db_update_order(msg_id, order)

        try:
            await query.edit_message_text(
                f"🔢 طلبية #{order['number']}\n🕒 {order['time']}\n\n📦 طلبية جديدة:\n\n{order['text']}",
                reply_markup=build_keyboard(taken=False),
            )
        except Exception:
            pass
        await query.answer("❌ تم الإلغاء، الطلبية رجعات خاوية")

        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"⚠️ *إشعار إلغاء:*\n\n"
                         f"الكوموند *#{order['number']}* تفتح إلغاؤها ورجعات متاحة.",
                    parse_mode="Markdown"
                )
            except Exception:
                pass


async def list_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    all_orders = db_get_all_orders()
    if not all_orders:
        await update.message.reply_text("📋 ما كاين حتى طلبية دابا!")
        return

    msg = "📋 *لائحة الطلبيات:*\n\n"
    for o in all_orders:
        if o["done"]:
            status = "🏁 تليفرات"
        elif o["taken"]:
            status = f"✅ مقبوطة ({o['taken_by']})"
        else:
            status = "⏳ مازال ما تقبطات"
        msg += f"#{o['number']} [{o['time']}] {status} — {o['text']}\n"

    await update.message.reply_text(msg, parse_mode="Markdown")


async def my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name

    all_orders = db_get_all_orders()
    mine = [o for o in all_orders if o["taken_by_id"] == user_id]

    if not mine:
        await update.message.reply_text("📭 ما قابط حتى طلبية دابا.")
        return

    msg = f"📦 *الطلبيات ديال {user_name}:*\n\n"
    for o in mine:
        status = "🏁 تليفرات" if o["done"] else "✅ مقبوطة"
        msg += f"#{o['number']} [{o['time']}] {status} — {o['text']}\n"

    await update.message.reply_text(msg, parse_mode="Markdown")


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    all_scores = db_get_scores()
    if not all_scores:
        await update.message.reply_text("🏆 ما كاين حتى واحد قبط شي طلبية!")
        return

    msg = "🏆 *لائحة المتصدرين:*\n\n"
    medals = ["🥇", "🥈", "🥉"]

    for i, (username, score) in enumerate(all_scores):
        medal = medals[i] if i < 3 else f"{i+1}."
        msg += f"{medal} {username} — {score} طلبية\n"

    await update.message.reply_text(msg, parse_mode="Markdown")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = db_get_stats()
    today = datetime.now().strftime("%d/%m/%Y")
    msg = (
        f"📊 *إحصائيات الطلبيات — {today}*\n\n"
        f"📦 المجموع: {s['total']}\n"
        f"🏁 تليفرات: {s['done']}\n"
        f"✅ جارية: {s['in_progress']}\n"
        f"⏳ مازال ما تقبطات: {s['waiting']}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ هاد الأمر مخصص للأدمن فقط.")
        return

    db_clear_all()
    await update.message.reply_text("🗑️ تم تصفير الطلبيات والنقاط بنجاح.")


# ── Main ──────────────────────────────────────────────────────────────────────

init_db()

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("cmd", cmd))
app.add_handler(CommandHandler("list", list_orders))
app.add_handler(CommandHandler("myorders", my_orders))
app.add_handler(CommandHandler("top", top))
app.add_handler(CommandHandler("stats", stats))
app.add_handler(CommandHandler("clear", clear))
app.add_handler(CallbackQueryHandler(button))

print("✅ Bot running with database persistence...")
import os
PORT = int(os.environ.get("PORT", 8080))
app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN, webhook_url=f"https://renderteset-1.onrender.com/{TOKEN}")
        
