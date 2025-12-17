"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💎 ULTIMATE TELEGRAM FILE STORE BOT (Render Optimized)
🔥 Single File | SQLite | Private Storage | Long Polling
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
from typing import Optional

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
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        
        # Files
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

        # Channels mapping
        c.execute('''CREATE TABLE IF NOT EXISTS channels (
            user_id INTEGER PRIMARY KEY,
            channel_id INTEGER,
            channel_title TEXT
        )''')

        # Settings (Key-Value)
        c.execute('''CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )''')

        conn.commit()
        conn.close()

    # --- SETTINGS ---
    def get_setting(self, key, default="0"):
        conn = self.get_connection()
        res = conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
        conn.close()
        return res[0] if res else default

    def set_setting(self, key, value):
        conn = self.get_connection()
        conn.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, str(value)))
        conn.commit()
        conn.close()

    # --- USERS ---
    def add_user(self, user_id):
        conn = self.get_connection()
        conn.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (user_id,))
        conn.commit()
        conn.close()

    def get_user_status(self, user_id):
        conn = self.get_connection()
        res = conn.execute('SELECT role, banned FROM users WHERE user_id = ?', (user_id,)).fetchone()
        conn.close()
        return res if res else ('user', 0)

    def set_ban(self, user_id, is_banned):
        conn = self.get_connection()
        conn.execute('UPDATE users SET banned = ? WHERE user_id = ?', (1 if is_banned else 0, user_id))
        conn.commit()
        conn.close()

    def get_all_users(self):
        """Yields users for broadcast to save RAM"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM users')
        while True:
            rows = cursor.fetchmany(100)
            if not rows: break
            for row in rows: yield row[0]
        conn.close()

    # --- FILES ---
    def add_file(self, code, name, mime, fid, uid, mid, cid, uploader):
        conn = self.get_connection()
        conn.execute('''INSERT INTO files (file_code, file_name, mime_type, file_id, file_unique_id, 
                        message_id, channel_id, uploader_id) VALUES (?,?,?,?,?,?,?,?)''',
                        (code, name, mime, fid, uid, mid, cid, uploader))
        conn.commit()
        conn.close()

    def get_file(self, code):
        conn = self.get_connection()
        res = conn.execute('SELECT * FROM files WHERE file_code = ?', (code,)).fetchone()
        conn.close()
        return res

    def delete_file(self, code):
        conn = self.get_connection()
        conn.execute('DELETE FROM files WHERE file_code = ?', (code,))
        conn.commit()
        conn.close()

    def add_download(self, code):
        conn = self.get_connection()
        conn.execute('UPDATE files SET downloads = downloads + 1 WHERE file_code = ?', (code,))
        conn.commit()
        conn.close()

    def get_user_files_stats(self, user_id):
        conn = self.get_connection()
        count = conn.execute('SELECT COUNT(*) FROM files WHERE uploader_id = ?', (user_id,)).fetchone()[0]
        conn.close()
        return count

    # --- STATS ---
    def get_system_stats(self):
        conn = self.get_connection()
        users = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        files = conn.execute('SELECT COUNT(*) FROM files').fetchone()[0]
        banned = conn.execute('SELECT COUNT(*) FROM users WHERE banned = 1').fetchone()[0]
        conn.close()
        return users, files, banned
    
    # --- CHANNELS ---
    def set_channel(self, uid, cid, title):
        conn = self.get_connection()
        conn.execute('INSERT OR REPLACE INTO channels VALUES (?,?,?)', (uid, cid, title))
        conn.commit()
        conn.close()
    
    def get_channel(self, uid):
        conn = self.get_connection()
        res = conn.execute('SELECT channel_id FROM channels WHERE user_id = ?', (uid,)).fetchone()
        conn.close()
        return res[0] if res else None

db = Database(DB_NAME)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🛠️ UTILS & DECORATORS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def generate_code(): return secrets.token_urlsafe(6)

def is_admin(func):
    @wraps(func)
    def wrapper(message, *args, **kwargs):
        if message.from_user.id not in ADMIN_LIST and message.from_user.id != OWNER_ID:
            return
        return func(message, *args, **kwargs)
    return wrapper

def check_user(func):
    @wraps(func)
    def wrapper(message, *args, **kwargs):
        uid = message.from_user.id
        role, banned = db.get_user_status(uid)
        if banned: return
        
        # Check Maintenance Mode (Admins bypass)
        m_mode = db.get_setting("maintenance_mode") == "1"
        if m_mode and uid not in ADMIN_LIST and uid != OWNER_ID:
            bot.reply_to(message, "⚠️ **System is under maintenance.**\nPlease try again later.")
            return
            
        return func(message, *args, **kwargs)
    return wrapper

def log(text):
    if LOG_CHANNEL:
        try: bot.send_message(LOG_CHANNEL, f"📝 `{text}`")
        except: pass

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🎮 MENUS & UI (INLINE KEYBOARDS)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main_menu_keyboard(user_id):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📂 My Files", callback_data="my_files"),
        types.InlineKeyboardButton("⚙️ Settings", callback_data="user_settings"),
        types.InlineKeyboardButton("🆘 Help & Commands", callback_data="help_menu")
    )
    if user_id in ADMIN_LIST or user_id == OWNER_ID:
        kb.add(types.InlineKeyboardButton("🛡️ Admin Panel", callback_data="admin_panel"))
    return kb

def admin_keyboard():
    m_mode = db.get_setting("maintenance_mode") == "1"
    status_icon = "🔴" if m_mode else "🟢"
    
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📊 Statistics", callback_data="adm_stats"),
        types.InlineKeyboardButton("📢 Broadcast", callback_data="adm_broadcast"),
        types.InlineKeyboardButton("🚫 Ban User", callback_data="adm_ban"),
        types.InlineKeyboardButton("🔓 Unban User", callback_data="adm_unban"),
        types.InlineKeyboardButton("🗑 Delete File", callback_data="adm_del"),
        types.InlineKeyboardButton(f"Maintenance: {status_icon}", callback_data="adm_maint_toggle")
    )
    kb.add(types.InlineKeyboardButton("🔙 Back to Home", callback_data="home"))
    return kb

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📥 COMMAND HANDLERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@bot.message_handler(commands=['start'])
@check_user
def start_command(message):
    db.add_user(message.from_user.id)
    args = message.text.split()
    
    # ➤ DEEP LINK HANDLING
    if len(args) > 1:
        code = args[1]
        file_data = db.get_file(code)
        
        if not file_data:
            bot.reply_to(message, "❌ **File Not Found**\nIt may have been deleted.")
            return

        # (code, name, mime, fid, uid, msg_id, chan_id, uploader, dl, vis, time)
        channel_id = file_data[6]
        msg_id = file_data[5]
        name = file_data[1]
        
        try:
            bot.copy_message(message.chat.id, channel_id, msg_id, caption=f"📄 `{name}`\n🤖 via @{bot.get_me().username}")
            db.add_download(code)
        except Exception:
            bot.reply_to(message, "⚠️ **Error:** content unavailable.")
    
    # ➤ NORMAL START
    else:
        txt = (
            f"👋 **Hello, {message.from_user.first_name}!**\n"
            f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
            f"☁️ **Advanced File Store Bot**\n\n"
            f"🔹 Send me **any file** to store it.\n"
            f"🔹 I will provide a **permanent link**.\n"
            f"🔹 Create **Personal Channels** for storage.\n\n"
            f"🚀 _Powered by Render & SQLite_"
        )
        bot.send_message(message.chat.id, txt, reply_markup=main_menu_keyboard(message.from_user.id))

@bot.message_handler(commands=['help'])
@check_user
def help_command(message):
    user_id = message.from_user.id
    is_adm = user_id in ADMIN_LIST or user_id == OWNER_ID
    
    txt = "📚 **COMMAND LIST**\n▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n\n"
    txt += "👤 **User Commands:**\n"
    txt += "• `/start` - Main Menu\n"
    txt += "• `/myfiles` - View your files\n"
    txt += "• `/connect_channel` - Link custom storage\n"
    txt += "• `/disconnect` - Unlink channel\n\n"
    
    if is_adm:
        txt += "🛡️ **Admin Commands:**\n"
        txt += "• `/admin` - Open Admin Dashboard\n"
        txt += "• `/stats` - Quick server stats\n"
        txt += "• `/ban <id>` - Ban a user\n"
    
    bot.reply_to(message, txt)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📤 FILE UPLOAD HANDLER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@bot.message_handler(content_types=['document', 'photo', 'video', 'audio'])
@check_user
def handle_file(message):
    # 1. Get File Info
    if message.document:
        fid, unique, name, mime = message.document.file_id, message.document.file_unique_id, message.document.file_name, message.document.mime_type
    elif message.video:
        fid, unique, name, mime = message.video.file_id, message.video.file_unique_id, "video.mp4", "video/mp4"
    elif message.audio:
        fid, unique, name, mime = message.audio.file_id, message.audio.file_unique_id, "audio.mp3", "audio/mpeg"
    elif message.photo:
        fid, unique, name, mime = message.photo[-1].file_id, message.photo[-1].file_unique_id, "photo.jpg", "image/jpeg"
    else: return

    user_id = message.from_user.id
    db.add_user(user_id)
    
    # 2. UI Feedback
    status = bot.reply_to(message, "⚡ **Processing...**")
    
    # 3. Determine Storage Channel
    storage_channel = db.get_channel(user_id) or BIN_CHANNEL
    
    try:
        # 4. Forward to Storage
        fwd = bot.forward_message(storage_channel, message.chat.id, message.message_id)
        
        # 5. Generate Data
        code = generate_code()
        link = f"https://t.me/{bot.get_me().username}?start={code}"
        
        # 6. Save DB
        db.add_file(code, name, mime, fid, unique, fwd.message_id, storage_channel, user_id)
        
        # 7. Success Response
        res_text = (
            f"✅ **File Saved Successfully!**\n"
            f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
            f"📂 **Name:** `{name}`\n"
            f"🔐 **Code:** `{code}`\n\n"
            f"🔗 **Share Link:**\n`{link}`"
        )
        
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔁 Share Link", url=f"https://t.me/share/url?url={link}"))
        
        bot.edit_message_text(res_text, message.chat.id, status.message_id, reply_markup=kb)
        log(f"User {user_id} uploaded {code}")
        
    except Exception as e:
        bot.edit_message_text(f"❌ **Error:** {e}", message.chat.id, status.message_id)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 👥 USER CHANNEL MANAGEMENT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@bot.message_handler(commands=['connect_channel'])
@check_user
def connect_req(message):
    msg = bot.reply_to(message, "👉 **Forward a message** from your channel here.\n\n⚠️ I must be an **Admin** in that channel!")
    bot.register_next_step_handler(msg, process_channel_link)

def process_channel_link(message):
    try:
        if not message.forward_from_chat:
            bot.reply_to(message, "❌ Not a forwarded channel message.")
            return
        
        cid = message.forward_from_chat.id
        title = message.forward_from_chat.title
        
        # Check permissions
        mem = bot.get_chat_member(cid, bot.get_me().id)
        if mem.status not in ['administrator', 'creator']:
            bot.reply_to(message, "❌ **I am not an admin there.** Promte me first!")
            return
            
        db.set_channel(message.from_user.id, cid, title)
        bot.reply_to(message, f"✅ **Connected!**\nChannel: `{title}`\nID: `{cid}`\n\nFuture files will be stored here.")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🛡️ ADMIN PANEL LOGIC
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@bot.message_handler(commands=['admin'])
@is_admin
def admin_dash(message):
    bot.reply_to(message, "🛡️ **Control Panel**", reply_markup=admin_keyboard())

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    uid = call.from_user.id
    
    # ➤ USER CALLBACKS
    if call.data == "help_menu":
        bot.answer_callback_query(call.id)
        help_command(call.message)
        return
        
    if call.data == "my_files":
        cnt = db.get_user_files_stats(uid)
        bot.answer_callback_query(call.id, f"You have {cnt} files stored.")
        return
        
    if call.data == "home":
        bot.edit_message_text(f"☁️ **Advanced File Store**\nSelect an option:", call.message.chat.id, call.message.message_id, reply_markup=main_menu_keyboard(uid))
        return

    # ➤ ADMIN CALLBACKS (Security Check)
    if uid not in ADMIN_LIST and uid != OWNER_ID:
        bot.answer_callback_query(call.id, "🚫 Admin only!", show_alert=True)
        return

    if call.data == "admin_panel":
        bot.edit_message_text("🛡️ **Admin Dashboard**", call.message.chat.id, call.message.message_id, reply_markup=admin_keyboard())

    elif call.data == "adm_stats":
        u, f, b = db.get_system_stats()
        txt = (
            f"📊 **Live Statistics**\n"
            f"▬▬▬▬▬▬▬▬▬▬▬▬\n"
            f"👥 **Users:** `{u}`\n"
            f"📂 **Files:** `{f}`\n"
            f"🚫 **Banned:** `{b}`\n"
            f"💾 **DB Size:** Optimized"
        )
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=admin_keyboard())

    elif call.data == "adm_maint_toggle":
        curr = db.get_setting("maintenance_mode")
        new_val = "0" if curr == "1" else "1"
        db.set_setting("maintenance_mode", new_val)
        status = "🔴 ON" if new_val == "1" else "🟢 OFF"
        bot.answer_callback_query(call.id, f"Maintenance Mode: {status}")
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=admin_keyboard())

    elif call.data == "adm_broadcast":
        msg = bot.send_message(call.message.chat.id, "📢 **Send the message to broadcast:**\n(Text, Photo, Video supported)")
        bot.register_next_step_handler(msg, start_broadcast)

    elif call.data == "adm_del":
        msg = bot.send_message(call.message.chat.id, "🗑 **Send File Code to delete:**")
        bot.register_next_step_handler(msg, lambda m: admin_delete_logic(m))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ⚙️ BACKGROUND TASKS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def start_broadcast(message):
    threading.Thread(target=run_broadcast_process, args=(message,)).start()

def run_broadcast_process(msg):
    admin_id = msg.from_user.id
    bot.send_message(admin_id, "🚀 **Broadcast Started...**")
    
    users = db.get_all_users()
    sent, failed = 0, 0
    start = time.time()
    
    for uid in users:
        try:
            bot.copy_message(uid, msg.chat.id, msg.message_id)
            sent += 1
            time.sleep(0.05) # Rate limit safety
        except:
            failed += 1
            
    bot.send_message(admin_id, 
        f"✅ **Broadcast Finished**\n"
        f"▬▬▬▬▬▬▬▬▬▬▬▬\n"
        f"👥 Sent: `{sent}`\n"
        f"❌ Failed: `{failed}`\n"
        f"⏱ Time: `{round(time.time()-start, 2)}s`"
    )

def admin_delete_logic(message):
    code = message.text.strip()
    db.delete_file(code)
    bot.reply_to(message, f"🗑 File `{code}` removed from Database.")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🌐 WEBSERVER KEEP-ALIVE (For Render)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def keep_alive():
    """Dummy server to satisfy Render Port check"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(('0.0.0.0', int(os.environ.get("PORT", 8080))))
        sock.listen(1)
        print("✅ Dummy Server Listening on Port 8080")
    except Exception as e:
        print(f"⚠️ Server Bind Error: {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🚀 MAIN LOOP
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    if not BOT_TOKEN or not BIN_CHANNEL:
        print("❌ CRITICAL: BOT_TOKEN or BIN_CHANNEL missing.")
        sys.exit()

    print("🔥 Bot 2.0 Starting...")
    
    # Start Dummy Server for Render
    threading.Thread(target=keep_alive, daemon=True).start()

    while True:
        try:
            bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except Exception as e:
            print(f"⚠️ Polling Error: {e}")
            time.sleep(5)
