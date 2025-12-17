"""
TELEGRAM FILE STORE BOT - Single File Production Ready
Optimized for Render Free Plan (Low RAM, Long Polling)
"""

import os
import sys
import time
import logging
import sqlite3
import threading
import secrets
import json
from datetime import datetime
from functools import wraps
from typing import Optional, List, Union

import telebot
from telebot import types, apihelper

# ---------------------------------------------------------
# CONFIGURATION & ENV VARS
# ---------------------------------------------------------
# Required Environment Variables:
# BOT_TOKEN: Your Telegram Bot Token
# OWNER_ID: Your Telegram User ID
# BIN_CHANNEL: ID of the private channel for main storage (e.g. -100xxxx)
# LOG_CHANNEL: ID of the private channel for logs (e.g. -100xxxx)

BOT_TOKEN = os.environ.get('BOT_TOKEN')
OWNER_ID = int(os.environ.get('OWNER_ID', 0))
BIN_CHANNEL = int(os.environ.get('BIN_CHANNEL', 0))
LOG_CHANNEL = int(os.environ.get('LOG_CHANNEL', 0))

# Optional Config
DB_NAME = "bot_data.db"
ADMIN_LIST = [OWNER_ID]  # Add other admin IDs here if hardcoded
BROADCAST_CHUNK_SIZE = 50  # Process 50 users at a time to save RAM

# Initialize Bot
bot = telebot.TeleBot(BOT_TOKEN, threaded=True, num_threads=3) # Low threads for low RAM
logger = telebot.logger
telebot.logger.setLevel(logging.INFO)

# ---------------------------------------------------------
# DATABASE MANAGER (SQLite)
# ---------------------------------------------------------
class Database:
    def __init__(self, db_file):
        self.db_file = db_file
        self.init_db()

    def get_connection(self):
        return sqlite3.connect(self.db_file, check_same_thread=False)

    def init_db(self):
        conn = self.get_connection()
        c = conn.cursor()
        
        # Users Table
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            role TEXT DEFAULT 'user',
            banned INTEGER DEFAULT 0,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        
        # Files Table
        c.execute('''CREATE TABLE IF NOT EXISTS files (
            file_code TEXT PRIMARY KEY,
            file_name TEXT,
            mime_type TEXT,
            file_id TEXT,
            file_unique_id TEXT,
            message_id INTEGER,
            channel_id INTEGER,
            uploader_id INTEGER,
            downloads INTEGER DEFAULT 0,
            visibility TEXT DEFAULT 'public',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')

        # User Channels Table
        c.execute('''CREATE TABLE IF NOT EXISTS channels (
            user_id INTEGER PRIMARY KEY,
            channel_id INTEGER,
            channel_title TEXT
        )''')

        # Indexes for speed
        c.execute('CREATE INDEX IF NOT EXISTS idx_files_uploader ON files(uploader_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_files_unique ON files(file_unique_id)')
        
        conn.commit()
        conn.close()

    # --- User Methods ---
    def add_user(self, user_id):
        conn = self.get_connection()
        c = conn.cursor()
        try:
            c.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (user_id,))
            conn.commit()
        finally:
            conn.close()

    def get_user_role(self, user_id):
        conn = self.get_connection()
        c = conn.cursor()
        res = c.execute('SELECT role, banned FROM users WHERE user_id = ?', (user_id,)).fetchone()
        conn.close()
        return res if res else ('user', 0)

    def set_ban_status(self, user_id, status: int):
        conn = self.get_connection()
        conn.execute('UPDATE users SET banned = ? WHERE user_id = ?', (status, user_id))
        conn.commit()
        conn.close()

    # --- File Methods ---
    def add_file(self, file_code, file_name, mime_type, file_id, unique_id, msg_id, chan_id, uploader_id):
        conn = self.get_connection()
        try:
            conn.execute('''INSERT INTO files 
                (file_code, file_name, mime_type, file_id, file_unique_id, message_id, channel_id, uploader_id) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (file_code, file_name, mime_type, file_id, unique_id, msg_id, chan_id, uploader_id))
            conn.commit()
        finally:
            conn.close()

    def get_file(self, file_code):
        conn = self.get_connection()
        c = conn.cursor()
        res = c.execute('SELECT * FROM files WHERE file_code = ?', (file_code,)).fetchone()
        conn.close()
        return res

    def delete_file(self, file_code):
        conn = self.get_connection()
        conn.execute('DELETE FROM files WHERE file_code = ?', (file_code,))
        conn.commit()
        conn.close()

    def increment_download(self, file_code):
        conn = self.get_connection()
        conn.execute('UPDATE files SET downloads = downloads + 1 WHERE file_code = ?', (file_code,))
        conn.commit()
        conn.close()

    def get_user_files(self, user_id, limit=10, offset=0):
        conn = self.get_connection()
        c = conn.cursor()
        res = c.execute('SELECT file_code, file_name, downloads FROM files WHERE uploader_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?', 
                        (user_id, limit, offset)).fetchall()
        conn.close()
        return res

    # --- Stats & System ---
    def get_stats(self):
        conn = self.get_connection()
        c = conn.cursor()
        total_users = c.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        total_files = c.execute('SELECT COUNT(*) FROM files').fetchone()[0]
        total_banned = c.execute('SELECT COUNT(*) FROM users WHERE banned = 1').fetchone()[0]
        conn.close()
        return total_users, total_files, total_banned

    def get_all_users_generator(self):
        """Generator to fetch users in chunks for memory efficiency"""
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('SELECT user_id FROM users')
        while True:
            batch = c.fetchmany(BROADCAST_CHUNK_SIZE)
            if not batch:
                break
            for row in batch:
                yield row[0]
        conn.close()
    
    # --- Channel Mapping ---
    def set_user_channel(self, user_id, channel_id, title):
        conn = self.get_connection()
        conn.execute('INSERT OR REPLACE INTO channels (user_id, channel_id, channel_title) VALUES (?, ?, ?)',
                     (user_id, channel_id, title))
        conn.commit()
        conn.close()

    def get_user_channel(self, user_id):
        conn = self.get_connection()
        res = conn.execute('SELECT channel_id FROM channels WHERE user_id = ?', (user_id,)).fetchone()
        conn.close()
        return res[0] if res else None

