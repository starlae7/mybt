import telebot
import os
import sqlite3
import shutil
import time
import threading
import uuid
from datetime import datetime
from dotenv import load_dotenv
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# Копируем базу из GitHub-бэкапа только при самом первом запуске
if not os.path.exists('prod_database.db') and os.path.exists('source.db'):
    shutil.copy('source.db', 'prod_database.db')
    print("✅ База данных успешно восстановлена из бэкапа!")

load_dotenv()
TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = 1014329713 # <--- ТВОЙ ID

if not TOKEN:
    print("Ошибка: Токен не найден! Проверь файл .env")
    exit()

bot = telebot.TeleBot(TOKEN)

# Временное хранилище для состояний рассылки
broadcast_data = {}

# --- РАБОТА С БАЗОЙ ДАННЫХ ---
def init_db():
    conn = sqlite3.connect('prod_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS files (
            code TEXT PRIMARY KEY,
            file_id TEXT,
            file_type TEXT,
            caption TEXT,
            downloads INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            join_date TEXT,
            last_active TEXT
        )
    ''')
    # 🔥 НОВОЕ: Таблица для хранения логов рассылки (чтобы потом удалять)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS broadcast_logs (
            broadcast_id TEXT,
            user_id INTEGER,
            message_id INTEGER
        )
    ''')
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
        cursor.execute('INSERT OR REPLACE INTO files (code, file_id, file_type, caption, downloads) VALUES (?, ?, ?, ?, 0)', 
                       (code, file_id, file_type, caption))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Ошибка БД: {e}")
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
        bot.reply_to(message, "⚠️ Сделай Reply на файл и напиши `/save код`", parse_mode='Markdown')
        return

    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "⚠️ Укажи код. Пример: `/save minecraft`", parse_mode='Markdown')
        return

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
            bot.reply_to(message, f"✅ Сохранено!\nКод: `{code}`\nСсылка: {link}", parse_mode='Markdown')
        else:
            bot.reply_to(message, "❌ Ошибка БД.")
    else:
        bot.reply_to(message, "⚠️ Файл не найден.")

@bot.message_handler(commands=['delete'])
def delete_command(message):
    if message.from_user.id != ADMIN_ID: return
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "⚠️ Укажи код.", parse_mode='Markdown'); return
    code = args[1]
    if delete_file_from_db(code):
        bot.reply_to(message, f"🗑 Файл `{code}` удален!", parse_mode='Markdown')
    else:
        bot.reply_to(message, "❌ Файл не найден.")


# 🔥 НОВЫЙ БЛОК: РАССЫЛКА И УДАЛЕНИЕ СООБЩЕНИЙ 🔥

@bot.message_handler(commands=['broadcast'])
def cmd_broadcast(message):
    if message.from_user.id != ADMIN_ID: return
    msg = bot.send_message(message.chat.id, "Отправь мне сообщение для рассылки (текст, фото, видео - что угодно). \nЯ скопирую его со всеми форматированиями.")
    bot.register_next_step_handler(msg, process_broadcast_preview)

def process_broadcast_preview(message):
    # Показываем админу, как будет выглядеть сообщение
    bot.send_message(message.chat.id, "👀 **Предпросмотр сообщения:**", parse_mode='Markdown')
    bot.copy_message(message.chat.id, message.chat.id, message.message_id)
    
    # Сохраняем ID сообщения для дальнейшего копирования
    broadcast_data[message.from_user.id] = {'message_id': message.message_id}
    
    # Создаем клавиатуру
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
        # Запускаем рассылку в фоновом потоке
        threading.Thread(target=run_broadcast, args=(admin_id, broadcast_data[admin_id]['message_id'], None)).start()
        
    elif call.data == "bc_limit":
        msg = bot.edit_message_text("Напиши число пользователей, которым нужно отправить сообщение:", call.message.chat.id, call.message.message_id)
        bot.register_next_step_handler(msg, process_broadcast_limit)

def process_broadcast_limit(message):
    if not message.text.isdigit():
        bot.send_message(message.chat.id, "❌ Это не число. Рассылка отменена.")
        return
    limit = int(message.text)
    bot.send_message(message.chat.id, f"🚀 Начинаю рассылку для {limit} пользователей...")
    threading.Thread(target=run_broadcast, args=(message.from_user.id, broadcast_data[message.from_user.id]['message_id'], limit)).start()

