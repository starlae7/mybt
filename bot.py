import telebot
import os
import sqlite3
from datetime import datetime # 🔥 НОВОЕ: Для работы с датами
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = 1014329713 # <--- НЕ ЗАБУДЬ ВСТАВИТЬ СВОЙ ID

if not TOKEN:
    print("Ошибка: Токен не найден! Проверь файл .env")
    exit()

bot = telebot.TeleBot(TOKEN)

# --- РАБОТА С БАЗОЙ ДАННЫХ ---
def init_db():
    # Эта функция теперь просто для страховки, основную работу мы сделали скриптом
    conn = sqlite3.connect('bot_database.db')
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
            join_date TEXT
        )
    ''')
    conn.commit()
    conn.close()

# 🔥 НОВОЕ: Функция регистрации пользователя
def log_user(user_id):
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    # Пытаемся добавить. Если такой ID уже есть, IGNORE пропустит ошибку
    date_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute('INSERT OR IGNORE INTO users (user_id, join_date) VALUES (?, ?)', (user_id, date_now))
    conn.commit()
    conn.close()

# 🔥 НОВОЕ: Функция счетчика скачиваний
def increment_download(code):
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE files SET downloads = downloads + 1 WHERE code = ?', (code,))
    conn.commit()
    conn.close()

# 🔥 НОВОЕ: Получение статистики
def get_bot_stats():
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    # Считаем людей
    cursor.execute('SELECT COUNT(*) FROM users')
    total_users = cursor.fetchone()[0]
    
    # Ищем ТОП-5 файлов
    cursor.execute('SELECT code, downloads FROM files ORDER BY downloads DESC LIMIT 10')
    top_files = cursor.fetchall()
    
    conn.close()
    return total_users, top_files

def add_file_to_db(code, file_id, file_type, caption):
    try:
        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()
        # При добавлении нового файла downloads = 0
        cursor.execute('INSERT OR REPLACE INTO files (code, file_id, file_type, caption, downloads) VALUES (?, ?, ?, ?, 0)', 
                       (code, file_id, file_type, caption))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Ошибка БД: {e}")
        return False

def get_file_from_db(code):
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT file_id, file_type, caption FROM files WHERE code = ?', (code,))
    result = cursor.fetchone()
    conn.close()
    return result

def delete_file_from_db(code):
    conn = sqlite3.connect('bot_database.db')
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
    # 🔥 НОВОЕ: Записываем пользователя, как только он нажал старт
    log_user(message.from_user.id)
    
    args = message.text.split()
    if len(args) > 1:
        code = args[1]
        data = get_file_from_db(code)
        
        if data:
            file_id, file_type, caption = data
            try:
                if file_type == 'document':
                    bot.send_document(message.chat.id, file_id, caption=caption)
                elif file_type == 'video':
                    bot.send_video(message.chat.id, file_id, caption=caption)
                elif file_type == 'photo':
                    bot.send_photo(message.chat.id, file_id, caption=caption)
                elif file_type == 'audio':
                    bot.send_audio(message.chat.id, file_id, caption=caption)
                
                # 🔥 НОВОЕ: Если файл отправился успешно, увеличиваем счетчик
                increment_download(code)
                
            except Exception as e:
                bot.send_message(message.chat.id, "Ой, что-то пошло не так при отправке файла.")
                print(e)
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
        caption = "🗂 Держи свой файл!\nНе забудь поставить реакцию\nна канал 💙\n@AppVault7"
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

# 🔥 НОВОЕ: Команда статистики
@bot.message_handler(commands=['stats'])
def stats_command(message):
    if message.from_user.id != ADMIN_ID: return
    
    users, top_files = get_bot_stats()
    
    text = f"📊 **Статистика бота:**\n\n"
    text += f"👥 **Всего пользователей:** {users}\n"
    text += f"➖➖➖➖➖➖➖\n"
    text += f"🔥 **Топ-10 скачиваний:**\n"
    
    if not top_files:
        text += "Пока ничего не скачивали."
    else:
        for i, (code, count) in enumerate(top_files, 1):
            text += f"{i}. `{code}` — {count} раз\n"
            
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

bot.infinity_polling()
    
    if file_id:
        # Стандартная подпись
        caption = "🗂 Держи свой файл!\nНе забудь поставить реакцию\nна канал 💙\n@AppVault7"
        if add_file_to_db(code, file_id, file_type, caption):
            link = f"https://t.me/{bot.get_me().username}?start={code}"
            bot.reply_to(message, f"✅ Файл сохранен!\nКод: `{code}`\nСсылка: {link}", parse_mode='Markdown')
        else:
            bot.reply_to(message, "❌ Ошибка записи в базу данных.")
    else:
        bot.reply_to(message, "⚠️ Я не вижу файла в сообщении, на которое ты ответил.")

# Чтобы бот не падал
bot.infinity_polling()

