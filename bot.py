import telebot
import os
import sqlite3
import shutil
import time
import threading
import uuid
import json
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, BotCommandScopeChat, BotCommandScopeDefault

# 🔥 ИМПОРТЫ ДЛЯ WEBHOOK 🔥
from flask import Flask, request

# Копируем базу из GitHub-бэкапа только при самом первом запуске
if not os.path.exists('prod_database.db') and os.path.exists('source.db'):
    shutil.copy('source.db', 'prod_database.db')
    print("✅ База данных успешно восстановлена из бэкапа!")

load_dotenv()
TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = 1014329713 # <--- ТВОЙ ID

if not TOKEN:
    print("Ошибка: Токен не найден! Проверь файл .env или переменные окружения на хостинге.")
    exit()

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__) # Инициализация веб-сервера Flask

# 🔥 ВАЖНО: Вставь сюда АВТОМАТИЧЕСКИЙ домен, который выдаст тебе хостинг! 🔥
# Указывать обязательно с https:// и без слеша на конце!
WEBHOOK_HOST = 'https://ТВОЙ-ДОМЕН.bothost.ru' 

# Временные хранилища данных
broadcast_data = {}
post_drafts = {} # 🔥 Для хранения черновиков постов

# 🔥 КЛАСС ДЛЯ ПОДДЕРЖКИ ЦВЕТНЫХ КНОПОК И ЭМОДЗИ (API 9.4) 🔥
class ModernInlineKeyboardButton(InlineKeyboardButton):
    def __init__(self, text, url=None, callback_data=None, style=None, icon_custom_emoji_id=None, **kwargs):
        super().__init__(text, url=url, callback_data=callback_data, **kwargs)
        self.style = style
        self.icon_custom_emoji_id = icon_custom_emoji_id

    def to_dict(self):
        d = super().to_dict()
        if self.style: d['style'] = self.style
        if self.icon_custom_emoji_id: d['icon_custom_emoji_id'] = self.icon_custom_emoji_id
        return d

def modern_markup_from_json(json_str):
    if not json_str: return None
    data = json.loads(json_str)
    markup = InlineKeyboardMarkup()
    for row in data.get('keyboard', data.get('inline_keyboard', [])):
        new_row = []
        for btn in row:
            new_row.append(ModernInlineKeyboardButton(**btn))
        markup.keyboard.append(new_row)
    return markup


# --- РАБОТА С БАЗОЙ ДАННЫХ ---
def init_db():
    conn = sqlite3.connect('prod_database.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS files (code TEXT PRIMARY KEY, file_id TEXT, file_type TEXT, caption TEXT, downloads INTEGER DEFAULT 0)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, join_date TEXT, last_active TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS broadcast_logs (broadcast_id TEXT, user_id INTEGER, message_id INTEGER)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS channels (username TEXT PRIMARY KEY)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS scheduled_posts (id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER, channel TEXT, message_id INTEGER, buttons TEXT, publish_time INTEGER)''')
    conn.commit()
    conn.close()

def log_user(user_id):
    conn = sqlite3.connect('prod_database.db')
    cursor = conn.cursor()
    date_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
    if cursor.fetchone() is None:
        cursor.execute('INSERT INTO users (user_id, join_date, last_active) VALUES (?, ?, ?)', (user_id, date_now, date_now))
    else:
        cursor.execute('UPDATE users SET last_active = ? WHERE user_id = ?', (date_now, user_id))
    conn.commit()
    conn.close()

def increment_download(code):
    conn = sqlite3.connect('prod_database.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE files SET downloads = downloads + 1 WHERE code = ?', (code,))
    conn.commit()
    conn.close()

def add_file_to_db(code, file_id, file_type, caption):
    try:
        conn = sqlite3.connect('prod_database.db')
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO files (code, file_id, file_type, caption, downloads) VALUES (?, ?, ?, ?, 0)', (code, file_id, file_type, caption))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        return False

def get_file_from_db(code):
    conn = sqlite3.connect('prod_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT file_id, file_type, caption FROM files WHERE code = ?', (code,))
    result = cursor.fetchone()
    conn.close()
    return result