# Initialize DB
db = Database(DB_NAME)

# ---------------------------------------------------------
# HELPERS & DECORATORS
# ---------------------------------------------------------

def get_file_type_and_id(message):
    """Extract file info from any message type."""
    if message.document:
        return 'doc', message.document.file_id, message.document.file_unique_id, message.document.file_name, message.document.mime_type
    elif message.video:
        return 'video', message.video.file_id, message.video.file_unique_id, message.video.file_name or "video.mp4", message.video.mime_type
    elif message.audio:
        return 'audio', message.audio.file_id, message.audio.file_unique_id, message.audio.file_name or "audio.mp3", message.audio.mime_type
    elif message.photo:
        # Photos are lists, get the largest
        p = message.photo[-1]
        return 'photo', p.file_id, p.file_unique_id, "photo.jpg", "image/jpeg"
    return None, None, None, None, None

def generate_safe_code():
    return secrets.token_urlsafe(8)

def log_event(text):
    """Logs to the private log channel."""
    if LOG_CHANNEL:
        try:
            bot.send_message(LOG_CHANNEL, f"📝 **LOG**: {text}", parse_mode="Markdown")
        except Exception as e:
            print(f"Logging Error: {e}")

# Decorators
def is_banned(func):
    @wraps(func)
    def wrapper(message, *args, **kwargs):
        user_id = message.from_user.id
        role, banned = db.get_user_role(user_id)
        if banned:
            return
        return func(message, *args, **kwargs)
    return wrapper

def is_admin(func):
    @wraps(func)
    def wrapper(message, *args, **kwargs):
        user_id = message.from_user.id
        if user_id not in ADMIN_LIST and user_id != OWNER_ID:
            return
        return func(message, *args, **kwargs)
    return wrapper

# ---------------------------------------------------------
# BOT HANDLERS
# ---------------------------------------------------------

