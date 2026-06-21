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
    with get_