def delete_file_from_db(code):
    conn = sqlite3.connect('prod_database.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM files WHERE code = ?', (code,))
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted_count > 0

def add_channel_db(username):
    try:
        conn = sqlite3.connect('prod_database.db')
        cursor = conn.cursor()
        cursor.execute('INSERT OR IGNORE INTO channels (username) VALUES (?)', (username,))
        conn.commit()
        conn.close()
        return True
    except: return False

def get_channels_db():
    conn = sqlite3.connect('prod_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT username FROM channels')
    res = [row[0] for row in cursor.fetchall()]
    conn.close()
    return res

def del_channel_db(username):
    conn = sqlite3.connect('prod_database.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM channels WHERE username = ?', (username,))
    conn.commit()
    conn.close()

init_db()

# --- КОМАНДЫ ПОЛЬЗОВАТЕЛЯ ---

@bot.message_handler(commands=['start'])
def start_message(message):
    log_user(message.from_user.id)
    args = message.text.split()
    if len(args) > 1:
        code = args[1]
        data = get_file_from_db(code)
        if data:
            file_id, file_type, caption = data
            try:
                if file_type == 'document': bot.send_document(message.chat.id, file_id, caption=caption)
                elif file_type == 'video': bot.send_video(message.chat.id, file_id, caption=caption)
                elif file_type == 'photo': bot.send_photo(message.chat.id, file_id, caption=caption)
                elif file_type == 'audio': bot.send_audio(message.chat.id, file_id, caption=caption)
                increment_download(code)
            except Exception as e:
                bot.send_message(message.chat.id, "Ой, что-то пошло не так при отправке файла.")
        else:
            bot.send_message(message.chat.id, "Файл не найден. Проверьте ссылку.")
    else:
        bot.send_message(message.chat.id, 'Привет! Я бот для скачивания файлов. Переходи по ссылкам из канала <a href="https://t.me/AppVault7">AppVault</a>.', parse_mode='HTML')

# --- КОМАНДЫ АДМИНА ---

@bot.message_handler(commands=['save'])
def save_file_command(message):
    if message.from_user.id != ADMIN_ID: return
    if not message.reply_to_message:
        return bot.reply_to(message, "⚠️ Сделай Reply (Ответить) на файл и напиши команду с кодом.\nПример: `/save minecraft`", parse_mode='Markdown')

    args = message.text.split()
    if len(args) < 2:
        return bot.reply_to(message, "⚠️ Укажи код для сохранения файла.\nПример: `/save minecraft`", parse_mode='Markdown')

    code = args[1].lower()
    target_msg = message.reply_to_message
    file_id = None; file_type = None

    if target_msg.document: file_id = target_msg.document.file_id; file_type = 'document'
    elif target_msg.video: file_id = target_msg.video.file_id; file_type = 'video'
    elif target_msg.photo: file_id = target_msg.photo[-1].file_id; file_type = 'photo'
    elif target_msg.audio: file_id = target_msg.audio.file_id; file_type = 'audio'
    
    if file_id:
        caption = "🗂 Держи свой файл!\nНе забудь поставить реакцию на канал 💙\n@AppVault7"
        if add_file_to_db(code, file_id, file_type, caption):
            link = f"https://t.me/{bot.get_me().username}?start={code}"
            bot.reply_to(message, f"✅ **Файл успешно сохранен!**\n\n🆔 Код: `{code}`\n🔗 Ссылка для скачивания:\n{link}", parse_mode='Markdown')
        else:
            bot.reply_to(message, "❌ Ошибка базы данных при сохранении.")
    else:
        bot.reply_to(message, "⚠️ Не удалось распознать файл в сообщении.")

@bot.message_handler(commands=['delete'])
def delete_command(message):
    if message.from_user.id != ADMIN_ID: return
    args = message.text.split()
    if len(args) < 2: return bot.reply_to(message, "⚠️ Укажи код файла для удаления.\nПример: `/delete minecraft`", parse_mode='Markdown')
    code = args[1]
    if delete_file_from_db(code): bot.reply_to(message, f"🗑 Файл с кодом `{code}` успешно удален из базы!", parse_mode='Markdown')
    else: bot.reply_to(message, "❌ Файл с таким кодом не найден.")

