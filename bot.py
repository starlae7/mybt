import telebot
import os
import sqlite3
from dotenv import load_dotenv

# Загружаем переменные и токен
load_dotenv()
TOKEN = os.getenv('BOT_TOKEN')

# --- НАСТРОЙКИ АДМИНА ---
# Впиши сюда свой ID (числовой), чтобы только ты мог добавлять файлы.
# Узнать ID можно у бота @getmyid_bot
ADMIN_ID = 1014329713  # <--- ЗАМЕНИ ЭТО НА СВОЙ ЦИФРОВОЙ ID

if not TOKEN:
    print("Ошибка: Токен не найден! Проверь файл .env")
    exit()

bot = telebot.TeleBot(TOKEN)

# --- РАБОТА С БАЗОЙ ДАННЫХ ---
def init_db():
    """Создает таблицу, если её нет"""
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
    """Добавляет файл в БД"""
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
    """Достает файл по коду"""
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT file_id, file_type, caption FROM files WHERE code = ?', (code,))
    result = cursor.fetchone()
    conn.close()
    return result

# Инициализируем БД при запуске
init_db()

# --- КОМАНДЫ БОТА ---

@bot.message_handler(commands=['start'])
def start_message(message):
    args = message.text.split()
    
    # Если есть аргумент (код файла)
    if len(args) > 1:
        code = args[1]
        data = get_file_from_db(code)
        
        if data:
            file_id, file_type, caption = data
            try:
                # Отправляем файл в зависимости от типа
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
        # Приветствие
        bot.send_message(message.chat.id, 
                         'Привет! Я бот для скачивания файлов. Переходи по ссылкам из канала <a href="https://t.me/AppVault7">AppVault</a>.', parse_mode='HTML')

# --- АДМИНКА (ДОБАВЛЕНИЕ ФАЙЛОВ) ---
# Как пользоваться:
# 1. Отправь боту файл (видео, документ, фото).
# 2. Ответь (Reply) на этот файл командой: /save слово
# Пример: /save minecraft

@bot.message_handler(commands=['save'])
def save_file_command(message):
    # Проверка на админа
    if message.from_user.id != ADMIN_ID:
        return # Игнорируем чужаков

    # Проверяем, что команда написана в ответ на сообщение с файлом
    if not message.reply_to_message:
        bot.reply_to(message, "ℹ️ Отправь мне файл, а потом сделай **Reply** (ответить) на него с командой `/save код`", parse_mode='Markdown')
        return

    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "⚠️ Укажи код для сохранения. Пример: `/save minecraft`", parse_mode='Markdown')
        return

    code = args[1].lower() # Код ссылки (например, minecraft)
    target_msg = message.reply_to_message
    
    file_id = None
    file_type = None

    # Определяем тип файла
    if target_msg.document:
        file_id = target_msg.document.file_id
        file_type = 'document'
    elif target_msg.video:
        file_id = target_msg.video.file_id
        file_type = 'video'
    elif target_msg.photo:
        file_id = target_msg.photo[-1].file_id # Берем лучшее качество
        file_type = 'photo'
    elif target_msg.audio:
        file_id = target_msg.audio.file_id
        file_type = 'audio'
    
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
