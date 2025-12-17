"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💠 TELEGRAM FILE STORE BOT v3.0 (Render Optimized)
🔥 Batch Links | Force Sub | Search | Admin Panel
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
from datetime import datetime
from functools import wraps

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
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")
logger = telebot.logger
telebot.logger.setLevel(logging.INFO)

# Global State for Batches (RAM based for current session to save DB writes)
# Structure: {user_id: [list_of_file_codes]}
user_batches = {}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🗄️ DATABASE ENGINE (SQLite)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Database:
    def __init__(self, db_file):
        self.db_file = db_file
        self.init_db()

    def get_connection(self):
        return sqlite3.connect(self.db_file, check_same_thread=False)

    def init_db(self):
        conn = self.get_connection()
        c = conn.cursor()
        
        # Users
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            role TEXT DEFAULT 'user',
            banned INTEGER DEFAULT 0,
            settings_notif INTEGER DEFAULT 1
        )''')
        
        # Files
        c.execute('''CREATE TABLE IF NOT EXISTS files (
            file_code TEXT PRIMARY KEY,
            file_name TEXT,
            mime_type TEXT,
            message_id INTEGER,
            channel_id INTEGER,
            uploader_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')

        # Batches (New Feature)
        c.execute('''CREATE TABLE IF NOT EXISTS batches (
            batch_id TEXT PRIMARY KEY,
            batch_name TEXT,
            owner_id INTEGER,
            file_codes TEXT -- Stored as comma separated string
        )''')

        # User Channels
        c.execute('''CREATE TABLE IF NOT EXISTS channels (
            user_id INTEGER PRIMARY KEY,
            channel_id INTEGER,
            channel_title TEXT
        )''')

        # System Settings
        c.execute('''CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )''')

        conn.commit()
        conn.close()

    # --- WRAPPERS ---
    def execute(self, query, params=()):
        conn = self.get_connection()
        try:
            c = conn.cursor()
            c.execute(query, params)
            conn.commit()
            return c
        finally:
            conn.close()

    def fetchone(self, query, params=()):
        c = self.execute(query, params)
        return c.fetchone()

    def fetchall(self, query, params=()):
        c = self.execute(query, params)
        return c.fetchall()

    # --- SPECIFIC METHODS ---
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
    """Checks Force Subscribe Status"""
    fsub_channel = db.get_setting("fsub_channel")
    if not fsub_channel: return True # No fsub set
    
    try:
        # Check cache or API
        chat_member = bot.get_chat_member(fsub_channel, user_id)
        if chat_member.status in ['left', 'kicked']:
            return False
        return True
    except Exception as e:
        # If bot is not admin in fsub channel or error, fail safe to True
        return True

def check_user(func):
    @wraps(func)
    def wrapper(message, *args, **kwargs):
        uid = message.from_user.id
        
        # 1. DB Entry
        db.add_user(uid)
        
        # 2. Ban Check
        user_data = db.fetchone('SELECT banned FROM users WHERE user_id = ?', (uid,))
        if user_data and user_data[0]: return
        
        # 3. Maintenance Check
        if db.get_setting("maintenance") == "1" and uid not in ADMIN_LIST:
            bot.reply_to(message, "⛔ **System Under Maintenance**\nPlease try again later.")
            return

        # 4. Force Subscribe Check (Only for start/file access)
        if message.text and message.text.startswith("/start") and not is_subscribed(uid):
            fsub_id = db.get_setting("fsub_channel")
            # Get Invite Link
            try: link = bot.create_chat_invite_link(fsub_id, member_limit=1).invite_link
            except: link = "https://t.me/" # Fallback
            
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("👉 Join Channel", url=link))
            kb.add(types.InlineKeyboardButton("🔄 Try Again", url=f"https://t.me/{bot.get_me().username}?start={message.text.split()[1] if len(message.text.split())>1 else ''}"))
            
            bot.reply_to(message, "⚠️ **Action Required**\n\nPlease join our update channel to use this bot.", reply_markup=kb)
            return

        return func(message, *args, **kwargs)
    return wrapper

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🎮 ADVANCED KEYBOARDS
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