# 🔥 БЛОК: РАССЫЛКА И УДАЛЕНИЕ СООБЩЕНИЙ 🔥

@bot.message_handler(commands=['broadcast'])
def cmd_broadcast(message):
    if message.from_user.id != ADMIN_ID: return
    msg = bot.send_message(message.chat.id, "📢 **Создание рассылки**\n\nОтправь мне сообщение (текст, фото, кружочек, медиагруппу), и я разошлю его всем пользователям бота.\nВсе форматирования будут сохранены.", parse_mode='Markdown')
    bot.register_next_step_handler(msg, process_broadcast_preview)

def process_broadcast_preview(message):
    bot.send_message(message.chat.id, "👀 **Предпросмотр рассылки:**\n*(Именно так пользователи увидят твое сообщение)*", parse_mode='Markdown')
    bot.copy_message(message.chat.id, message.chat.id, message.message_id)
    broadcast_data[message.from_user.id] = {'message_id': message.message_id}
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ Отправить всем", callback_data="bc_all"))
    markup.add(InlineKeyboardButton("🔢 Выбрать количество", callback_data="bc_limit"))
    markup.add(InlineKeyboardButton("❌ Отмена", callback_data="bc_cancel"))
    bot.send_message(message.chat.id, "Что делаем дальше?", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('bc_'))
def broadcast_callback(call):
    admin_id = call.from_user.id
    if admin_id != ADMIN_ID: return
    bot.answer_callback_query(call.id)
    
    if call.data == "bc_cancel":
        bot.edit_message_text("❌ Рассылка отменена.", call.message.chat.id, call.message.message_id)
        broadcast_data.pop(admin_id, None)
    elif call.data == "bc_all":
        bot.edit_message_text("🚀 Начинаю рассылку всем пользователям...", call.message.chat.id, call.message.message_id)
        threading.Thread(target=run_broadcast, args=(admin_id, broadcast_data[admin_id]['message_id'], None)).start()
    elif call.data == "bc_limit":
        msg = bot.edit_message_text("Напиши число пользователей, которым нужно отправить сообщение:", call.message.chat.id, call.message.message_id)
        bot.register_next_step_handler(msg, process_broadcast_limit)

def process_broadcast_limit(message):
    if not message.text.isdigit():
        return bot.send_message(message.chat.id, "❌ Это не число. Рассылка отменена.")
    limit = int(message.text)
    bot.send_message(message.chat.id, f"🚀 Начинаю рассылку для {limit} пользователей...")
    threading.Thread(target=run_broadcast, args=(message.from_user.id, broadcast_data[message.from_user.id]['message_id'], limit)).start()

def run_broadcast(admin_id, message_id_to_copy, limit):
    conn = sqlite3.connect('prod_database.db')
    cursor = conn.cursor()
    if limit: cursor.execute('SELECT user_id FROM users LIMIT ?', (limit,))
    else: cursor.execute('SELECT user_id FROM users')
    users = cursor.fetchall()
    broadcast_id = str(uuid.uuid4())[:8] 
    
    success = 0; blocked = 0
    for user in users:
        user_id = user[0]
        try:
            sent_msg = bot.copy_message(user_id, admin_id, message_id_to_copy)
            cursor.execute('INSERT INTO broadcast_logs (broadcast_id, user_id, message_id) VALUES (?, ?, ?)', (broadcast_id, user_id, sent_msg.message_id))
            success += 1
        except Exception: blocked += 1
        time.sleep(0.05)
        
    conn.commit()
    conn.close()
    
    report = (f"✅ **Рассылка завершена!**\nУспешно: {success}\nЗаблокировали бота: {blocked}\n\n"
              f"🗑 Чтобы удалить это сообщение у всех, напиши:\n`/delcast {broadcast_id}`")
    bot.send_message(admin_id, report, parse_mode='Markdown')

