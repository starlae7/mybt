import telebot
import os
import sqlite3
from dotenv import load_dotenv

# Загружаем переменные
load_dotenv()
TOKEN = os.getenv('BOT_TOKEN')

# --- НАСТРОЙКИ АДМИНА ---
# Вставь сюда свой ID
ADMIN_ID = 000000000 

if not TOKEN:
    print("Ошибка: Токен не найден! Проверь файл .env")
    exit()

bot = telebot.TeleBot(TOKEN)

# --- РАБОТА С БАЗОЙ ДАННЫХ ---
def init_db():
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS files (
            code TEXT PRIMARY KEY,
            file_id TEXT,
            file_type TEXT,
            caption TEXT
        )
    ''')
    conn.commit()
    conn.close()

def add_file_to_db(code, file_id, file_type, caption):
    try:
        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?)', 
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
    """Удаляет конкретный файл по коду"""
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM files WHERE code = ?', (code,))
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted_count > 0

def get_all_files_list():
    """Возвращает список всех кодов файлов"""
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    # rowid - это скрытый номер строки, он показывает порядок добавления
    cursor.execute('SELECT code FROM files ORDER BY rowid DESC') 
    result = cursor.fetchall()
    conn.close()
    return result

def delete_oldest_files_db(limit=5):
    """Удаляет N самых старых файлов"""
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    # Сначала узнаем, КОГО мы удалим (чтобы показать админу)
    cursor.execute(f'SELECT code FROM files ORDER BY rowid ASC LIMIT ?', (limit,))
    files_to_delete = cursor.fetchall()
    
    deleted_codes = []
    for row in files_to_delete:
        code = row[0]
        cursor.execute('DELETE FROM files WHERE code = ?', (code,))
        deleted_codes.append(code)
        
    conn.commit()
    conn.close()
    return deleted_codes

# Инициализируем БД
init_db()

# --- КОМАНДЫ ПОЛЬЗОВАТЕЛЯ ---

@bot.message_handler(commands=['start'])
def start_message(message):
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
    
    file_id = None
    file_type = None

    if target_msg.document:
        file_id = target_msg.document.file_id; file_type = 'document'
    elif target_msg.video:
        file_id = target_msg.video.file_id; file_type = 'video'
    elif target_msg.photo:
        file_id = target_msg.photo[-1].file_id; file_type = 'photo'
    elif target_msg.audio:
        file_id = target_msg.audio.file_id; file_type = 'audio'
    
    if file_id:
        caption = "🗂 Держи свой файл!\nНе забудь поставить реакцию на канал 💙\n@AppVault7"
        if add_file_to_db(code, file_id, file_type, caption):
            link = f"https://t.me/{bot.get_me().username}?start={code}"
            bot.reply_to(message, f"✅ Сохранено!\nКод: `{code}`\nСсылка: {link}", parse_mode='Markdown')
        else:
            bot.reply_to(message, "❌ Ошибка БД.")
    else:
        bot.reply_to(message, "⚠️ Файл не найден в сообщении.")

# НОВОЕ: Удаление конкретного файла
@bot.message_handler(commands=['delete'])
def delete_command(message):
    if message.from_user.id != ADMIN_ID: return
    
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "⚠️ Укажи код для удаления. Пример: `/delete minecraft`", parse_mode='Markdown')
        return
        
    code = args[1]
    if delete_file_from_db(code):
        bot.reply_to(message, f"🗑 Файл `{code}` успешно удален!", parse_mode='Markdown')
    else:
        bot.reply_to(message, f"❌ Файл с кодом `{code}` не найден.", parse_mode='Markdown')

# НОВОЕ: Список всех файлов
@bot.message_handler(commands=['all'])
def all_files_command(message):
    if message.from_user.id != ADMIN_ID: return
    
    files = get_all_files_list()
    if not files:
        bot.reply_to(message, "📂 База данных пуста.")
        return
        
    # Собираем список в красивое сообщение
    text = "📂 **Список файлов (от новых к старым):**\n\n"
    for row in files:
        text += f"🔹 `{row[0]}`\n"
        
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

# НОВОЕ: Очистка старых файлов (то, что ты просил)
@bot.message_handler(commands=['cleanup'])
def cleanup_command(message):
    if message.from_user.id != ADMIN_ID: return
    
    # По умолчанию удаляем 5, но можно написать /cleanup 10
    args = message.text.split()
    limit = 5
    if len(args) > 1 and args[1].isdigit():
        limit = int(args[1])
        
    deleted = delete_oldest_files_db(limit)
    
    if deleted:
        text = f"🧹 **Удалено старых файлов: {len(deleted)}**\n\nСписок удаленных кодов:\n" + ", ".join(deleted)
        bot.reply_to(message, text)
    else:
        bot.reply_to(message, "🤷‍♂️ База пуста или файлов не найдено.")

bot.infinity_polling()
