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
                    taken_by_id BIGINT
                )
            """)
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
                INSERT INTO orders (msg_id, number, text, time, taken, done, taken_by, taken_by_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (msg_id) DO UPDATE SET
                    taken       = EXCLUDED.taken,
                    done        = EXCLUDED.done,
                    taken_by    = EXCLUDED.taken_by,
                    taken_by_id = EXCLUDED.taken_by_id
            """, (
                msg_id,
                order["number"],
                order["text"],
                order["time"],
                order["taken"],
                order["done"],
                order["taken_by"],
                order["taken_by_id"],
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
                    taken_by_id = %s
                WHERE msg_id = %s
            """, (
                order["taken"],
                order["done"],
                order["taken_by"],
                order["taken_by_id"],
                msg_id,
            ))
        conn.commit()


def db_get_order(msg_id: int) -> dict | None:
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
            [InlineKeyboardButton("✅ قبول", callback_data="take")]
        ])
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏁 مكمّل", callback_data="done"),
            InlineKeyboardButton("❌ إلغاء", callback_data="cancel"),
        ]
    ])


# ── Handlers ──────────────────────────────────────────────────────────────────

async def cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text("⚠️ خاصك تكتب نص الكوماند بعد /cmd")
        return

    counter = db_increment_counter()
    now = datetime.now().strftime("%H:%M")

    msg = await update.message.reply_text(
        f"🔢 كوماند #{counter}\n🕒 {now}\n\n🚚 كوموند جديد:\n\n{text}",
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
    })


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user.first_name
    user_id = query.from_user.id
    msg_id = query.message.message_id
    data = query.data

    order = db_get_order(msg_id)
    if not order:
        await query.answer("⚠️ هاد الكوماند ماشي موجود")
        return

    if data == "take":
        if order["taken"]:
            await query.answer("❌ هاد الكوموند خذاها شي واحد", show_alert=True)
            return

        order["taken"] = True
        order["taken_by"] = user
        order["taken_by_id"] = user_id
        db_update_order(msg_id, order)
        db_add_score(user, +1)

        await query.edit_message_text(
            f"✅ خذاها: {user}\n🔢 كوماند #{order['number']}\n🕒 {order['time']}\n\n🚚 كوموند جديد:\n\n{order['text']}",
            reply_markup=build_keyboard(taken=True),
        )
        await query.answer()

    elif data == "done":
        if order["taken_by_id"] != user_id and user_id not in ADMIN_IDS:
            await query.answer("❌ غير اللي خذا الكوماند يقدر يكملها", show_alert=True)
            return

        order["done"] = True
        db_update_order(msg_id, order)

        await query.edit_message_text(
            f"🏁 مكمّل بواسطة: {order['taken_by']}\n🔢 كوماند #{order['number']}\n🕒 {order['time']}\n\n🚚 كوموند جديد:\n\n{order['text']}"
        )
        await query.answer("✅ تم تأكيد الإكمال")

    elif data == "cancel":
        if order["taken_by_id"] != user_id and user_id not in ADMIN_IDS:
            await query.answer("❌ غير اللي خذا الكوماند يقدر يلغيها", show_alert=True)
            return

        taken_by = order["taken_by"]
        if taken_by:
            db_add_score(taken_by, -1)

        order["taken"] = False
        order["taken_by"] = None
        order["taken_by_id"] = None
        db_update_order(msg_id, order)

        await query.edit_message_text(
            f"🔢 كوماند #{order['number']}\n🕒 {order['time']}\n\n🚚 كوموند جديد:\n\n{order['text']}",
            reply_markup=build_keyboard(taken=False),
        )
        await query.answer("❌ تم الإلغاء، الكوماند رجع متاح")


async def list_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    all_orders = db_get_all_orders()
    if not all_orders:
        await update.message.reply_text("📋 ما كاين حتى كوماند!")
        return

    msg = "📋 *لائحة الكوماندات:*\n\n"
    for o in all_orders:
        if o["done"]:
            status = "🏁 مكمّل"
        elif o["taken"]:
            status = f"✅ مخذوز ({o['taken_by']})"
        else:
            status = "⏳ في الانتظار"
        msg += f"#{o['number']} [{o['time']}] {status} — {o['text']}\n"

    await update.message.reply_text(msg, parse_mode="Markdown")


async def my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name

    all_orders = db_get_all_orders()
    mine = [o for o in all_orders if o["taken_by_id"] == user_id]

    if not mine:
        await update.message.reply_text("📭 ما عندك حتى كوماند مخذوز.")
        return

    msg = f"📦 *الكوماندات ديال {user_name}:*\n\n"
    for o in mine:
        status = "🏁 مكمّل" if o["done"] else "✅ مخذوز"
        msg += f"#{o['number']} [{o['time']}] {status} — {o['text']}\n"

    await update.message.reply_text(msg, parse_mode="Markdown")


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    all_scores = db_get_scores()
    if not all_scores:
        await update.message.reply_text("🏆 ما كاين حتى واحد خذا كوماند!")
        return

    msg = "🏆 *لائحة المتصدرين:*\n\n"
    medals = ["🥇", "🥈", "🥉"]

    for i, (username, score) in enumerate(all_scores):
        medal = medals[i] if i < 3 else f"{i+1}."
        msg += f"{medal} {username} — {score} كوماند\n"

    await update.message.reply_text(msg, parse_mode="Markdown")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = db_get_stats()
    today = datetime.now().strftime("%d/%m/%Y")
    msg = (
        f"📊 *إحصائيات الكوماندات — {today}*\n\n"
        f"📦 المجموع: {s['total']}\n"
        f"🏁 مكملة: {s['done']}\n"
        f"✅ جارية: {s['in_progress']}\n"
        f"⏳ في الانتظار: {s['waiting']}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ هاد الأمر مخصص للأدمن فقط.")
        return

    db_clear_all()
    await update.message.reply_text("🗑️ تم تصفير الكوماندات والنقاط.")


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