def settings_panel(uid):
    # Fetch user specific settings if needed, for now global user settings
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔙 Back", callback_data="home"))
    # Add dummy toggles for visual effect
    kb.add(types.InlineKeyboardButton("🔔 Notifications: ON", callback_data="dummy_toggle"))
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
# 📥 START & FILE HANDLING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@bot.message_handler(commands=['start'])
@check_user
def start(message):
    args = message.text.split()
    
    if len(args) > 1:
        payload = args[1]
        
        # ➤ SINGLE FILE
        if not payload.startswith("batch_"):
            send_single_file(message.chat.id, payload)
            
        # ➤ BATCH FILES
        else:
            batch_id = payload.replace("batch_", "")
            send_batch(message.chat.id, batch_id)
    else:
        # Welcome UI
        txt = (
            f"👋 **Hi {message.from_user.first_name}!**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📥 **Store** Unlimited Files\n"
            f"📦 **Create** Batch Links\n"
            f"🔎 **Search** Your Uploads\n"
            f"⚡ **Fast & Secure**\n"
        )
        bot.send_message(message.chat.id, txt, reply_markup=main_menu(message.from_user.id))

def send_single_file(chat_id, code):
    file_row = db.fetchone('SELECT * FROM files WHERE file_code = ?', (code,))
    if not file_row:
        bot.send_message(chat_id, "❌ File not found.")
        return
    
    # file_row: code, name, mime, mid, cid, uid
    try:
        bot.copy_message(chat_id, file_row[4], file_row[3], caption=f"📄 `{file_row[1]}`")
    except Exception as e:
        bot.send_message(chat_id, "⚠️ File unavailable (Deleted from channel).")

def send_batch(chat_id, batch_id):
    batch = db.get_batch(batch_id)
    if not batch:
        bot.send_message(chat_id, "❌ Batch not found.")
        return
        
    codes = batch[3].split(",")
    bot.send_message(chat_id, f"📦 **Opening Batch:** {batch[1]}\n__Processing {len(codes)} files...__")
    
    for code in codes:
        send_single_file(chat_id, code)
        time.sleep(0.5) # Flood protection
        
    bot.send_message(chat_id, "✅ **Batch Complete!**")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📤 UPLOAD LOGIC
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@bot.message_handler(content_types=['document', 'photo', 'video', 'audio'])
@check_user
def handle_upload(message):
    # Check if user is in "Batch Mode"
    uid = message.from_user.id
    if uid in user_batches:
        # Add to temporary batch list
        status = bot.reply_to(message, "📥 Added to Batch queue...")
        process_upload(message, uid, is_batch=True)
        bot.delete_message(message.chat.id, status.message_id)
        return

    # Normal Upload
    process_upload(message, uid)

def process_upload(message, uid, is_batch=False):
    # 1. Extract File Data
    if message.document:
        name = message.document.file_name
        mime = message.document.mime_type
    elif message.video:
        name = "Video.mp4"
        mime = "video/mp4"
    elif message.audio:
        name = "Audio.mp3"
        mime = "audio/mpeg"
    elif message.photo:
        name = "Photo.jpg"
        mime = "image/jpeg"
    else: return

    # 2. Forward to Storage
    storage_id = db.fetchone('SELECT channel_id FROM channels WHERE user_id = ?', (uid,))
    target = storage_id[0] if storage_id else BIN_CHANNEL
    
    try:
        fwd = bot.forward_message(target, message.chat.id, message.message_id)
        code = get_code()
        
        # 3. Save DB
        db.add_file(code, name, mime, fwd.message_id, target, uid)
        
        # 4. Response
        if is_batch:
            user_batches[uid].append(code)
        else:
            link = f"https://t.me/{bot.get_me().username}?start={code}"
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("↗️ Share", url=f"https://t.me/share/url?url={link}"))
            bot.reply_to(message, f"✅ **Saved!**\n🔗 `{link}`", reply_markup=kb)
            
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📦 BATCH MODE HANDLERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@bot.callback_query_handler(func=lambda c: c.data == "batch_start")
def batch_start_btn(call):
    uid = call.from_user.id
    user_batches[uid] = [] # Init list
    msg = bot.send_message(call.message.chat.id, 
        "📦 **Batch Mode Active**\n\n"
        "1. Send/Forward files here.\n"
        "2. When done, type `/savebatch <name>`\n"
        "3. Type `/cancel` to stop."
    )