@bot.message_handler(commands=['delcast'])
def cmd_delete_broadcast(message):
    if message.from_user.id != ADMIN_ID: return
    args = message.text.split()
    if len(args) < 2:
        return bot.reply_to(message, "⚠️ Укажи ID рассылки для удаления.\nПример: `/delcast a1b2c3d4`", parse_mode='Markdown')
    broadcast_id = args[1]
    bot.reply_to(message, f"⏳ Начинаю удаление сообщений рассылки `{broadcast_id}`...", parse_mode='Markdown')
    threading.Thread(target=run_delete_broadcast, args=(message.from_user.id, broadcast_id)).start()

def run_delete_broadcast(admin_id, broadcast_id):
    conn = sqlite3.connect('prod_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, message_id FROM broadcast_logs WHERE broadcast_id = ?', (broadcast_id,))
    logs = cursor.fetchall()
    
    if not logs:
        bot.send_message(admin_id, "❌ Рассылка с таким ID не найдена (возможно, она уже была удалена).")
        conn.close(); return
        
    deleted = 0; first_error = None
    for log in logs:
        user_id, msg_id = log
        try:
            bot.delete_message(chat_id=int(user_id), message_id=int(msg_id))
            deleted += 1
        except Exception as e:
            if not first_error: first_error = str(e)
            if "Too Many Requests" in str(e): time.sleep(3)
        time.sleep(0.1) 
        
    if deleted > 0 or first_error is None:
        cursor.execute('DELETE FROM broadcast_logs WHERE broadcast_id = ?', (broadcast_id,))
        conn.commit()
    conn.close()
    
    report = f"🗑 **Удаление завершено.**\nУдалено сообщений: {deleted} из {len(logs)}"
    if deleted < len(logs) and first_error: report += f"\n\n⚠️ **Причина ошибки у некоторых юзеров:**\n`{first_error}`"
    bot.send_message(admin_id, report, parse_mode='Markdown')

# 🔥 БЛОК: СОЗДАНИЕ И ОТЛОЖЕННАЯ ПУБЛИКАЦИЯ ПОСТОВ 🔥

@bot.message_handler(commands=['addchannel'])
def cmd_add_channel(message):
    if message.from_user.id != ADMIN_ID: return
    args = message.text.split()
    if len(args) < 2: return bot.reply_to(message, "⚠️ Укажи юзернейм канала для добавления.\nПример: `/addchannel @AppVault7`", parse_mode='Markdown')
    channel = args[1]
    if not channel.startswith('@'): channel = '@' + channel
    if add_channel_db(channel): bot.reply_to(message, f"✅ Канал {channel} добавлен в базу.\n\n⚠️ **Важно:** Не забудь выдать боту права администратора в этом канале (право на публикацию сообщений)!")
    else: bot.reply_to(message, "❌ Ошибка базы данных при добавлении канала.")

@bot.message_handler(commands=['delchannel'])
def cmd_del_channel(message):
    if message.from_user.id != ADMIN_ID: return
    args = message.text.split()
    if len(args) < 2: return bot.reply_to(message, "⚠️ Укажи юзернейм канала для удаления.\nПример: `/delchannel @AppVault7`", parse_mode='Markdown')
    channel = args[1] if args[1].startswith('@') else '@' + args[1]
    del_channel_db(channel)
    bot.reply_to(message, f"🗑 Канал {channel} успешно удален из базы.")

@bot.message_handler(commands=['post'])
def cmd_post(message):
    if message.from_user.id != ADMIN_ID: return
    channels = get_channels_db()
    if not channels: return bot.reply_to(message, "⚠️ Сперва добавь канал в базу с помощью команды:\n`/addchannel @твойканал`", parse_mode='Markdown')
    
    markup = InlineKeyboardMarkup()
    for ch in channels: markup.add(InlineKeyboardButton(ch, callback_data=f"p_chan_{ch}"))
    bot.send_message(message.chat.id, "📢 **Создание поста**\n\nВыбери канал для публикации из списка ниже:", reply_markup=markup, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data.startswith('p_chan_'))