def run_broadcast(admin_id, message_id_to_copy, limit):
    conn = sqlite3.connect('prod_database.db')
    cursor = conn.cursor()
    
    # Берем пользователей
    if limit:
        cursor.execute('SELECT user_id FROM users LIMIT ?', (limit,))
    else:
        cursor.execute('SELECT user_id FROM users')
        
    users = cursor.fetchall()
    broadcast_id = str(uuid.uuid4())[:8] # Генерируем короткий уникальный ID рассылки
    
    success = 0
    blocked = 0
    
    for user in users:
        user_id = user[0]
        try:
            # Копируем сообщение
            sent_msg = bot.copy_message(user_id, admin_id, message_id_to_copy)
            # Записываем ID отправленного сообщения в базу
            cursor.execute('INSERT INTO broadcast_logs (broadcast_id, user_id, message_id) VALUES (?, ?, ?)', 
                           (broadcast_id, user_id, sent_msg.message_id))
            success += 1
        except Exception as e:
            # Если пользователь заблокировал бота
            blocked += 1
            
        # 🔥 Анти-спам задержка: 20 сообщений в секунду
        time.sleep(0.05)
        
    conn.commit()
    conn.close()
    
    # Отчет админу
    report = (f"✅ **Рассылка завершена!**\n"
              f"Успешно: {success}\n"
              f"Заблокировали бота: {blocked}\n\n"
              f"🗑 Чтобы удалить это сообщение у всех, напиши:\n"
              f"`/delcast {broadcast_id}`")
    bot.send_message(admin_id, report, parse_mode='Markdown')

@bot.message_handler(commands=['delcast'])
def cmd_delete_broadcast(message):
    if message.from_user.id != ADMIN_ID: return
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "⚠️ Укажи ID рассылки. Пример: `/delcast a1b2c3d4`", parse_mode='Markdown')
        return
        
    broadcast_id = args[1]
    bot.reply_to(message, "⏳ Начинаю удаление сообщений...")
    threading.Thread(target=run_delete_broadcast, args=(message.from_user.id, broadcast_id)).start()

def run_delete_broadcast(admin_id, broadcast_id):
    conn = sqlite3.connect('prod_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, message_id FROM broadcast_logs WHERE broadcast_id = ?', (broadcast_id,))
    logs = cursor.fetchall()
    
    if not logs:
        bot.send_message(admin_id, "❌ Рассылка с таким ID не найдена.")
        conn.close()
        return
        
    deleted = 0
    for log in logs:
        user_id, msg_id = log
        try:
            bot.delete_message(user_id, msg_id)
            deleted += 1
        except Exception:
            pass # Игнорируем, если сообщение уже удалено пользователем или бот заблокирован
            
        time.sleep(0.05) # Задержка для API
        
    # Удаляем логи из базы
    cursor.execute('DELETE FROM broadcast_logs WHERE broadcast_id = ?', (broadcast_id,))
    conn.commit()
    conn.close()
    
    bot.send_message(admin_id, f"🗑 **Удаление завершено.**\nУдалено сообщений: {deleted}", parse_mode='Markdown')

# 🔥 ВРЕМЕННАЯ ФУНКЦИЯ ДЛЯ ВОССТАНОВЛЕНИЯ БАЗЫ
@bot.message_handler(content_types=['document'])
def restore_database(message):
    if message.from_user.id != ADMIN_ID: 
        return
        
    if message.document.file_name == 'prod_database.db':
        bot.reply_to(message, "⏳ Загружаю старую базу данных на сервер...")
        try:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            
            with open('prod_database.db', 'wb') as new_file:
                new_file.write(downloaded_file)
                
            bot.reply_to(message, "✅ База данных успешно восстановлена! Старые данные подгружены.")
        except Exception as e:
            bot.reply_to(message, f"❌ Ошибка при загрузке: {e}")

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
    
    text = f"📊 **Подробная статистика:**\n\n"
    text += f"👥 Всего пользователей: `{total_users}`\n"
    text += f"📈 Новых за сегодня: `{new_today}`\n"
    text += f"🔥 Активных сегодня (DAU): `{dau}`\n"
    text += f"📅 Активных в этом месяце (MAU): `{mau}`\n"
    text += f"➖➖➖➖➖➖➖\n"
    text += f"🏆 **Топ-10 скачиваний:**\n"
    if not top_files:
        text += "Пока ничего не скачивали."
    else:
        for i, (code, count) in enumerate(top_files, 1):
            text += f"{i}. `{code}` — {count} раз\n"
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

bot.infinity_polling()