@bot.message_handler(commands=['savebatch'])
def save_batch_cmd(message):
    uid = message.from_user.id
    if uid not in user_batches or not user_batches[uid]:
        bot.reply_to(message, "❌ You haven't uploaded any files for the batch!")
        return
        
    try:
        name = message.text.split(maxsplit=1)[1]
    except:
        bot.reply_to(message, "⚠️ Usage: `/savebatch My Collection Name`")
        return

    bid = get_code(8)
    codes = user_batches[uid]
    
    db.create_batch(bid, name, uid, codes)
    del user_batches[uid] # Clear RAM
    
    link = f"https://t.me/{bot.get_me().username}?start=batch_{bid}"
    
    txt = (
        f"✅ **Batch Created Successfully!**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📂 **Name:** {name}\n"
        f"🔢 **Files:** {len(codes)}\n"
        f"🔗 **Link:** `{link}`"
    )
    bot.reply_to(message, txt)

@bot.message_handler(commands=['cancel'])
def cancel_cmd(message):
    uid = message.from_user.id
    if uid in user_batches:
        del user_batches[uid]
        bot.reply_to(message, "❌ Batch mode cancelled.")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔎 SEARCH FEATURE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@bot.callback_query_handler(func=lambda c: c.data == "search_mode")
def search_btn(call):
    msg = bot.send_message(call.message.chat.id, "🔎 **Send me a keyword to search your files:**")
    bot.register_next_step_handler(msg, process_search)

def process_search(message):
    results = db.search_files(message.from_user.id, message.text)
    if not results:
        bot.reply_to(message, "❌ No files found.")
        return
    
    txt = "🔎 **Search Results:**\n\n"
    for row in results:
        # row: code, name
        txt += f"📄 `{row[1]}`\n🔗 /start {row[0]}\n\n"
    
    bot.reply_to(message, txt)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🛠️ SETTINGS & ADMIN PANEL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@bot.callback_query_handler(func=lambda c: True)
def master_callback(call):
    uid = call.from_user.id
    data = call.data
    
    # ➤ Navigation
    if data == "home":
        bot.edit_message_text("👋 **Welcome Back**", call.message.chat.id, call.message.message_id, reply_markup=main_menu(uid))
        return
    
    if data == "user_settings":
        bot.edit_message_text("⚙️ **User Settings**", call.message.chat.id, call.message.message_id, reply_markup=settings_panel(uid))
        return

    # ➤ Admin Controls
    if uid in ADMIN_LIST:
        if data == "admin_panel":
            bot.edit_message_text("🛡️ **Admin Dashboard**", call.message.chat.id, call.message.message_id, reply_markup=admin_panel())
        
        elif data == "adm_stats":
            u = db.fetchone('SELECT COUNT(*) FROM users')[0]
            f = db.fetchone('SELECT COUNT(*) FROM files')[0]
            bot.answer_callback_query(call.id, f"Users: {u} | Files: {f}", show_alert=True)
            
        elif data == "adm_maint":
            curr = db.get_setting("maintenance")
            new = "1" if curr != "1" else "0"
            db.set_setting("maintenance", new)
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=admin_panel())

        elif data == "adm_fsub":
            bot.send_message(call.message.chat.id, "📢 **Send the Channel ID (e.g. -100xxxx) for Force Sub:**\nSend `0` to disable.")
            bot.register_next_step_handler(call.message, lambda m: set_fsub(m))
            
        elif data == "adm_broadcast":
            bot.send_message(call.message.chat.id, "📢 **Send Message to Broadcast:**")
            bot.register_next_step_handler(call.message, start_broadcast)

def set_fsub(message):
    val = message.text.strip()
    if val == "0": 
        db.set_setting("fsub_channel", "")
        bot.reply_to(message, "❌ Force Sub Disabled.")
    else:
        db.set_setting("fsub_channel", val)
        bot.reply_to(message, f"✅ Force Sub set to `{val}`. Make sure I am Admin there!")

def start_broadcast(message):
    users = db.fetchall('SELECT user_id FROM users')
    bot.reply_to(message, f"🚀 Sending to {len(users)} users...")
    
    for row in users:
        try:
            bot.copy_message(row[0], message.chat.id, message.message_id)
            time.sleep(0.05)
        except: pass
    bot.reply_to(message, "✅ Done.")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🚀 SERVER KEEPALIVE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def keep_alive():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(('', int(os.environ.get("PORT", 8080))))
        s.listen(1)
    except: pass

if __name__ == "__main__":
    threading.Thread(target=keep_alive).start()
    print("💎 BOT v3.0 ONLINE")
    while True:
        try: bot.infinity_polling(skip_pending=True)
        except: time.sleep(5)