def post_channel_selected(call):
    admin_id = call.from_user.id
    if admin_id != ADMIN_ID: return
    channel = call.data.replace('p_chan_', '')
    post_drafts[admin_id] = {'channel': channel, 'message_id': None, 'buttons': None}
    
    instruction = (
        f"📢 Ты выбрал канал: **{channel}**\n\n"
        "Отправь боту сообщение, которое собираешься опубликовать.\n"
        "*(Поддерживается любой формат: текст со скрытым текстом/спойлерами, фото, видео, кружочки и документы)*"
    )
    bot.edit_message_text(instruction, call.message.chat.id, call.message.message_id, parse_mode='Markdown')
    bot.register_next_step_handler(call.message, process_post_content)

def process_post_content(message):
    admin_id = message.from_user.id
    if admin_id not in post_drafts: return
    post_drafts[admin_id]['message_id'] = message.message_id
    show_post_menu(admin_id, message.chat.id)

def show_post_menu(admin_id, chat_id):
    draft = post_drafts.get(admin_id)
    if not draft: return
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔗 Урл-кнопки", callback_data="post_add_btns"))
    markup.add(InlineKeyboardButton("👁 Предпросмотр", callback_data="post_preview"))
    markup.add(InlineKeyboardButton("✅ Опубликовать", callback_data="post_publish"), InlineKeyboardButton("📅 Отложить", callback_data="post_schedule"))
    markup.add(InlineKeyboardButton("❌ Отмена", callback_data="post_cancel"))
    bot.send_message(chat_id, "⚙️ **Меню поста:**\nЧто делаем дальше?", reply_markup=markup, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data.startswith('post_'))
def post_menu_callback(call):
    admin_id = call.from_user.id
    if admin_id != ADMIN_ID: return
    bot.answer_callback_query(call.id)
    action = call.data
    draft = post_drafts.get(admin_id)
    
    if not draft and action != 'post_cancel': return bot.send_message(call.message.chat.id, "❌ Черновик не найден. Начни заново через /post.")

    if action == "post_cancel":
        post_drafts.pop(admin_id, None)
        bot.edit_message_text("❌ Создание поста отменено.", call.message.chat.id, call.message.message_id)
    elif action == "post_add_btns":
        text = (
            "🛠 **Настройка URL-кнопок**\n\n"
            "Отправь список кнопок в таком формате:\n"
            "`Кнопка 1 - https://t.me/durov`\n\n"
            "💡 **Полезные фишки:**\n"
            "1️⃣ **Кнопки в ряд:** используй разделитель `|`\n"
            "`Кнопка 1 - http://... | Кнопка 2 - http://...`\n\n"
            "2️⃣ **Цветные кнопки (API 9.4):** добавь тег перед названием\n"
            "🔵 `(primary)` — синяя (основная)\n"
            "🟢 `(success)` — зеленая (успех/скачать)\n"
            "🔴 `(danger)` — красная (удалить/отмена)\n\n"
            "3️⃣ **Кастомные эмодзи:** просто вставь их в текст кнопки!\n\n"
            "📝 **Пример идеальной кнопки:**\n"
            "`(success) Скачать 📥 - https://t.me/durov`\n\n"
            "❌ Напиши `0` (ноль), чтобы удалить все кнопки."
        )
        msg = bot.send_message(call.message.chat.id, text, parse_mode='Markdown')
        bot.register_next_step_handler(msg, process_post_buttons)
    elif action == "post_preview":
        bot.send_message(call.message.chat.id, f"👀 **Предпросмотр поста для {draft['channel']}:**", parse_mode='Markdown')
        try:
            markup = modern_markup_from_json(draft['buttons'].to_json()) if draft['buttons'] else None
            bot.copy_message(call.message.chat.id, admin_id, draft['message_id'], reply_markup=markup)
        except Exception as e: bot.send_message(call.message.chat.id, f"❌ Ошибка предпросмотра: {e}")
        show_post_menu(admin_id, call.message.chat.id)
    elif action == "post_publish":
        bot.edit_message_text("🚀 Публикую...", call.message.chat.id, call.message.message_id)
        try:
            markup = modern_markup_from_json(draft['buttons'].to_json()) if draft['buttons'] else None
            bot.copy_message(draft['channel'], admin_id, draft['message_id'], reply_markup=markup)
            bot.send_message(call.message.chat.id, f"✅ Сообщение успешно опубликовано в **{draft['channel']}**!", parse_mode='Markdown')
            post_drafts.pop(admin_id, None) 
        except Exception as e: bot.send_message(call.message.chat.id, f"❌ Ошибка публикации.\nУбедись, что бот является администратором канала.\n\n`{e}`", parse_mode='Markdown')
    elif action == "post_schedule":
        text = (
            "⏳ **Отложенная публикация**\n\n"
            "Отправь дату и время по **Московскому времени** (UTC+3).\n\n"
            "Формат: `ДД.ММ.ГГГГ ЧЧ:ММ`\n"
            "Пример: `25.12.2023 15:30`\n\n"
            "❌ Напиши `0` (ноль), чтобы отменить и вернуться в меню."
        )
        msg = bot.send_message(call.message.chat.id, text, parse_mode='Markdown')
        bot.register_next_step_handler(msg, process_schedule_time)