@bot.message_handler(commands=['start'])
@is_banned
def start_handler(message):
    db.add_user(message.from_user.id)
    args = message.text.split()
    
    # Deep Linking Logic
    if len(args) > 1:
        file_code = args[1]
        file_data = db.get_file(file_code)
        
        if not file_data:
            bot.reply_to(message, "⚠️ File not found or deleted.")
            return

        # file_data: (0:code, 1:name, 2:mime, 3:fid, 4:uid, 5:mid, 6:cid, 7:uid, 8:dl, 9:vis, 10:time)
        channel_id = file_data[6]
        message_id = file_data[5]
        file_name = file_data[1]

        try:
            # Use copy_message to avoid forwarding tag and protect original sender
            bot.copy_message(
                chat_id=message.chat.id,
                from_chat_id=channel_id,
                message_id=message_id,
                caption=f"📄 **{file_name}**\n\n🤖 via @{bot.get_me().username}",
                parse_mode="Markdown"
            )
            db.increment_download(file_code)
            log_event(f"User {message.from_user.id} accessed file {file_code}")
        except Exception as e:
            bot.reply_to(message, "⚠️ Error retrieving file. It might have been deleted from storage.")
            log_event(f"Error fetching file {file_code}: {e}")
    else:
        # Normal Welcome
        bot.reply_to(message, 
                     f"👋 Hi {message.from_user.first_name}!\n\n"
                     "I am your personal File Store Bot.\n"
                     "📤 **Send me any file** to store it.\n"
                     "🔗 I will give you a shareable link.\n\n"
                     "Use /myfiles to see your uploads.")

@bot.message_handler(content_types=['document', 'photo', 'video', 'audio'])
@is_banned
def file_handler(message):
    """Handles user uploads"""
    user_id = message.from_user.id
    db.add_user(user_id)
    
    file_type, file_id, unique_id, file_name, mime_type = get_file_type_and_id(message)
    if not file_id:
        bot.reply_to(message, "❌ File type not supported.")
        return

    status_msg = bot.reply_to(message, "🔄 Processing...")

    # Determine Storage Channel (Personal or Main)
    storage_target = db.get_user_channel(user_id) or BIN_CHANNEL
    
    try:
        # Forward to storage channel
        forwarded = bot.forward_message(storage_target, message.chat.id, message.message_id)
        
        # Save Metadata
        code = generate_safe_code()
        db.add_file(code, file_name, mime_type, file_id, unique_id, forwarded.message_id, storage_target, user_id)
        
        link = f"https://t.me/{bot.get_me().username}?start={code}"
        
        bot.edit_message_text(
            f"✅ **File Saved!**\n\n"
            f"📂 Name: `{file_name}`\n"
            f"🔗 Link: {link}",
            chat_id=message.chat.id,
            message_id=status_msg.message_id,
            parse_mode="Markdown"
        )
        log_event(f"User {user_id} uploaded {file_name} ({code})")
        
    except Exception as e:
        bot.edit_message_text(f"❌ Error saving file: {e}", message.chat.id, status_msg.message_id)
        logger.error(f"Upload Error: {e}")

@bot.message_handler(commands=['myfiles'])
@is_banned
def myfiles_handler(message):
    """Simple pagination for user files"""
    files = db.get_user_files(message.from_user.id, limit=20)
    if not files:
        bot.reply_to(message, "You haven't uploaded any files yet.")
        return
    
    text = "📂 **Your Recent Files:**\n\n"
    for f in files:
        # f: code, name, downloads
        text += f"🔹 `{f[1]}`\n   └ 🔗 `/start {f[0]}` | 📥 {f[2]}\n\n"
    
    bot.reply_to(message, text, parse_mode="Markdown")

@bot.message_handler(commands=['connect_channel'])
@is_banned
def connect_channel(message):
    bot.reply_to(message, "Forward a message from your channel to connect it. I must be an Admin there!")
    bot.register_next_step_handler(message, process_channel_connect)

def process_channel_connect(message):
    if not message.forward_from_chat:
        bot.reply_to(message, "❌ That is not a forwarded channel message.")
        return
    
    channel_id = message.forward_from_chat.id
    title = message.forward_from_chat.title
    user_id = message.from_user.id
    
    try:
        # Verify Admin Rights
        chat_member = bot.get_chat_member(channel_id, bot.get_me().id)
        if chat_member.status not in ['administrator', 'creator']:
            bot.reply_to(message, "❌ I am not an admin in that channel.")
            return
            
        db.set_user_channel(user_id, channel_id, title)
        bot.reply_to(message, f"✅ Successfully connected to **{title}**.\nFuture uploads will go there.")
        log_event(f"User {user_id} connected channel {channel_id}")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")

# ---------------------------------------------------------
# ADMIN & OWNER PANEL
# ---------------------------------------------------------

