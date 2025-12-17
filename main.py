"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💎 TELEGRAM FILE STORE BOT v4.0 (Stable & Fixed)
🔥 Thread-Safe SQLite | Render Ready | Anti-Crash
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import sys
import time
import logging
import sqlite3
import threading
import secrets
import socket
from functools import wraps
from datetime import datetime

import telebot
from telebot import types

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ⚙️ CONFIGURATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

BOT_TOKEN = os.environ.get('BOT_TOKEN')
OWNER_ID = int(os.environ.get('OWNER_ID', '0'))
BIN_CHANNEL = int(os.environ.get('BIN_CHANNEL', '0'))  # Storage Channel
LOG_CHANNEL = int(os.environ.get('LOG_CHANNEL', '0'))  # Log Channel

# DB Config
DB_NAME = "bot_data.db"
ADMIN_LIST = [OWNER_ID] 

# Initialize Bot
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown", threaded=True, num_threads=5)
logger = telebot.logger
telebot.logger.setLevel(logging.INFO)

# Global Memory for Batches (RAM)
user_batches = {}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🗄️ DATABASE ENGINE (Thread-Safe Fix)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Database:
    def __init__(self, db_file):
        self.db_file = db_file
        self.init_db()

    def get_conn(self):
        # Open a NEW connection for every thread to prevent locking
        conn = sqlite3.connect(self.db_file, check_same_thread=False)
        conn.row_factory = sqlite3.Row  # Access columns by name
        return conn

    def init_db(self):
        conn = self.get_conn()
        with conn:
            # Users
            conn.execute('''CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                role TEXT DEFAULT 'user',
                banned INTEGER DEFAULT 0
            )''')
            
            # Files
            conn.execute('''CREATE TABLE IF NOT EXISTS files (
                file_code TEXT PRIMARY KEY,
                file_name TEXT,
                mime_type TEXT,
                message_id INTEGER,
                channel_id INTEGER,
                uploader_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')

            # Batches
            conn.execute('''CREATE TABLE IF NOT EXISTS batches (
                batch_id TEXT PRIMARY KEY,
                batch_name TEXT,
                owner_id INTEGER,
                file_codes TEXT
            )''')

            # User Channels
            conn.execute('''CREATE TABLE IF NOT EXISTS channels (
                user_id INTEGER PRIMARY KEY,
                channel_id INTEGER,
                channel_title TEXT
            )''')

            # Settings
            conn.execute('''CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )''')
        conn.close()

    # --- GENERIC EXECUTION ---
    def execute(self, query, params=()):
        conn = self.get_conn()
        try:
            with conn:
                conn.execute(query, params)
        finally:
            conn.close()

    def fetchone(self, query, params=()):
        conn = self.get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.fetchone()
        finally:
            conn.close()

    def fetchall(self, query, params=()):
        conn = self.get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.fetchall()
        finally:
            conn.close()

    # --- METHODS ---
    def add_user(self, uid):
        self.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (uid,))

    def add_file(self, code, name, mime, mid, cid, uid):
        self.execute('INSERT INTO files (file_code, file_name, mime_type, message_id, channel_id, uploader_id) VALUES (?,?,?,?,?,?)',
                     (code, name, mime, mid, cid, uid))

    def create_batch(self, bid, name, uid, codes):
        self.execute('INSERT INTO batches VALUES (?,?,?,?)', (bid, name, uid, ",".join(codes)))

    def get_batch(self, bid):
        return self.fetchone('SELECT * FROM batches WHERE batch_id = ?', (bid,))

    def search_files(self, uid, query):
        return self.fetchall("SELECT file_code, file_name FROM files WHERE uploader_id = ? AND file_name LIKE ? LIMIT 10", (uid, f"%{query}%"))

    def get_setting(self, key, default=None):
        res = self.fetchone('SELECT value FROM settings WHERE key = ?', (key,))
        return res[0] if res else default

    def set_setting(self, key, val):
        self.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, str(val)))

db = Database(DB_NAME)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🛠️ UTILS & DECORATORS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_code(length=6): return secrets.token_urlsafe(length)

def is_subscribed(user_id):
    fsub_channel = db.get_setting("fsub_channel")
    if not fsub_channel: return True
    try:
        chat_member = bot.get_chat_member(fsub_channel, user_id)
        if chat_member.status in ['left', 'kicked']: return False
        return True
    except: return True

def check_user(func):
    @wraps(func)
    def wrapper(message, *args, **kwargs):
        # Handle CallbackQuery or Message
        if isinstance(message, types.CallbackQuery):
            uid = message.from_user.id
            msg_obj = message.message
        else:
            uid = message.from_user.id
            msg_obj = message

        db.add_user(uid)
        
        # Check Ban
        user_data = db.fetchone('SELECT banned FROM users WHERE user_id = ?', (uid,))
        if user_data and user_data['banned']: return

        # Check Maintenance
        if db.get_setting("maintenance") == "1" and uid not in ADMIN_LIST:
            bot.send_message(uid, "⛔ **System Under Maintenance**")
            return

        # Check Force Sub (Only for /start commands)
        if isinstance(message, types.Message) and message.text and message.text.startswith("/start"):
            if not is_subscribed(uid):
                fsub_id = db.get_setting("fsub_channel")
                try: link = bot.create_chat_invite_link(fsub_id, member_limit=1).invite_link
                except: link = "https://t.me/"
                
                kb = types.InlineKeyboardMarkup()
                kb.add(types.InlineKeyboardButton("👉 Join Channel", url=link))
                kb.add(types.InlineKeyboardButton("🔄 Try Again", url=f"https://t.me/{bot.get_me().username}?start={message.text.split()[1] if len(message.text.split())>1 else ''}"))
                bot.reply_to(message, "⚠️ **Please Join Our Channel**", reply_markup=kb)
                return

        return func(message, *args, **kwargs)
    return wrapper

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🎮 KEYBOARDS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main_menu(uid):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📦 Create Batch", callback_data="batch_start"),
        types.InlineKeyboardButton("📂 My Files", callback_data="my_files"),
        types.InlineKeyboardButton("⚙️ Settings", callback_data="user_settings"),
        types.InlineKeyboardButton("🔍 Search", callback_data="search_mode")
    )
    if uid in ADMIN_LIST:
        kb.add(types.InlineKeyboardButton("🛡️ Admin Panel", callback_data="admin_panel"))
    return kb

def admin_panel():
    maint = "🟢" if db.get_setting("maintenance") != "1" else "🔴"
    fsub = "✅" if db.get_setting("fsub_channel") else "❌"
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📊 Stats", callback_data="adm_stats"),
        types.InlineKeyboardButton("📢 Broadcast", callback_data="adm_broadcast"),
        types.InlineKeyboardButton(f"Maint: {maint}", callback_data="adm_maint"),
        types.InlineKeyboardButton(f"FSub: {fsub}", callback_data="adm_fsub"),
        types.InlineKeyboardButton("🔙 Home", callback_data="home")
    )
    return kb

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📥 HANDLERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@bot.message_handler(commands=['start'])
@check_user
def start(message):
    args = message.text.split()
    if len(args) > 1:
        payload = args[1]
        if payload.startswith("batch_"):
            send_batch(message.chat.id, payload.replace("batch_", ""))
        else:
            send_single_file(message.chat.id, payload)
    else:
        bot.send_message(message.chat.id, 
            f"👋 **Hi {message.from_user.first_name}!**\n\nAdvanced File Store Bot v4.0\nRunning on Render.", 
            reply_markup=main_menu(message.from_user.id))

def send_single_file(chat_id, code):
    f = db.fetchone('SELECT * FROM files WHERE file_code = ?', (code,))
    if f:
        try: bot.copy_message(chat_id, f['channel_id'], f['message_id'], caption=f"📄 `{f['file_name']}`")
        except: bot.send_message(chat_id, "⚠️ File deleted or unavailable.")
    else:
        bot.send_message(chat_id, "❌ File not found.")

def send_batch(chat_id, bid):
    b = db.get_batch(bid)
    if not b: 
        bot.send_message(chat_id, "❌ Batch not found.")
        return
    
    codes = b['file_codes'].split(",")
    bot.send_message(chat_id, f"📦 **Opening Batch: {b['batch_name']}** ({len(codes)} files)")
    
    for code in codes:
        send_single_file(chat_id, code)
        time.sleep(0.5)

@bot.message_handler(content_types=['document', 'photo', 'video', 'audio'])
@check_user
def upload(message):
    uid = message.from_user.id
    
    # Batch Mode
    if uid in user_batches:
        bot.reply_to(message, "📥 Added to batch queue...")
        process_upload(message, uid, True)
        return

    # Normal Mode
    process_upload(message, uid, False)

def process_upload(message, uid, is_batch):
    if message.document:
        name, mime = message.document.file_name, message.document.mime_type
    elif message.video:
        name, mime = "Video.mp4", "video/mp4"
    elif message.audio:
        name, mime = "Audio.mp3", "audio/mpeg"
    elif message.photo:
        name, mime = "Photo.jpg", "image/jpeg"
    else: return

    target = db.fetchone('SELECT channel_id FROM channels WHERE user_id = ?', (uid,))
    cid = target['channel_id'] if target else BIN_CHANNEL
    
    try:
        fwd = bot.forward_message(cid, message.chat.id, message.message_id)
        code = get_code()
        db.add_file(code, name, mime, fwd.message_id, cid, uid)
        
        if is_batch:
            user_batches[uid].append(code)
        else:
            link = f"https://t.me/{bot.get_me().username}?start={code}"
            bot.reply_to(message, f"✅ **Saved!**\n🔗 `{link}`")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📡 CALLBACKS & ADMIN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@bot.callback_query_handler(func=lambda c: True)
@check_user
def cb_handler(call):
    uid = call.from_user.id
    d = call.data

    if d == "home":
        bot.edit_message_text("👋 **Welcome Back**", call.message.chat.id, call.message.message_id, reply_markup=main_menu(uid))
    
    elif d == "batch_start":
        user_batches[uid] = []
        bot.send_message(call.message.chat.id, "📦 **Batch Mode**\nSend files now. Type `/savebatch <name>` when done.")
        bot.answer_callback_query(call.id)
    
    elif d == "search_mode":
        msg = bot.send_message(call.message.chat.id, "🔎 Send keyword to search:")
        bot.register_next_step_handler(msg, perform_search)

    elif d == "admin_panel" and uid in ADMIN_LIST:
        bot.edit_message_text("🛡️ **Admin Panel**", call.message.chat.id, call.message.message_id, reply_markup=admin_panel())

    elif d == "adm_stats" and uid in ADMIN_LIST:
        u = db.fetchone('SELECT COUNT(*) FROM users')[0]
        f = db.fetchone('SELECT COUNT(*) FROM files')[0]
        bot.answer_callback_query(call.id, f"Users: {u} | Files: {f}", show_alert=True)

    elif d == "adm_maint" and uid in ADMIN_LIST:
        curr = db.get_setting("maintenance")
        db.set_setting("maintenance", "1" if curr != "1" else "0")
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=admin_panel())

def perform_search(message):
    res = db.search_files(message.from_user.id, message.text)
    if not res: 
        bot.reply_to(message, "No results.")
        return
    txt = "🔎 **Results:**\n"
    for r in res: txt += f"📄 `{r['file_name']}`\n🔗 /start {r['file_code']}\n\n"
    bot.reply_to(message, txt)

@bot.message_handler(commands=['savebatch'])
def save_batch(message):
    uid = message.from_user.id
    if uid not in user_batches or not user_batches[uid]:
        bot.reply_to(message, "❌ List empty.")
        return
    
    try: name = message.text.split(maxsplit=1)[1]
    except: name = "My Batch"
    
    bid = get_code(8)
    db.create_batch(bid, name, uid, user_batches[uid])
    del user_batches[uid]
    bot.reply_to(message, f"✅ Batch Saved!\n🔗 https://t.me/{bot.get_me().username}?start=batch_{bid}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🚀 RENDER KEEPALIVE & STARTUP
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def start_server():
    """Binds to 0.0.0.0 to satisfy Render Web Service Health Check"""
    try:
        port = int(os.environ.get("PORT", 8080))
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(('0.0.0.0', port))
        sock.listen(1)
        print(f"✅ Dummy Server listening on port {port}")
    except Exception as e:
        print(f"⚠️ Server bind failed: {e}")

if __name__ == "__main__":
    if not BOT_TOKEN or not BIN_CHANNEL:
        print("❌ CONFIG MISSING")
        sys.exit(1)
    
    # 1. Start Port Listener
    threading.Thread(target=start_server, daemon=True).start()
    
    # 2. Clear previous webhooks to prevent 409 Conflict
    try:
        bot.delete_webhook(drop_pending_updates=True)
        print("✅ Webhooks cleared")
    except Exception as e:
        print(f"⚠️ Webhook clear error: {e}")

    print("🚀 Bot v4.0 Started")
    
    # 3. Infinite Loop with safe restart
    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=10, allowed_updates=['message', 'callback_query'])
        except Exception as e:
            print(f"❌ Crash: {e}")
            time.sleep(5)