def process_post_buttons(message):
    admin_id = message.from_user.id
    if admin_id not in post_drafts: return
    
    if message.text.lower().strip() in ['0', 'отмена']:
        post_drafts[admin_id]['buttons'] = None
        bot.send_message(message.chat.id, "🗑 Кнопки успешно удалены.")
        show_post_menu(admin_id, message.chat.id)
        return

    custom_emojis = {}
    if message.entities:
        for ent in message.entities:
            if ent.type == 'custom_emoji':
                emo_txt = message.text[ent.offset : ent.offset + ent.length]
                custom_emojis[emo_txt] = ent.custom_emoji_id

    markup = InlineKeyboardMarkup()
    try:
        for line in message.text.split('\n'):
            if not line.strip(): continue
            row = []
            for btn in line.split('|'):
                parts = btn.split('-', 1)
                if len(parts) == 2:
                    btn_text, btn_url = parts[0].strip(), parts[1].strip()
                    btn_style = None
                    if btn_text.lower().startswith('(primary)'): btn_style = 'primary'; btn_text = btn_text[9:].strip()
                    elif btn_text.lower().startswith('(danger)'): btn_style = 'danger'; btn_text = btn_text[8:].strip()
                    elif btn_text.lower().startswith('(success)'): btn_style = 'success'; btn_text = btn_text[9:].strip()
                    
                    btn_emoji_id = None
                    for emo_txt, emo_id in custom_emojis.items():
                        if emo_txt in btn_text:
                            btn_emoji_id = emo_id
                            btn_text = btn_text.replace(emo_txt, '', 1).strip()
                            break

                    row.append(ModernInlineKeyboardButton(text=btn_text, url=btn_url, style=btn_style, icon_custom_emoji_id=btn_emoji_id))
            if row: markup.add(*row)
            
        post_drafts[admin_id]['buttons'] = markup
        bot.send_message(message.chat.id, "✅ Клавиатура успешно сохранена!")
    except Exception as e: bot.send_message(message.chat.id, f"❌ Ошибка форматирования кнопок: {e}")
    show_post_menu(admin_id, message.chat.id)