@bot.message_handler(commands=['admin', 'stats'])
@is_admin
def admin_panel(message):
    u, f, b = db.get_stats()
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
        types.InlineKeyboardButton("🚫 Ban User", callback_data="admin_ban"),
        types.InlineKeyboardButton("🔓 Unban User", callback_data="admin_unban"),
        types.InlineKeyboardButton("🗑 Delete File", callback_data="admin_del")
    )
    
    bot.reply_to(message, 
                 f"🛡 **Admin Panel**\n\n"
                 f"👤 Users: `{u}`\n"
                 f"📂 Files: `{f}`\n"
                 f"🚫 Banned: `{b}`",
                 reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_'))
def admin_callback(call):
    if call.from_user.id not in ADMIN_LIST and call.from_user.id != OWNER_ID:
        return

    if call.data == "admin_broadcast":
        msg = bot.send_message(call.message.chat.id, "Send the message you want to broadcast (Text, Photo, etc).")
        bot.register_next_step_handler(msg, start_broadcast)
    
    elif call.data == "admin_ban":
        msg = bot.send_message(call.message.chat.id, "Send User ID to Ban:")
        bot.register_next_step_handler(msg, lambda m: toggle_ban(m, 1))
        
    elif call.data == "admin_unban":
        msg = bot.send_message(call.message.chat.id, "Send User ID to Unban:")
        bot.register_next_step_handler(msg, lambda m: toggle_ban(m, 0))
        
    elif call.data == "admin_del":
        msg = bot.send_message(call.message.chat.id, "Send File Code to Delete:")
        bot.register_next_step_handler(msg, delete_file_admin)

def toggle_ban(message, status):
    try:
        uid = int(message.text)
        db.set_ban_status(uid, status)
        bot.reply_to(message, f"User {uid} {'Banned' if status else 'Unbanned'}.")
        log_event(f"Admin {message.from_user.id} set ban={status} for {uid}")
    except:
        bot.reply_to(message, "Invalid ID.")

def delete_file_admin(message):
    code = message.text.strip()
    db.delete_file(code)
    bot.reply_to(message, f"File {code} removed from DB (Message in channel remains).")
    log_event(f"Admin {message.from_user.id} deleted file {code}")

# ---------------------------------------------------------
# BROADCAST SYSTEM (Memory Efficient)
# ---------------------------------------------------------

def start_broadcast(message):
    # Run in a separate thread to not block the bot
    threading.Thread(target=run_broadcast, args=(message,)).start()

def run_broadcast(message_to_copy):
    """
    Sequentially sends messages to avoid hitting RAM limits or Telegram limits.
    """
    admin_id = message_to_copy.from_user.id
    bot.send_message(admin_id, "🚀 Broadcast started...")
    
    users = db.get_all_users_generator() # Generator
    count = 0
    errors = 0
    
    start_time = time.time()
    
    # Save broadcast to Log Channel for archive
    try:
        bot.copy_message(LOG_CHANNEL, message_to_copy.chat.id, message_to_copy.message_id)
    except: pass

    for user_id in users:
        try:
            bot.copy_message(user_id, message_to_copy.chat.id, message_to_copy.message_id)
            count += 1
        except Exception as e:
            errors += 1
            # If blocked, maybe mark as inactive in future versions
            pass
        
        # Rate Limiting: 20 messages per second is global limit, let's be safe
        if count % 20 == 0:
            time.sleep(1) 
            
    elapsed = time.time() - start_time
    bot.send_message(admin_id, 
                     f"✅ **Broadcast Complete**\n\n"
                     f"👥 Sent: {count}\n"
                     f"❌ Failed: {errors}\n"
                     f"⏱ Time: {elapsed:.2f}s", parse_mode="Markdown")

# ---------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------

def main():
    if not BOT_TOKEN or not BIN_CHANNEL:
        print("❌ Error: BOT_TOKEN and BIN_CHANNEL env vars are required.")
        sys.exit(1)
        
    print("🚀 Bot Started on Render...")
    log_event("🤖 Bot System Restarted / Online")
    
    # Infinite polling with auto-restart on network error
    while True:
        try:
            bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except Exception as e:
            print(f"⚠️ Bot crashed: {e}")
            time.sleep(5) # Wait before retry

if __name__ == '__main__':
    main()