def process_schedule_time(message):
    admin_id = message.from_user.id
    draft = post_drafts.get(admin_id)
    if not draft: return
    if message.text.lower().strip() in ['0', 'отмена', 'cancel']:
        return show_post_menu(admin_id, message.chat.id)
        
    try:
        dt = datetime.strptime(message.text.strip(), "%d.%m.%Y %H:%M")
        MSK = timezone(timedelta(hours=3))
        publish_time = int(dt.replace(tzinfo=MSK).timestamp()) 
        
        if publish_time <= time.time():
            msg = bot.send_message(message.chat.id, "❌ **Это время уже прошло!**\nПожалуйста, введи время в будущем (или напиши `0` для отмены):", parse_mode='Markdown')
            return bot.register_next_step_handler(msg, process_schedule_time) 
            
        buttons_json = draft['buttons'].to_json() if draft['buttons'] else None
        conn = sqlite3.connect('prod_database.db')
        cursor = conn.cursor()
        cursor.execute('''INSERT INTO scheduled_posts (admin_id, channel, message_id, buttons, publish_time) VALUES (?, ?, ?, ?, ?)''', 
                       (admin_id, draft['channel'], draft['message_id'], buttons_json, publish_time))
        conn.commit()
        conn.close()
        
        bot.send_message(message.chat.id, f"✅ **Пост успешно отложен!**\nОн будет автоматически опубликован в **{draft['channel']}** ровно в `{message.text}` (по Москве).", parse_mode='Markdown')
        post_drafts.pop(admin_id, None)
    except ValueError:
        msg = bot.send_message(message.chat.id, "❌ **Неверный формат времени!**\nПожалуйста, используй строгий формат: `ДД.ММ.ГГГГ ЧЧ:ММ`\nПример: `25.12.2023 15:30`\n\nПопробуй еще раз (или напиши `0` для отмены):", parse_mode='Markdown')
        bot.register_next_step_handler(msg, process_schedule_time)

def scheduler_loop():
    while True:
        try:
            conn = sqlite3.connect('prod_database.db')
            cursor = conn.cursor()
            current_time = int(time.time())
            cursor.execute('SELECT id, admin_id, channel, message_id, buttons FROM scheduled_posts WHERE publish_time <= ?', (current_time,))
            posts = cursor.fetchall()
            for post in posts:
                post_id, admin_id, channel, message_id, buttons_json = post
                markup = modern_markup_from_json(buttons_json)
                try:
                    bot.copy_message(channel, admin_id, message_id, reply_markup=markup)
                    bot.send_message(admin_id, f"✅ Отложенный пост автоматически опубликован в **{channel}**!", parse_mode='Markdown')
                except Exception as e: bot.send_message(admin_id, f"❌ Ошибка автоматической публикации поста в {channel}: {e}")
                cursor.execute('DELETE FROM scheduled_posts WHERE id = ?', (post_id,))
            conn.commit()
            conn.close()
        except Exception: pass
        time.sleep(30)

threading.Thread(target=scheduler_loop, daemon=True).start()

# 🔥 ВРЕМЕННАЯ ФУНКЦИЯ ДЛЯ ВОССТАНОВЛЕНИЯ БАЗЫ
@bot.message_handler(content_types=['document'])
def restore_database(message):
    if message.from_user.id != ADMIN_ID: return
    if message.document.file_name == 'prod_database.db':
        bot.reply_to(message, "⏳ Загружаю старую базу данных на сервер...")
        try:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            with open('prod_database.db', 'wb') as new_file: new_file.write(downloaded_file)
            bot.reply_to(message, "✅ База данных успешно восстановлена!")
        except Exception as e: bot.reply_to(message, f"❌ Ошибка: {e}")

@bot.message_handler(commands=['stats'])
def stats_command(message):
    if message.from_user.id != ADMIN_ID: return
    conn = sqlite3.connect('prod_database.db')
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    this_month = datetime.now().strftime("%Y-%m")
    cursor.execute('SELECT COUNT(*) FROM users')
    total_users = cursor.fetchone()[0]
    cursor.execute(f"SELECT COUNT(*) FROM users WHERE join_date LIKE '{today}%'")
    new_today = cursor.fetchone()[0]
    cursor.execute(f"SELECT COUNT(*) FROM users WHERE last_active LIKE '{today}%'")
    dau = cursor.fetchone()[0]
    cursor.execute(f"SELECT COUNT(*) FROM users WHERE last_active LIKE '{this_month}%'")
    mau = cursor.fetchone()[0]
    cursor.execute('SELECT code, downloads FROM files ORDER BY downloads DESC LIMIT 10')
    top_files = cursor.fetchall()
    conn.close()
    
    text = (
        "📊 **Статистика бота:**\n\n"
        f"👥 Всего пользователей: `{total_users}`\n"
        f"📈 Новых за сегодня: `{new_today}`\n"
        f"🔥 Активных сегодня (DAU): `{dau}`\n"
        f"📅 Активных за месяц (MAU): `{mau}`\n\n"
        "🏆 **Топ-10 файлов по скачиваниям:**\n"
    )
    
    if not top_files: text += "Скачиваний пока нет."
    else:
        for i, (code, count) in enumerate(top_files, 1): text += f"{i}. `{code}` — {count} раз\n"
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

def setup_bot_commands():
    try:
        bot.set_my_commands([BotCommand("/start", "Запустить бота")], scope=BotCommandScopeDefault())
        admin_commands = [
            BotCommand("/start", "Запустить бота"), BotCommand("/post", "Создать пост в канал"),
            BotCommand("/broadcast", "Сделать рассылку"), BotCommand("/stats", "Статистика бота"),
            BotCommand("/save", "Сохранить файл"), BotCommand("/delete", "Удалить файл"),
            BotCommand("/addchannel", "Добавить канал"), BotCommand("/delchannel", "Удалить канал")
        ]
        bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=ADMIN_ID))
    except Exception: pass

setup_bot_commands()

# 🔥 НАСТРОЙКИ ДЛЯ РЕЖИМА WEBHOOK 🔥

# Маршрут для проверки, что сервер Flask работает
@app.route('/', methods=['GET', 'HEAD'])
def index():
    return '''
    <html>
        <head>
            <title>Bot Webhook</title>
            <meta charset="utf-8">
        </head>
        <body style="font-family: sans-serif; padding: 2rem; text-align: center; background-color: #f4f4f9;">
            <h1 style="color: #4CAF50;">✅ Бот успешно запущен!</h1>
            <p style="font-size: 18px;">Сервер Flask работает и готов принимать сообщения от Telegram.</p>
            <br><br>
            <a href="/set_webhook" style="padding: 15px 25px; background-color: #0088cc; color: white; text-decoration: none; border-radius: 8px; font-weight: bold; font-size: 18px;">🔗 Привязать Webhook Telegram</a>
        </body>
    </html>
    '''

# Маршрут для РУЧНОЙ установки вебхука (динамически берет правильный домен)
@app.route('/set_webhook', methods=['GET'])
def set_webhook():
    bot.remove_webhook()
    time.sleep(1)
    
    # Динамически получаем текущий домен, на который мы зашли (чтобы обойти ошибки в WEBHOOK_HOST)
    host_url = request.host_url.rstrip('/')
    webhook_url = f"{host_url}/{TOKEN}"
    
    success = bot.set_webhook(url=webhook_url)
    
    if success:
        return f'''
        <body style="font-family: sans-serif; padding: 2rem; text-align: center; background-color: #f4f4f9;">
            <h2 style="color:green;">✅ Webhook успешно установлен!</h2>
            <p style="font-size: 18px;">Telegram теперь привязан к адресу:</p>
            <p><b>{webhook_url}</b></p>
            <br>
            <p style="font-size: 18px;">Теперь просто напишите боту <b>/start</b> в Telegram!</p>
        </body>
        '''
    else:
        return '<h3 style="color:red; text-align:center;">❌ Ошибка установки Webhook! Проверьте правильность токена.</h3>'


# Маршрут, куда Telegram будет присылать обновления
@app.route(f'/{TOKEN}', methods=['POST']) # Без слэша!
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return ''
    else:
        return 'error', 403

if __name__ == '__main__':
    # Обязательно снимаем старый вебхук или поллинг перед запуском
    bot.remove_webhook()
    time.sleep(1)
    
    # Пытаемся установить Webhook при старте
    webhook_url = f"{WEBHOOK_HOST}/{TOKEN}" 
    bot.set_webhook(url=webhook_url)
    
    # Запускаем Flask-сервер.
    port = int(os.environ.get('PORT', 3000))
    app.run(host='0.0.0.0', port=port)
