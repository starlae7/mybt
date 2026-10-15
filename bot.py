import telebot
import os
import sqlite3
import shutil
import time
import threading
import uuid
import json
import logging  # 🔥 Импортируем библиотеку для логирования
import re
import requests
import html  # 🔥 Импортируем библиотеку для безопасного экранирования спецсимволов HTML
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor  # 🔥 Для пула потоков
from telebot.apihelper import (
    ApiTelegramException,
)  # 🔥 Для перехвата ошибок API Telegram

# 🔥 Импортируем InputMedia для создания красивых альбомов
from telebot.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaAudio,
)

# 🔥 ИМПОРТЫ ДЛЯ WEBHOOK 🔥
from flask import Flask, request
from waitress import serve  # 🔥 Импортируем надежный WSGI-сервер

# 🔥 НАСТРОЙКА ЛОГИРОВАНИЯ 🔥
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

# Копируем базу из GitHub-бэкапа только при самом первом запуске
if not os.path.exists("prod_database.db") and os.path.exists("source.db"):
    shutil.copy("source.db", "prod_database.db")
    logging.info("✅ База данных успешно восстановлена из бэкапа!")

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 1014329713))  # <--- ТВОЙ ID (или из .env)

if not TOKEN:
    logging.error(
        "Ошибка: Токен не найден! Проверь файл .env или переменные окружения на хостинге."
    )
    exit()

# 🔥 ОПТИМИЗАЦИЯ: Увеличиваем пул внутренних потоков telebot 🔥
bot = telebot.TeleBot(TOKEN, num_threads=20)
app = Flask(__name__)  # Инициализация веб-сервера Flask

# 🔥 ВАЖНО: Вставь сюда АВТОМАТИЧЕСКИЙ домен, который выдаст тебе хостинг! 🔥
WEBHOOK_HOST = "https://bot-1774689776-9879-starl-ae.bothost.tech"

# Кешируем юзернейм бота, чтобы не делать лишних запросов при каждом /save
BOT_USERNAME = None


def get_bot_username():
    global BOT_USERNAME
    if not BOT_USERNAME:
        BOT_USERNAME = bot.get_me().username
    return BOT_USERNAME


# 🔥 ПУЛ ПОТОКОВ: Ограничиваем количество одновременных тяжелых задач 🔥
task_pool = ThreadPoolExecutor(max_workers=2)

# 🔥 Пул потоков для мгновенной обработки обновлений вебхука 🔥
webhook_pool = ThreadPoolExecutor(max_workers=20)


# 🔥 КЛАСС ДЛЯ ПОДДЕРЖКИ ЦВЕТНЫХ КНОПОК И ЭМОДЗИ (API 9.4+) 🔥
class ModernInlineKeyboardButton(InlineKeyboardButton):
    def __init__(
        self,
        text,
        url=None,
        callback_data=None,
        style=None,
        icon_custom_emoji_id=None,
        **kwargs,
    ):
        super().__init__(text, url=url, callback_data=callback_data, **kwargs)
        self.style = style
        self.icon_custom_emoji_id = (
            str(icon_custom_emoji_id) if icon_custom_emoji_id else None
        )

    def to_dict(self):
        d = super().to_dict()
        if self.style:
            d["style"] = self.style
        if self.icon_custom_emoji_id:
            d["icon_custom_emoji_id"] = self.icon_custom_emoji_id
        return d


def modern_markup_from_json(json_str):
    if not json_str:
        return None
    data = json.loads(json_str) if isinstance(json_str, str) else json_str
    markup = InlineKeyboardMarkup()
    for row in data.get("keyboard", data.get("inline_keyboard", [])):
        new_row = []
        for btn in row:
            new_row.append(ModernInlineKeyboardButton(**btn))
        markup.keyboard.append(new_row)
    return markup


# --- МОДУЛЬ ИЗВЛЕЧЕНИЯ КАСТОМНЫХ/АНИМИРОВАННЫХ ЭМОДЗИ (UTF-16 SAFE) ---


def extract_custom_emojis_map(text, entities):
    """
    Безопасно извлекает кастомные эмодзи с учетом UTF-16 смещений Telegram
    Возвращает словарь {текст_символа: custom_emoji_id}
    """
    if not entities or not text:
        return {}

    mapping = {}
    u16_pos = 0
    for py_pos, char in enumerate(text):
        mapping[u16_pos] = py_pos
        u16_pos += len(char.encode("utf-16-le")) // 2
    mapping[u16_pos] = len(text)

    def get_py_pos(u16):
        if u16 in mapping:
            return mapping[u16]
        for k in sorted(mapping.keys()):
            if k > u16:
                return mapping[k]
        return len(text)

    custom_emojis = {}
    for e in entities:
        e_type = getattr(e, "type", None) or (
            e.get("type") if isinstance(e, dict) else None
        )
        e_offset = (
            getattr(e, "offset", None)
            if getattr(e, "offset", None) is not None
            else (e.get("offset", 0) if isinstance(e, dict) else 0)
        )
        e_length = (
            getattr(e, "length", None)
            if getattr(e, "length", None) is not None
            else (e.get("length", 0) if isinstance(e, dict) else 0)
        )
        e_emoji_id = getattr(e, "custom_emoji_id", None) or (
            e.get("custom_emoji_id") if isinstance(e, dict) else None
        )

        if e_type == "custom_emoji" and e_emoji_id:
            py_start = get_py_pos(e_offset)
            py_end = get_py_pos(e_offset + e_length)
            emo_text = text[py_start:py_end]
            if emo_text:
                custom_emojis[emo_text] = str(e_emoji_id)

    return custom_emojis


def parse_buttons_input(text, entities):
    """
    Универсальный парсер пользовательского ввода кнопок с поддержкой:
    1. Разделителя строк \n и столбцов |
    2. Цветов (primary), (success), (danger)
    3. Анимированных эмодзи (прямая вставка из премиум-стикеров либо тег (emoji:ID))
    """
    if not text:
        return None
    custom_emojis = extract_custom_emojis_map(text, entities)
    markup = InlineKeyboardMarkup()

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        row = []
        for btn in line.split("|"):
            parts = btn.split("-", 1)
            if len(parts) == 2:
                btn_text, btn_url = parts[0].strip(), parts[1].strip()
                btn_style = None
                if btn_text.lower().startswith("(primary)"):
                    btn_style = "primary"
                    btn_text = btn_text[9:].strip()
                elif btn_text.lower().startswith("(danger)"):
                    btn_style = "danger"
                    btn_text = btn_text[8:].strip()
                elif btn_text.lower().startswith("(success)"):
                    btn_style = "success"
                    btn_text = btn_text[9:].strip()

                btn_emoji_id = None
                # Способ 1: Поиск вставленного кастомного эмодзи из текста
                for emo_txt, emo_id in custom_emojis.items():
                    if emo_txt in btn_text:
                        btn_emoji_id = emo_id
                        btn_text = btn_text.replace(emo_txt, "", 1).strip()
                        break

                # Способ 2: Поиск явного тега (emoji:123456789)
                if not btn_emoji_id:
                    emo_match = re.search(r"\(emoji:(\d+)\)", btn_text, re.IGNORECASE)
                    if emo_match:
                        btn_emoji_id = emo_match.group(1)
                        btn_text = btn_text.replace(emo_match.group(0), "").strip()

                row.append(
                    ModernInlineKeyboardButton(
                        text=btn_text,
                        url=btn_url,
                        style=btn_style,
                        icon_custom_emoji_id=btn_emoji_id,
                    )
                )
        if row:
            markup.add(*row)

    return markup if markup.keyboard else None


# --- РАБОТА С БАЗОЙ ДАННЫХ ---
db_local = threading.local()


def get_db_connection():
    if not hasattr(db_local, "conn"):
        db_local.conn = sqlite3.connect(
            "prod_database.db", timeout=20, check_same_thread=False
        )
        db_local.conn.execute("PRAGMA journal_mode=WAL;")
        db_local.conn.execute("PRAGMA synchronous=NORMAL;")
    return db_local.conn


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """CREATE TABLE IF NOT EXISTS files (code TEXT PRIMARY KEY, file_id TEXT, file_type TEXT, caption TEXT, downloads INTEGER DEFAULT 0)"""
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, join_date TEXT, last_active TEXT)"""
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS broadcast_logs (broadcast_id TEXT, user_id INTEGER, message_id INTEGER)"""
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS channels (username TEXT PRIMARY KEY)"""
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS scheduled_posts (id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER, channel TEXT, message_id INTEGER, buttons TEXT, publish_time INTEGER)"""
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS transactions_queue (id INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT, status TEXT DEFAULT 'pending')"""
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS force_sub (channel_id TEXT PRIMARY KEY, channel_url TEXT, channel_name TEXT, button_text TEXT DEFAULT '👉 Подписаться')"""
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"""
    )
    cursor.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('force_sub_status', 'off')"
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS admin_sessions (admin_id INTEGER, session_key TEXT, session_data TEXT, PRIMARY KEY (admin_id, session_key))"""
    )
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS daily_downloads (code TEXT, date TEXT, count INTEGER DEFAULT 0, PRIMARY KEY (code, date))"""
    )

    # 🔥 Нормализация БД для поддержки медиагрупп (Альбомов) 🔥
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS file_attachments (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, file_id TEXT, file_type TEXT, order_index INTEGER)"""
    )

    # 🛠 БЕСШОВНАЯ МИГРАЦИЯ СТАРЫХ ФАЙЛОВ:
    cursor.execute(
        'SELECT code, file_id, file_type FROM files WHERE file_id IS NOT NULL AND file_id != ""'
    )
    old_files = cursor.fetchall()
    if old_files:
        logging.info(
            f"🔄 Запущена миграция базы данных: найдено {len(old_files)} старых файлов. Перенос в file_attachments..."
        )
        for code, f_id, f_type in old_files:
            cursor.execute(
                "INSERT INTO file_attachments (code, file_id, file_type, order_index) VALUES (?, ?, ?, 1)",
                (code, f_id, f_type),
            )
            cursor.execute(
                "UPDATE files SET file_id = NULL, file_type = NULL WHERE code = ?",
                (code,),
            )
        logging.info("✅ Миграция файлов успешно завершена!")

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_status ON transactions_queue(status);"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_publish_time ON scheduled_posts(publish_time);"
    )

    try:
        cursor.execute("ALTER TABLE scheduled_posts ADD COLUMN draft_data TEXT")
    except sqlite3.OperationalError:
        pass

    conn.commit()


# --- ФУНКЦИИ УПРАВЛЕНИЯ СЕССИЯМИ ---
def set_admin_session(admin_id, key, data):
    conn = get_db_connection()
    cursor = conn.cursor()
    if data is None:
        cursor.execute(
            "DELETE FROM admin_sessions WHERE admin_id = ? AND session_key = ?",
            (admin_id, key),
        )
    else:
        cursor.execute(
            "INSERT OR REPLACE INTO admin_sessions (admin_id, session_key, session_data) VALUES (?, ?, ?)",
            (admin_id, key, json.dumps(data)),
        )
    conn.commit()


def get_admin_session(admin_id, key):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT session_data FROM admin_sessions WHERE admin_id = ? AND session_key = ?",
        (admin_id, key),
    )
    res = cursor.fetchone()
    return json.loads(res[0]) if res else None


# --- ФУНКЦИИ НАСТРОЕК И ОП ---
def get_setting(key, default=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    res = cursor.fetchone()
    return res[0] if res else default


def set_setting(key, value):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
    )
    conn.commit()


def get_force_channels():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT channel_id, channel_url, channel_name, button_text FROM force_sub"
    )
    return [
        {"id": row[0], "url": row[1], "name": row[2], "btn_text": row[3]}
        for row in cursor.fetchall()
    ]


def add_force_channel(ch_id, url, name):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO force_sub (channel_id, channel_url, channel_name, button_text) VALUES (?, ?, ?, ?)",
        (ch_id, url, name, "👉 Подписаться"),
    )
    conn.commit()


def remove_force_channel(ch_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM force_sub WHERE channel_id = ?", (ch_id,))
    conn.commit()


def update_force_button(ch_id, text):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE force_sub SET button_text = ? WHERE channel_id = ?", (text, ch_id)
    )
    conn.commit()


# --- ФУНКЦИИ ЮЗЕРОВ И ФАЙЛОВ ---
def log_user(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    date_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        """
        INSERT INTO users (user_id, join_date, last_active) 
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET last_active = excluded.last_active
    """,
        (user_id, date_now, date_now),
    )
    conn.commit()


def increment_download(code):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE files SET downloads = downloads + 1 WHERE code = ?", (code,)
        )
        today = datetime.now().strftime("%Y-%m-%d")
        cursor.execute(
            """
            INSERT INTO daily_downloads (code, date, count) 
            VALUES (?, ?, 1) 
            ON CONFLICT(code, date) DO UPDATE SET count = count + 1
        """,
            (code, today),
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error(f"Ошибка при инкременте скачиваний: {e}", exc_info=True)


def save_post_to_db(code, files_list, caption):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO files (code, caption, downloads) VALUES (?, ?, COALESCE((SELECT downloads FROM files WHERE code = ?), 0))",
            (code, caption, code),
        )
        cursor.execute("DELETE FROM file_attachments WHERE code = ?", (code,))
        for idx, f in enumerate(files_list, 1):
            cursor.execute(
                "INSERT INTO file_attachments (code, file_id, file_type, order_index) VALUES (?, ?, ?, ?)",
                (code, f["id"], f["type"], idx),
            )
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logging.error(f"Ошибка сохранения поста {code} в БД: {e}", exc_info=True)
        return False


def get_file_data(code):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT caption FROM files WHERE code = ?", (code,))
    res_caption = cursor.fetchone()
    if not res_caption:
        return None

    cursor.execute(
        "SELECT file_id, file_type FROM file_attachments WHERE code = ? ORDER BY order_index ASC",
        (code,),
    )
    attachments = cursor.fetchall()
    return {"caption": res_caption[0], "attachments": attachments}


def delete_file_from_db(code):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM file_attachments WHERE code = ?", (code,))
        cursor.execute("DELETE FROM files WHERE code = ?", (code,))
        deleted_count = cursor.rowcount
        conn.commit()
        return deleted_count > 0
    except Exception as e:
        conn.rollback()
        logging.error(f"Ошибка при удалении файлов поста {code}: {e}", exc_info=True)
        return False


def add_channel_db(username):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO channels (username) VALUES (?)", (username,)
        )
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logging.error(f"Ошибка добавления канала {username} в БД: {e}", exc_info=True)
        return False


def get_channels_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT username FROM channels")
    return [row[0] for row in cursor.fetchall()]


def del_channel_db(username):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM channels WHERE username = ?", (username,))
    conn.commit()


init_db()


# --- КЭШ РЕКЛАМЫ ДЛЯ ФАЙЛОВ ---
GLOBAL_AD_BUTTONS = None
GLOBAL_AD_ALBUM_TEXT = "👇 Рекомендуем:"

def load_ad_cache():
    global GLOBAL_AD_BUTTONS, GLOBAL_AD_ALBUM_TEXT
    GLOBAL_AD_BUTTONS = get_setting("file_ad_buttons", None)
    GLOBAL_AD_ALBUM_TEXT = get_setting("file_ad_album_text", "👇 Рекомендуем:")

load_ad_cache()


# --- ФУНКЦИЯ УМНОГО WYSIWYG ПАРСЕРА ---
def parse_entities_to_html(text, entities):
    if not text:
        return ""
    utf16_text = text.encode("utf-16-le")
    insertions = {}

    sorted_entities = sorted(entities or [], key=lambda e: (e.offset, -e.length))

    for ent in sorted_entities:
        start = ent.offset * 2
        end = (ent.offset + ent.length) * 2

        open_tag = ""
        close_tag = ""

        if ent.type == "bold":
            open_tag, close_tag = "<b>", "</b>"
        elif ent.type == "italic":
            open_tag, close_tag = "<i>", "</i>"
        elif ent.type == "text_link":
            open_tag, close_tag = f'<a href="{ent.url}">', "</a>"
        elif ent.type == "custom_emoji":
            open_tag, close_tag = (
                f'<tg-emoji emoji-id="{ent.custom_emoji_id}">',
                "</tg-emoji>",
            )
        elif ent.type == "blockquote":
            open_tag, close_tag = "<blockquote>", "</blockquote>"
        elif ent.type == "spoiler":
            open_tag, close_tag = "<tg-spoiler>", "</tg-spoiler>"
        elif ent.type == "strikethrough":
            open_tag, close_tag = "<s>", "</s>"
        elif ent.type == "underline":
            open_tag, close_tag = "<u>", "</u>"
        elif ent.type == "code":
            open_tag, close_tag = "<code>", "</code>"
        elif ent.type == "pre":
            open_tag, close_tag = "<pre>", "</pre>"
        else:
            continue

        if start not in insertions:
            insertions[start] = {"open": [], "close": []}
        if end not in insertions:
            insertions[end] = {"open": [], "close": []}

        insertions[start]["open"].append(open_tag)
        insertions[end]["close"].insert(0, close_tag)

    result = ""
    current_pos = 0

    for pos in sorted(insertions.keys()):
        if pos > current_pos:
            chunk = utf16_text[current_pos:pos].decode("utf-16-le")
            result += html.escape(chunk)
            current_pos = pos

        for tag in insertions[pos]["close"]:
            result += tag

        for tag in insertions[pos]["open"]:
            result += tag

    if current_pos < len(utf16_text):
        chunk = utf16_text[current_pos:].decode("utf-16-le")
        result += html.escape(chunk)

    return result


# --- ВЫДАЧА ФАЙЛА И ПРОВЕРКА ПОДПИСКИ ---
def send_file_to_user(chat_id, code):
    data = get_file_data(code)

    if data and data["attachments"]:
        attachments = data["attachments"]
        caption = data["caption"]
        
        # Загружаем рекламные кнопки из кэша
        ad_markup = modern_markup_from_json(GLOBAL_AD_BUTTONS)
        
        try:
            if len(attachments) == 1:
                f_id, f_type = attachments[0]
                if f_type == "document":
                    bot.send_document(chat_id, f_id, caption=caption, parse_mode="HTML", reply_markup=ad_markup)
                elif f_type == "video":
                    bot.send_video(chat_id, f_id, caption=caption, parse_mode="HTML", reply_markup=ad_markup)
                elif f_type == "photo":
                    bot.send_photo(chat_id, f_id, caption=caption, parse_mode="HTML", reply_markup=ad_markup)
                elif f_type == "audio":
                    bot.send_audio(chat_id, f_id, caption=caption, parse_mode="HTML", reply_markup=ad_markup)
            else:
                media_group = []
                last_idx = len(attachments) - 1

                for i, (f_id, f_type) in enumerate(attachments):
                    cap = caption if i == last_idx else None
                    pm = "HTML" if i == last_idx else None

                    if f_type == "document":
                        media_group.append(
                            InputMediaDocument(f_id, caption=cap, parse_mode=pm)
                        )
                    elif f_type == "photo":
                        media_group.append(
                            InputMediaPhoto(f_id, caption=cap, parse_mode=pm)
                        )
                    elif f_type == "video":
                        media_group.append(
                            InputMediaVideo(f_id, caption=cap, parse_mode=pm)
                        )
                    elif f_type == "audio":
                        media_group.append(
                            InputMediaAudio(f_id, caption=cap, parse_mode=pm)
                        )

                for i in range(0, len(media_group), 10):
                    chunk = media_group[i : i + 10]
                    is_last_chunk = (i + 10 >= len(media_group))
                    sent_via_direct_api = False

                    if is_last_chunk and ad_markup:
                        # 🚀 ЭКСПЕРИМЕНТАЛЬНЫЙ ТЕСТ: Пытаемся прикрепить кнопку прямо к альбому через API
                        try:
                            media_json = [m.to_dict() for m in chunk]
                            payload = {
                                "chat_id": chat_id,
                                "media": media_json,
                                "reply_markup": ad_markup.to_dict() if hasattr(ad_markup, "to_dict") else ad_markup
                            }
                            url = f"https://api.telegram.org/bot{TOKEN}/sendMediaGroup"
                            resp = requests.post(url, json=payload, timeout=20)
                            if resp.status_code == 200:
                                sent_via_direct_api = True
                        except Exception:
                            pass

                    if not sent_via_direct_api:
                        bot.send_media_group(chat_id, chunk)
                        if is_last_chunk and ad_markup:
                            time.sleep(0.5)
                            bot.send_message(chat_id, GLOBAL_AD_ALBUM_TEXT, parse_mode="HTML", reply_markup=ad_markup)
                    else:
                        time.sleep(0.5)

            increment_download(code)
        except Exception as e:
            logging.error(
                f"Ошибка при отправке файлов {code} пользователю {chat_id}: {e}",
                exc_info=True,
            )
            bot.send_message(
                chat_id,
                "Ой, что-то пошло не так <b>при выдаче файла</b>. Возможно, файлы несовместимы для альбома.",
                parse_mode="HTML",
            )
    else:
        bot.send_message(
            chat_id,
            '<tg-emoji emoji-id="5447644880824181073">⚠️</tg-emoji> <b>Файл не найден</b>. Проверьте ссылку.',
            parse_mode="HTML",
        )


_sub_cache = {}


def check_user_subscription(user_id):
    status = get_setting("force_sub_status", "off")
    if status == "off":
        return True, []

    channels = get_force_channels()
    if not channels:
        return True, []

    current_time = time.time()
    if user_id in _sub_cache and _sub_cache[user_id] > current_time:
        return True, []

    not_subscribed = []
    for ch in channels:
        for attempt in range(2):
            try:
                url = f"https://api.telegram.org/bot{TOKEN}/getChatMember"
                payload = {"chat_id": ch["id"], "user_id": user_id}
                response = requests.post(url, json=payload, timeout=5)
                data = response.json()

                if response.status_code == 200 and data.get("ok"):
                    if data["result"]["status"] not in [
                        "member",
                        "administrator",
                        "creator",
                    ]:
                        not_subscribed.append(ch)
                    break
                elif response.status_code in [400, 403]:
                    error_msg = data.get("description", "Unknown error")
                    if (
                        "participant not found" in error_msg.lower()
                        or "user not found" in error_msg.lower()
                    ):
                        not_subscribed.append(ch)
                    else:
                        logging.error(
                            f"Потеряны права в канале {ch['name']}: {error_msg}"
                        )
                        if attempt == 1:
                            bot.send_message(
                                ADMIN_ID,
                                f"⚠️ <b>Внимание!</b>\nЯ не могу проверить подписку в канале <b>{ch['name']}</b>!\nВозможно, у меня забрали права администратора. Причина: <code>{error_msg}</code>",
                                parse_mode="HTML",
                            )
                    break
                response.raise_for_status()
            except requests.exceptions.Timeout:
                if attempt == 0:
                    time.sleep(0.5)
                else:
                    logging.warning(
                        f"⏳ Telegram API лагает (Таймаут 5с). Проверка {ch['name']} пропущена."
                    )
            except Exception as e:
                if attempt == 0:
                    time.sleep(0.5)
                else:
                    logging.error(
                        f"Системная ошибка проверки подписки {ch['name']}: {e}",
                        exc_info=True,
                    )

    is_subbed = len(not_subscribed) == 0
    if is_subbed:
        _sub_cache[user_id] = current_time + 60

    return is_subbed, channels


def show_subscription_warning(chat_id, code, channels):
    text = '<tg-emoji emoji-id="5447644880824181073">⚠️</tg-emoji> <b>Чтобы получить файл</b>, нужно быть подписанным на канал!'
    markup = InlineKeyboardMarkup(row_width=1)
    for ch in channels:
        markup.add(InlineKeyboardButton(ch["btn_text"], url=ch["url"]))
    cb_data = f"checksub_{code}"
    if len(cb_data) > 64:
        cb_data = cb_data[:64]
    markup.add(InlineKeyboardButton("🔄 Проверить подписку", callback_data=cb_data))
    bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")


@bot.callback_query_handler(func=lambda call: call.data.startswith("checksub_"))
def handle_checksub(call):
    code = call.data.replace("checksub_", "")
    is_subbed, _ = check_user_subscription(call.from_user.id)
    if is_subbed:
        bot.answer_callback_query(call.id, "✅ Спасибо за подписку!")
        bot.delete_message(call.message.chat.id, call.message.message_id)
        send_file_to_user(call.message.chat.id, code)
    else:
        bot.answer_callback_query(
            call.id, "❌ Вы подписались не на все каналы!", show_alert=True
        )


# --- КОМАНДЫ ПОЛЬЗОВАТЕЛЯ ---
@bot.message_handler(commands=["start"])
def start_message(message):
    log_user(message.from_user.id)
    args = message.text.split()
    if len(args) > 1:
        code = args[1]
        is_subbed, channels = check_user_subscription(message.from_user.id)
        if is_subbed:
            send_file_to_user(message.chat.id, code)
        else:
            show_subscription_warning(message.chat.id, code, channels)
    else:
        bot.send_message(
            message.chat.id,
            '<b>Привет!</b> Я бот для скачивания файлов. Переходи по ссылкам из канала <a href="https://t.me/AppVault7">AppVault</a>.',
            parse_mode="HTML",
        )


# 🔥 ГЛОБАЛЬНАЯ ОТМЕНА (СБРОС СОСТОЯНИЙ) 🔥
@bot.message_handler(commands=["cancel"])
@bot.message_handler(func=lambda m: m.text and m.text.lower() == "отмена")
def cmd_cancel(message):
    if message.from_user.id != ADMIN_ID:
        return
    bot.clear_step_handler_by_chat_id(message.chat.id)
    set_admin_session(ADMIN_ID, "save_wizard", None)
    set_admin_session(ADMIN_ID, "post", None)
    set_admin_session(ADMIN_ID, "broadcast", None)
    set_admin_session(ADMIN_ID, "sched_edit", None)
    bot.send_message(
        message.chat.id,
        "✅ <b>Все активные операции отменены.</b> Бот вернулся в базовое состояние.",
        parse_mode="HTML",
    )


# --- МАСТЕР СОХРАНЕНИЯ КОМАНДЫ /save ---
@bot.message_handler(commands=["save"])
def save_wizard_start(message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split()
    if len(args) < 2:
        return bot.reply_to(
            message,
            "⚠️ <b>Укажи код для сохранения.</b>\nПример: <code>/save gta5</code>\n<i>(Делать ответ/Reply на файл больше не нужно!)</i>",
            parse_mode="HTML",
        )

    code = args[1].lower()
    set_admin_session(
        ADMIN_ID, "save_wizard", {"code": code, "files": [], "step": "collecting"}
    )

    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("✅ Все файлы загружены", callback_data="save_wizard_done")
    )
    markup.add(InlineKeyboardButton("❌ Отменить", callback_data="save_wizard_cancel"))

    text = (
        f"📥 <b>Режим сохранения для:</b> <code>{code}</code>\n\n"
        f"Отправляй мне нужные файлы (документы, фото, видео). "
        f"Можно отправлять по одному или выделить сразу несколько как Альбом.\n\n"
        f"<i>Я буду молча складывать их в корзину. Как загрузишь всё, жми кнопку!</i>"
    )
    bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")


@bot.message_handler(content_types=["document", "photo", "video", "audio"])
def handle_admin_media(message):
    if message.from_user.id != ADMIN_ID:
        return
    if message.document and message.document.file_name == "prod_database.db":
        return restore_database(message)

    draft = get_admin_session(ADMIN_ID, "save_wizard")
    if draft and draft.get("step") == "collecting":
        f_id, f_type = None, None
        if message.document:
            f_id, f_type = message.document.file_id, "document"
        elif message.video:
            f_id, f_type = message.video.file_id, "video"
        elif message.photo:
            f_id, f_type = message.photo[-1].file_id, "photo"
        elif message.audio:
            f_id, f_type = message.audio.file_id, "audio"

        if f_id:
            draft["files"].append({"id": f_id, "type": f_type})
            set_admin_session(ADMIN_ID, "save_wizard", draft)
        return


@bot.callback_query_handler(
    func=lambda call: call.data
    in ["save_wizard_done", "save_cap_std", "save_cap_custom", "save_wizard_cancel"]
)
def save_wizard_callback(call):
    if call.from_user.id != ADMIN_ID:
        return
    draft = get_admin_session(ADMIN_ID, "save_wizard")
    if not draft:
        return bot.answer_callback_query(call.id, "Сессия устарела. Начни заново /save")
    bot.answer_callback_query(call.id)

    if call.data == "save_wizard_cancel":
        set_admin_session(ADMIN_ID, "save_wizard", None)
        bot.edit_message_text(
            "❌ Сохранение отменено.", call.message.chat.id, call.message.message_id
        )
    elif call.data == "save_wizard_done":
        if len(draft["files"]) == 0:
            return bot.send_message(
                call.message.chat.id,
                "⚠️ Ты не отправил ни одного файла! Сначала отправь файлы, затем жми кнопку.",
            )

        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("💫 Стандартное", callback_data="save_cap_std"))
        markup.add(
            InlineKeyboardButton("✍️ Написать своё", callback_data="save_cap_custom")
        )
        text = f"📝 Файлов получено: <b>{len(draft['files'])}</b>\n\nКакое описание будем использовать при выдаче?"
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=markup,
            parse_mode="HTML",
        )
    elif call.data == "save_cap_std":
        caption = '<tg-emoji emoji-id="5469744063815102906">💫</tg-emoji> Держи свой файл!\nНе забудь поставить реакцию на канал <tg-emoji emoji-id="5461117441612462242">🙂</tg-emoji>\n<a href="https://t.me/AppVault7">наш канал</a> <tg-emoji emoji-id="5220079633533250496">👈</tg-emoji>\n<a href="https://t.me/+B_pehHS2vnIxNDBi">наш чат</a> <tg-emoji emoji-id="5220079633533250496">👈</tg-emoji>'
        finish_saving_wizard(call.message, draft, caption)
    elif call.data == "save_cap_custom":
        draft["step"] = "custom_caption"
        set_admin_session(ADMIN_ID, "save_wizard", draft)
        std_text = '<tg-emoji emoji-id="5469744063815102906">💫</tg-emoji> Держи свой файл!\nНе забудь поставить реакцию на канал <tg-emoji emoji-id="5461117441612462242">🙂</tg-emoji>\n<a href="https://t.me/AppVault7">наш канал</a> <tg-emoji emoji-id="5220079633533250496">👈</tg-emoji>\n<a href="https://t.me/+B_pehHS2vnIxNDBi">наш чат</a> <tg-emoji emoji-id="5220079633533250496">👈</tg-emoji>'
        bot.edit_message_text(
            "✍️ <b>Режим своего описания.</b>\nСкопируй базовый текст ниже, измени его (можешь добавлять свои премиум-стикеры) и отправь мне обратно:",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML",
        )
        bot.send_message(call.message.chat.id, std_text, parse_mode="HTML")


@bot.message_handler(
    func=lambda m: m.from_user.id == ADMIN_ID
    and get_admin_session(ADMIN_ID, "save_wizard")
    and get_admin_session(ADMIN_ID, "save_wizard").get("step") == "custom_caption"
)
def handle_custom_caption(message):
    draft = get_admin_session(ADMIN_ID, "save_wizard")
    html_text = parse_entities_to_html(message.text, message.entities)
    finish_saving_wizard(message, draft, html_text)


def finish_saving_wizard(message, draft, html_caption):
    if save_post_to_db(draft["code"], draft["files"], html_caption):
        bot_username = get_bot_username()
        link = f"https://t.me/{bot_username}?start={draft['code']}"
        bot.send_message(
            message.chat.id,
            f"✅ <b>Файл(ы) успешно сохранены!</b>\n\n🆔 Код: <code>{draft['code']}</code>\n🔗 Ссылка для скачивания:\n{link}",
            parse_mode="HTML",
        )
    else:
        bot.send_message(message.chat.id, "❌ Ошибка базы данных при сохранении.")
    set_admin_session(ADMIN_ID, "save_wizard", None)


# --- ПРОЧИЕ КОМАНДЫ АДМИНА ---
@bot.message_handler(commands=["delete"])
def delete_command(message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split()
    if len(args) < 2:
        return bot.reply_to(
            message,
            "⚠️ Укажи код файла для удаления.\nПример: <code>/delete minecraft</code>",
            parse_mode="HTML",
        )
    code = args[1].lower()
    if delete_file_from_db(code):
        bot.reply_to(
            message,
            f"🗑 Пост с кодом <code>{code}</code> и все его файлы успешно удалены!",
            parse_mode="HTML",
        )
    else:
        bot.reply_to(message, "❌ Файлы с таким кодом не найдены.")


# --- 🔥 УПРАВЛЕНИЕ РЕКЛАМОЙ ПОД ФАЙЛАМИ (/ads) 🔥 ---
@bot.message_handler(commands=["ads"])
def cmd_ads(message):
    if message.from_user.id != ADMIN_ID:
        return
    show_ads_menu(message.chat.id)

def show_ads_menu(chat_id, message_id=None):
    text = (
        "📊 <b>Управление рекламой под файлами</b>\n\n"
        "<i>Текст для альбомов:</i>\n"
        f"{GLOBAL_AD_ALBUM_TEXT}\n\n"
        "<i>Кнопки (появляются под одиночными файлами и под текстом для альбомов):</i>"
    )
    
    markup = modern_markup_from_json(GLOBAL_AD_BUTTONS) or InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("— УПРАВЛЕНИЕ —", callback_data="ignore"))
    markup.add(
        InlineKeyboardButton("⚙️ Изменить кнопки", callback_data="ads_edit"),
        InlineKeyboardButton("📝 Изменить текст", callback_data="ads_text")
    )
    markup.add(
        InlineKeyboardButton("🗑 Удалить рекламу", callback_data="ads_del"),
        InlineKeyboardButton("❌ Закрыть", callback_data="ads_close")
    )
    
    if message_id:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode="HTML")
    else:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith("ads_"))
def ads_callback(call):
    if call.from_user.id != ADMIN_ID:
        return
    action = call.data
    if action == "ads_close":
        bot.delete_message(call.message.chat.id, call.message.message_id)
    elif action == "ads_del":
        set_setting("file_ad_buttons", None)
        load_ad_cache()
        bot.answer_callback_query(call.id, "✅ Реклама удалена")
        show_ads_menu(call.message.chat.id, call.message.message_id)
    elif action == "ads_edit":
        set_admin_session(ADMIN_ID, "ads_setup", "buttons")
        msg = bot.send_message(
            call.message.chat.id, 
            "Отправь кнопки в формате:\n<code>(success) ✔️ СКАЧАТЬ VPN - https://...</code>\n\n❌ Отправь 0 для отмены.", 
            parse_mode="HTML"
        )
        bot.register_next_step_handler(msg, process_ads_buttons)
    elif action == "ads_text":
        set_admin_session(ADMIN_ID, "ads_setup", "text")
        msg = bot.send_message(
            call.message.chat.id, 
            "Отправь текст для альбомов (можно с форматированием и эмодзи):\n\n❌ Отправь 0 для отмены.", 
            parse_mode="HTML"
        )
        bot.register_next_step_handler(msg, process_ads_text)

def process_ads_buttons(message):
    if message.text.strip() == "0":
        set_admin_session(ADMIN_ID, "ads_setup", None)
        return show_ads_menu(message.chat.id)
    
    try:
        markup = parse_buttons_input(message.text, message.entities)
        if markup:
            set_setting("file_ad_buttons", markup.to_json())
            load_ad_cache()
            bot.send_message(message.chat.id, "✅ Кнопки сохранены!")
        else:
            bot.send_message(message.chat.id, "❌ Кнопки не найдены, проверь синтаксис.")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка: {e}")
    
    set_admin_session(ADMIN_ID, "ads_setup", None)
    show_ads_menu(message.chat.id)

def process_ads_text(message):
    if message.text.strip() == "0":
        set_admin_session(ADMIN_ID, "ads_setup", None)
        return show_ads_menu(message.chat.id)
    
    try:
        html_text = parse_entities_to_html(message.text, message.entities)
        set_setting("file_ad_album_text", html_text)
        load_ad_cache()
        bot.send_message(message.chat.id, "✅ Текст сохранен!")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка: {e}")
        
    set_admin_session(ADMIN_ID, "ads_setup", None)
    show_ads_menu(message.chat.id)


# 🔥 ОБЯЗАТЕЛЬНАЯ ПОДПИСКА 🔥
@bot.message_handler(commands=["sub"])
def cmd_sub(message):
    if message.from_user.id != ADMIN_ID:
        return
    show_sub_menu(message.chat.id)


def show_sub_menu(chat_id, message_id=None):
    status = get_setting("force_sub_status", "off")
    channels = get_force_channels()

    text = f"🛡 <b>Настройки обязательной подписки</b>\n"
    text += f"Статус: {'🟢 ВКЛЮЧЕНО' if status == 'on' else '🔴 ВЫКЛЮЧЕНО'}\n\n"
    if channels:
        text += "<b>Подключенные каналы:</b>\n"
        for i, ch in enumerate(channels, 1):
            text += f"{i}. <b>{ch['name']}</b> (Текст: <i>\"{ch['btn_text']}\"</i>)\n"
    else:
        text += "Подключенных каналов нет.\n"

    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton(
            "🔴 Выключить ОП" if status == "on" else "🟢 Включить ОП",
            callback_data="sub_toggle",
        )
    )
    markup.add(
        InlineKeyboardButton("➕ Добавить канал", callback_data="sub_add"),
        InlineKeyboardButton("➖ Удалить канал", callback_data="sub_del"),
    )
    markup.add(
        InlineKeyboardButton("📝 Изменить текст кнопки", callback_data="sub_edit_btn")
    )

    if message_id:
        bot.edit_message_text(
            text, chat_id, message_id, reply_markup=markup, parse_mode="HTML"
        )
    else:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("sub_")
    or call.data.startswith("setbtn_")
    or call.data.startswith("delch_")
)
def sub_menu_callback(call):
    admin_id = call.from_user.id
    if admin_id != ADMIN_ID:
        return

    if call.data == "sub_toggle":
        current = get_setting("force_sub_status", "off")
        new_status = "off" if current == "on" else "on"
        set_setting("force_sub_status", new_status)
        show_sub_menu(call.message.chat.id, call.message.message_id)
    elif call.data == "sub_cancel":
        show_sub_menu(call.message.chat.id, call.message.message_id)
    elif call.data == "sub_add":
        msg = bot.edit_message_text(
            "➕ <b>Добавление канала</b>\n\nПерешли мне ЛЮБОЕ сообщение из нужного канала.\n<i>(Либо отправь его ID, если знаешь, например: -100123456)</i>\n\n❌ Отправь <code>0</code> для отмены.",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML",
        )
        bot.register_next_step_handler(msg, process_sub_add_id)
    elif call.data == "sub_del":
        channels = get_force_channels()
        if not channels:
            return bot.answer_callback_query(
                call.id, "❌ Нет добавленных каналов!", show_alert=True
            )
        markup = InlineKeyboardMarkup()
        for ch in channels:
            markup.add(
                InlineKeyboardButton(
                    f"❌ {ch['name']}", callback_data=f"delch_{ch['id']}"
                )
            )
        markup.add(InlineKeyboardButton("🔙 Назад", callback_data="sub_cancel"))
        bot.edit_message_text(
            "➖ <b>Удаление канала</b>\nВыбери канал, который нужно отвязать:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=markup,
            parse_mode="HTML",
        )
    elif call.data.startswith("delch_"):
        ch_id = call.data.replace("delch_", "")
        remove_force_channel(ch_id)
        bot.answer_callback_query(call.id, "✅ Канал удален!")
        show_sub_menu(call.message.chat.id, call.message.message_id)
    elif call.data == "sub_edit_btn":
        channels = get_force_channels()
        if not channels:
            return bot.answer_callback_query(
                call.id, "❌ Нет добавленных каналов!", show_alert=True
            )
        markup = InlineKeyboardMarkup()
        for ch in channels:
            markup.add(
                InlineKeyboardButton(ch["name"], callback_data=f"setbtn_{ch['id']}")
            )
        markup.add(InlineKeyboardButton("🔙 Назад", callback_data="sub_cancel"))
        bot.edit_message_text(
            "📝 <b>Изменение текста кнопки</b>\nВыбери канал:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=markup,
            parse_mode="HTML",
        )
    elif call.data.startswith("setbtn_"):
        ch_id = call.data.replace("setbtn_", "")
        msg = bot.edit_message_text(
            "Отправь новый текст для кнопки (например: <code>🎁 Забрать подарок</code>)\nПоддерживаются эмодзи.\n\n❌ Отправь <code>0</code> для отмены.",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML",
        )
        bot.register_next_step_handler(msg, process_set_btn_text, ch_id)


def process_sub_add_id(message):
    if message.text and message.text.strip() == "0":
        return show_sub_menu(message.chat.id)
    chat_id = None
    channel_name = "Неизвестный канал"
    if message.forward_from_chat:
        chat_id = message.forward_from_chat.id
        channel_name = message.forward_from_chat.title
    elif message.text:
        chat_id = message.text.strip()
        try:
            chat_info = bot.get_chat(chat_id)
            chat_id = chat_info.id
            channel_name = chat_info.title
        except Exception:
            pass

    if not chat_id:
        msg = bot.send_message(
            message.chat.id,
            "❌ Не удалось определить канал. Перешли сообщение или отправь ID (или 0 для отмены):",
        )
        return bot.register_next_step_handler(msg, process_sub_add_id)

    msg = bot.send_message(
        message.chat.id,
        f"✅ Канал определён: <b>{channel_name}</b>\n\n🔗 Теперь отправь <b>ссылку</b> на этот канал (её будут нажимать пользователи для подписки).\n❌ Отправь <code>0</code> для отмены.",
        parse_mode="HTML",
    )
    bot.register_next_step_handler(msg, process_sub_add_url, chat_id, channel_name)


def process_sub_add_url(message, chat_id, channel_name):
    url = message.text.strip()
    if url == "0":
        return show_sub_menu(message.chat.id)
    if not url.startswith("http") and not url.startswith("@"):
        msg = bot.send_message(
            message.chat.id,
            "❌ Ссылка должна начинаться с http или @. Попробуй еще раз (или 0 для отмены):",
        )
        return bot.register_next_step_handler(
            msg, process_sub_add_url, chat_id, channel_name
        )

    add_force_channel(str(chat_id), url, channel_name)
    bot.send_message(
        message.chat.id,
        "✅ Канал успешно добавлен для обязательной подписки!\n\n⚠️ <b>ВАЖНО:</b> Не забудь добавить бота в этот канал как администратора!",
    )
    show_sub_menu(message.chat.id)


def process_set_btn_text(message, ch_id):
    if message.text.strip() == "0":
        return show_sub_menu(message.chat.id)
    update_force_button(ch_id, message.text)
    bot.send_message(message.chat.id, "✅ Текст кнопки успешно обновлен!")
    show_sub_menu(message.chat.id)


# --- 🔥 НОВЫЙ БЛОК: РАССЫЛКА С ПОДДЕРЖКОЙ КНОПОК И ЭМОДЗИ 🔥 ---


def show_broadcast_menu(admin_id, chat_id, message_id=None):
    draft = get_admin_session(admin_id, "broadcast")
    if not draft:
        return

    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("🔗 URL-кнопки (с иконками)", callback_data="bc_add_btns")
    )
    markup.add(InlineKeyboardButton("👁 Предпросмотр", callback_data="bc_preview"))
    markup.add(InlineKeyboardButton("✅ Отправить всем", callback_data="bc_all"))
    markup.add(InlineKeyboardButton("🔢 Выбрать количество", callback_data="bc_limit"))
    markup.add(InlineKeyboardButton("❌ Отмена", callback_data="bc_cancel"))

    btns_count = 0
    if draft.get("buttons"):
        try:
            b_data = json.loads(draft["buttons"])
            btns_count = sum(
                len(row)
                for row in b_data.get("keyboard", b_data.get("inline_keyboard", []))
            )
        except Exception:
            pass

    text = (
        f"📢 <b>Панель управления рассылкой:</b>\n"
        f"Кнопок настроено: <b>{btns_count} шт.</b>\n\n"
        f"Вы можете прикрепить цветные кнопки с анимированными стикерами, посмотреть предпросмотр или начать рассылку."
    )
    if message_id:
        bot.edit_message_text(
            text, chat_id, message_id, reply_markup=markup, parse_mode="HTML"
        )
    else:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")


@bot.message_handler(commands=["broadcast"])
def cmd_broadcast(message):
    if message.from_user.id != ADMIN_ID:
        return
    msg = bot.send_message(
        message.chat.id,
        "📢 <b>Создание рассылки</b>\n\nОтправь мне сообщение (текст, фото, видео, кружочек, медиагруппу), которое нужно разослать.",
        parse_mode="HTML",
    )
    bot.register_next_step_handler(msg, process_broadcast_content)


def process_broadcast_content(message):
    set_admin_session(
        message.from_user.id,
        "broadcast",
        {"message_id": message.message_id, "buttons": None},
    )
    bot.send_message(message.chat.id, "✅ Сообщение для рассылки успешно сохранено!")
    show_broadcast_menu(message.from_user.id, message.chat.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("bc_"))
def broadcast_callback(call):
    admin_id = call.from_user.id
    if admin_id != ADMIN_ID:
        return
    bot.answer_callback_query(call.id)
    action = call.data
    draft = get_admin_session(admin_id, "broadcast")
    if not draft and action != "bc_cancel":
        return bot.send_message(
            call.message.chat.id,
            "❌ Сессия рассылки устарела. Начни заново /broadcast.",
        )

    if action == "bc_cancel":
        bot.edit_message_text(
            "❌ Рассылка отменена.", call.message.chat.id, call.message.message_id
        )
        set_admin_session(admin_id, "broadcast", None)

    elif action == "bc_add_btns":
        text = (
            "🛠 <b>Настройка URL-кнопок для рассылки</b>\n\n"
            "Отправь список кнопок в формате:\n"
            "<code>Кнопка 1 - https://t.me/durov</code>\n\n"
            "💡 <b>Полезные фишки:</b>\n"
            "1️⃣ <b>Кнопки в ряд:</b> используй разделитель <code>|</code>\n"
            "<code>Кнопка 1 - http://... | Кнопка 2 - http://...</code>\n\n"
            "2️⃣ <b>Цветные кнопки:</b> добавь тег перед текстом\n"
            "🔵 <code>(primary)</code> — синяя (основная)\n"
            "🟢 <code>(success)</code> — зеленая (скачать/успех)\n"
            "🔴 <code>(danger)</code> — красная (удалить/отмена)\n\n"
            "3️⃣ <b>Анимированные стикеры/эмодзи:</b> просто вставь премиум-эмодзи перед текстом кнопки!\n"
            "<i>Пример:</i> <code>(success) 💎 Скачать - https://... | 💬 Чат - https://...</code>\n\n"
            "❌ Напиши <code>0</code> (ноль), чтобы удалить кнопки."
        )
        msg = bot.send_message(call.message.chat.id, text, parse_mode="HTML")
        bot.register_next_step_handler(msg, process_broadcast_buttons)

    elif action == "bc_preview":
        markup = (
            modern_markup_from_json(draft.get("buttons"))
            if draft.get("buttons")
            else None
        )
        bot.send_message(
            call.message.chat.id,
            "👀 <b>Предпросмотр сообщения рассылки:</b>",
            parse_mode="HTML",
        )
        try:
            bot.copy_message(
                call.message.chat.id, admin_id, draft["message_id"], reply_markup=markup
            )
        except Exception as e:
            bot.send_message(call.message.chat.id, f"❌ Ошибка предпросмотра: {e}")
        show_broadcast_menu(admin_id, call.message.chat.id)

    elif action == "bc_all":
        bot.edit_message_text(
            "🚀 Начинаю рассылку всем пользователям...",
            call.message.chat.id,
            call.message.message_id,
        )
        task_pool.submit(
            run_broadcast, admin_id, draft["message_id"], None, draft.get("buttons")
        )
        set_admin_session(admin_id, "broadcast", None)

    elif action == "bc_limit":
        msg = bot.edit_message_text(
            "Напиши число пользователей, которым нужно отправить сообщение:",
            call.message.chat.id,
            call.message.message_id,
        )
        bot.register_next_step_handler(msg, process_broadcast_limit)


def process_broadcast_buttons(message):
    admin_id = message.from_user.id
    draft = get_admin_session(admin_id, "broadcast")
    if not draft:
        return

    if message.text.lower().strip() in ["0", "отмена"]:
        draft["buttons"] = None
        set_admin_session(admin_id, "broadcast", draft)
        bot.send_message(message.chat.id, "🗑 Кнопки рассылки успешно удалены.")
        show_broadcast_menu(admin_id, message.chat.id)
        return

    try:
        markup = parse_buttons_input(message.text, message.entities)
        draft["buttons"] = markup.to_json() if markup else None
        set_admin_session(admin_id, "broadcast", draft)
        bot.send_message(
            message.chat.id,
            "✅ Кнопки с анимированными стикерами успешно привязаны к рассылке!",
        )
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка форматирования кнопок: {e}")
    show_broadcast_menu(admin_id, message.chat.id)


def process_broadcast_limit(message):
    admin_id = message.from_user.id
    draft = get_admin_session(admin_id, "broadcast")
    if not draft:
        return

    if not message.text.isdigit():
        return bot.send_message(message.chat.id, "❌ Это не число. Рассылка отменена.")
    limit = int(message.text)
    bot.send_message(
        message.chat.id, f"🚀 Начинаю рассылку для {limit} пользователей..."
    )
    task_pool.submit(
        run_broadcast, admin_id, draft["message_id"], limit, draft.get("buttons")
    )
    set_admin_session(admin_id, "broadcast", None)


def run_broadcast(admin_id, message_id_to_copy, limit, buttons_json=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    if limit:
        cursor.execute("SELECT user_id FROM users LIMIT ?", (limit,))
    else:
        cursor.execute("SELECT user_id FROM users")
    broadcast_id = str(uuid.uuid4())[:8]

    success = 0
    blocked = 0
    processed_in_batch = 0
    markup = modern_markup_from_json(buttons_json) if buttons_json else None

    if True:
        users = cursor.fetchall()


        for user in users:
            user_id = user[0]
            retry_count = 0
            while retry_count < 5:
                try:
                    sent_msg = bot.copy_message(
                        user_id, admin_id, message_id_to_copy, reply_markup=markup
                    )
                    cursor.execute(
                        "INSERT INTO broadcast_logs (broadcast_id, user_id, message_id) VALUES (?, ?, ?)",
                        (broadcast_id, user_id, sent_msg.message_id),
                    )
                    conn.commit()  # 🔥 МГНОВЕННО ОТПУСКАЕМ БАЗУ ДАННЫХ
                    success += 1
                    break
                except ApiTelegramException as e:
                    if e.error_code == 429:
                        retry_after = 3
                        if (
                            e.result_json
                            and "parameters" in e.result_json
                            and "retry_after" in e.result_json["parameters"]
                        ):
                            retry_after = e.result_json["parameters"]["retry_after"]
                        logging.warning(
                            f"⚠️ Лимит рассылки! Засыпаем на {retry_after} секунд..."
                        )
                        time.sleep(retry_after)
                        retry_count += 1
                        continue
                    elif e.error_code in [400, 403]:
                        blocked += 1
                        break
                    else:
                        blocked += 1
                        break
                except Exception:
                    blocked += 1
                    break

            time.sleep(0.05)

    conn.commit()
    report = (
        f"✅ <b>Рассылка завершена!</b>\nУспешно: {success}\nЗаблокировали бота: {blocked}\n\n"
        f"🗑 Чтобы удалить это сообщение у всех, напиши:\n<code>/delcast {broadcast_id}</code>"
    )
    bot.send_message(admin_id, report, parse_mode="HTML")


@bot.message_handler(commands=["delcast"])
def cmd_delete_broadcast(message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split()
    if len(args) < 2:
        return bot.reply_to(
            message,
            "⚠️ Укажи ID рассылки для удаления.\nПример: <code>/delcast a1b2c3d4</code>",
            parse_mode="HTML",
        )
    broadcast_id = args[1]
    bot.reply_to(
        message,
        f"⏳ Начинаю удаление сообщений рассылки <code>{broadcast_id}</code>...",
        parse_mode="HTML",
    )
    task_pool.submit(run_delete_broadcast, message.from_user.id, broadcast_id)


def run_delete_broadcast(admin_id, broadcast_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT user_id, message_id FROM broadcast_logs WHERE broadcast_id = ?",
        (broadcast_id,),
    )

    deleted = 0
    first_error = None
    total_logs = 0

    if True:
        logs = cursor.fetchall()

        total_logs += len(logs)
        for log in logs:
            user_id, msg_id = log
            retry_count = 0
            while retry_count < 5:
                try:
                    bot.delete_message(chat_id=int(user_id), message_id=int(msg_id))
                    deleted += 1
                    break
                except ApiTelegramException as e:
                    if e.error_code == 429:
                        retry_after = 3
                        if (
                            e.result_json
                            and "parameters" in e.result_json
                            and "retry_after" in e.result_json["parameters"]
                        ):
                            retry_after = e.result_json["parameters"]["retry_after"]
                        time.sleep(retry_after)
                        retry_count += 1
                        continue
                    elif "message to delete not found" in e.description.lower():
                        deleted += 1
                        break
                    else:
                        if not first_error:
                            first_error = str(e)
                        break
                except Exception as e:
                    if not first_error:
                        first_error = str(e)
                    break
            time.sleep(0.05)

    if deleted > 0 or first_error is None:
        cursor.execute(
            "DELETE FROM broadcast_logs WHERE broadcast_id = ?", (broadcast_id,)
        )
        conn.commit()

    report = (
        f"🗑 <b>Удаление завершено.</b>\nУдалено сообщений: {deleted} из {total_logs}"
    )
    if deleted < total_logs and first_error:
        report += f"\n\n⚠️ <b>Причина ошибки у некоторых юзеров:</b>\n<code>{first_error}</code>"
    bot.send_message(admin_id, report, parse_mode="HTML")


# --- 🔥 ПАРСЕР RICH MESSAGES (API 10.1+) С ПОДДЕРЖКОЙ ЭМОДЗИ 🔥 ---


class EntObj:
    def __init__(self, d):
        self.type = d.get("type")
        self.offset = d.get("offset", 0)
        self.length = d.get("length", 0)
        self.url = d.get("url")
        self.custom_emoji_id = d.get("custom_emoji_id")


def adjust_entities_to_python(text, entities_dicts):
    if not entities_dicts:
        return []
    mapping = {}
    u16_pos = 0
    for py_pos, char in enumerate(text):
        mapping[u16_pos] = py_pos
        u16_pos += len(char.encode("utf-16-le")) // 2
    mapping[u16_pos] = len(text)

    def get_py_pos(u16):
        if u16 in mapping:
            return mapping[u16]
        for k in sorted(mapping.keys()):
            if k > u16:
                return mapping[k]
        return len(text)

    new_ents = []
    for ed in entities_dicts:
        e = EntObj(ed)
        py_offset = get_py_pos(e.offset)
        py_end = get_py_pos(e.offset + e.length)
        ne = EntObj(
            {
                "type": e.type,
                "offset": py_offset,
                "length": py_end - py_offset,
                "url": e.url,
                "custom_emoji_id": e.custom_emoji_id,
            }
        )
        new_ents.append(ne)
    return new_ents


def entities_to_rich_text(text, entities):
    if not entities:
        return text
    entities = sorted(entities, key=lambda e: (e.offset, -e.length))

    def process(start_idx, end_idx, ents):
        if not ents:
            return text[start_idx:end_idx]
        result = []
        current_idx = start_idx
        i = 0
        while i < len(ents):
            e = ents[i]
            if e.offset > current_idx:
                result.append(text[current_idx : e.offset])
                current_idx = e.offset
            e_end = e.offset + e.length
            children = []
            j = i + 1
            while j < len(ents) and ents[j].offset < e_end:
                if ents[j].offset + ents[j].length <= e_end:
                    children.append(ents[j])
                j += 1
            inside_content = process(e.offset, e_end, children)
            obj = {"type": e.type, "text": inside_content}
            if e.type == "text_link":
                obj["type"] = "url"
                obj["url"] = e.url
            elif e.type == "custom_emoji":
                obj["type"] = "custom_emoji"
                obj["custom_emoji_id"] = e.custom_emoji_id
            result.append(obj)
            current_idx = e_end
            i = j
        if current_idx < end_idx:
            result.append(text[current_idx:end_idx])
        result = [r for r in result if r != ""]
        if not result:
            return ""
        return result if len(result) > 1 else result[0]

    return process(0, len(text), entities)


def parse_rich_blocks(text, entities_dicts):
    blocks = []
    if not text:
        return blocks
    entities = adjust_entities_to_python(text, entities_dicts)
    custom_emojis = extract_custom_emojis_map(text, entities_dicts)

    # Регулярка убирает пустые отступы перед и после кнопок
    pattern = r"\s*\[\[(.*?)\]\]\s*"
    last_end = 0

    for match in re.finditer(pattern, text, re.DOTALL):
        start_idx = match.start()
        end_idx = match.end()

        if start_idx > last_end:
            para_text = text[last_end:start_idx]
            para_ents = []
            for e in entities:
                if e.offset >= last_end and e.offset < start_idx:
                    new_len = min(e.length, start_idx - e.offset)
                    para_ents.append(
                        EntObj(
                            {
                                "type": e.type,
                                "offset": e.offset - last_end,
                                "length": new_len,
                                "url": e.url,
                                "custom_emoji_id": e.custom_emoji_id,
                            }
                        )
                    )
            rich_text = entities_to_rich_text(para_text, para_ents)
            blocks.append({"type": "paragraph", "text": rich_text})

        buttons_str = match.group(1)
        row_buttons = []
        for btn_str in buttons_str.split("|"):
            parts = btn_str.split("-", 1)
            if len(parts) == 2:
                btn_text, btn_url = parts[0].strip(), parts[1].strip()
                style = None
                if btn_text.lower().startswith("(primary)"):
                    style = "primary"
                    btn_text = btn_text[9:].strip()
                elif btn_text.lower().startswith("(success)"):
                    style = "success"
                    btn_text = btn_text[9:].strip()
                elif btn_text.lower().startswith("(danger)"):
                    style = "danger"
                    btn_text = btn_text[8:].strip()

                btn_emoji_id = None
                # Извлекаем кастомный эмодзи внутри кнопки, если он есть
                for emo_txt, emo_id in custom_emojis.items():
                    if emo_txt in btn_text:
                        btn_emoji_id = emo_id
                        btn_text = btn_text.replace(emo_txt, "", 1).strip()
                        break

                if not btn_emoji_id:
                    emo_match = re.search(r"\(emoji:(\d+)\)", btn_text, re.IGNORECASE)
                    if emo_match:
                        btn_emoji_id = emo_match.group(1)
                        btn_text = btn_text.replace(emo_match.group(0), "").strip()

                btn_obj = {"text": btn_text, "url": btn_url}
                if style:
                    btn_obj["style"] = style
                if btn_emoji_id:
                    btn_obj["icon_custom_emoji_id"] = btn_emoji_id
                row_buttons.append(btn_obj)

        if row_buttons:
            blocks.append({"type": "buttons", "buttons": row_buttons})

        last_end = end_idx

    if last_end < len(text):
        para_text = text[last_end:]
        para_ents = []
        for e in entities:
            if e.offset >= last_end:
                para_ents.append(
                    EntObj(
                        {
                            "type": e.type,
                            "offset": e.offset - last_end,
                            "length": e.length,
                            "url": e.url,
                            "custom_emoji_id": e.custom_emoji_id,
                        }
                    )
                )
        rich_text = entities_to_rich_text(para_text, para_ents)
        blocks.append({"type": "paragraph", "text": rich_text})

    if not blocks and text:
        rich_text = entities_to_rich_text(text, entities)
        blocks.append({"type": "paragraph", "text": rich_text})

    return blocks


def publish_post_draft(target_chat_id, from_chat_id, draft, markup):
    text = draft.get("text", "")
    entities = draft.get("entities", [])

    if "[[" in text and "]]" in text:
        blocks = parse_rich_blocks(text, entities)

        if draft.get("media_id"):
            m_type = draft["media_type"]
            blocks.insert(
                0,
                {"type": m_type, m_type: {"type": m_type, "media": draft["media_id"]}},
            )

        url = f"https://api.telegram.org/bot{TOKEN}/sendRichMessage"
        payload = {"chat_id": target_chat_id, "rich_message": {"blocks": blocks}}

        if markup:
            payload["reply_markup"] = (
                markup.to_dict() if hasattr(markup, "to_dict") else markup
            )

        response = requests.post(url, json=payload)
        response.raise_for_status()
        return True
    else:
        bot.copy_message(
            target_chat_id, from_chat_id, draft["message_id"], reply_markup=markup
        )
        return True


# --- БЛОК: СОЗДАНИЕ И ОТЛОЖЕННАЯ ПУБЛИКАЦИЯ ПОСТОВ ---
@bot.message_handler(commands=["addchannel"])
def cmd_add_channel(message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split()
    if len(args) < 2:
        return bot.reply_to(
            message,
            "⚠️ Укажи юзернейм канала для добавления.\nПример: <code>/addchannel @AppVault7</code>",
            parse_mode="HTML",
        )
    channel = args[1] if args[1].startswith("@") else "@" + args[1]
    if add_channel_db(channel):
        bot.reply_to(
            message,
            f"✅ Канал {channel} добавлен в базу.\n\n⚠️ <b>Важно:</b> Не забудь выдать боту права администратора в этом канале!",
            parse_mode="HTML",
        )
    else:
        bot.reply_to(message, "❌ Ошибка БД при добавлении канала.")


@bot.message_handler(commands=["delchannel"])
def cmd_del_channel(message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split()
    if len(args) < 2:
        return bot.reply_to(
            message,
            "⚠️ Укажи юзернейм канала для удаления.\nПример: <code>/delchannel @AppVault7</code>",
            parse_mode="HTML",
        )
    channel = args[1] if args[1].startswith("@") else "@" + args[1]
    del_channel_db(channel)
    bot.reply_to(
        message, f"🗑 Канал {channel} успешно удален из базы.", parse_mode="HTML"
    )


@bot.message_handler(commands=["post"])
def cmd_post(message):
    if message.from_user.id != ADMIN_ID:
        return
    channels = get_channels_db()
    if not channels:
        return bot.reply_to(
            message,
            "⚠️ Сперва добавь канал в базу с помощью команды:\n<code>/addchannel @твойканал</code>",
            parse_mode="HTML",
        )

    markup = InlineKeyboardMarkup()
    for ch in channels:
        markup.add(InlineKeyboardButton(ch, callback_data=f"p_chan_{ch}"))
    bot.send_message(
        message.chat.id,
        "📢 <b>Создание поста</b>\n\nВыбери канал для публикации из списка ниже:",
        reply_markup=markup,
        parse_mode="HTML",
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("p_chan_"))
def post_channel_selected(call):
    admin_id = call.from_user.id
    if admin_id != ADMIN_ID:
        return
    channel = call.data.replace("p_chan_", "")
    set_admin_session(
        admin_id, "post", {"channel": channel, "message_id": None, "buttons": None}
    )

    instruction = (
        f"📢 Ты выбрал канал: <b>{channel}</b>\n\n"
        f"Отправь боту сообщение, которое собираешься опубликовать.\n"
        f"<i>(Поддерживается текст, фото, видео)</i>\n\n"
        f"💡 <b>НОВАЯ ФИШКА: Внутритекстовые кнопки с эмодзи!</b>\n"
        f"Чтобы добавить кнопки прямо внутрь текста, напиши в нужном месте:\n"
        f"<code>[[ (success) 💎 Скачать - https://... | 💬 Чат - https://... ]]</code>"
    )
    bot.edit_message_text(
        instruction, call.message.chat.id, call.message.message_id, parse_mode="HTML"
    )
    bot.register_next_step_handler(call.message, process_post_content)


def process_post_content(message):
    admin_id = message.from_user.id
    draft = get_admin_session(admin_id, "post")
    if not draft:
        return

    draft["message_id"] = message.message_id
    draft["text"] = message.text or message.caption or ""
    draft["entities"] = (
        message.json.get("entities") or message.json.get("caption_entities") or []
    )
    draft["media_id"] = None
    draft["media_type"] = None

    if message.photo:
        draft["media_id"] = message.photo[-1].file_id
        draft["media_type"] = "photo"
    elif message.video:
        draft["media_id"] = message.video.file_id
        draft["media_type"] = "video"
    elif message.audio:
        draft["media_id"] = message.audio.file_id
        draft["media_type"] = "audio"
    elif message.document:
        draft["media_id"] = message.document.file_id
        draft["media_type"] = "document"

    set_admin_session(admin_id, "post", draft)
    show_post_menu(admin_id, message.chat.id)


def show_post_menu(admin_id, chat_id):
    draft = get_admin_session(admin_id, "post")
    if not draft:
        return
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("🔗 Урл-кнопки (Внизу)", callback_data="post_add_btns")
    )
    markup.add(InlineKeyboardButton("👁 Предпросмотр", callback_data="post_preview"))
    markup.add(
        InlineKeyboardButton("✅ Опубликовать", callback_data="post_publish"),
        InlineKeyboardButton("📅 Отложить", callback_data="post_schedule"),
    )
    markup.add(InlineKeyboardButton("❌ Отмена", callback_data="post_cancel"))
    bot.send_message(
        chat_id,
        "⚙️ <b>Меню поста:</b>\nЧто делаем дальше?",
        reply_markup=markup,
        parse_mode="HTML",
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("post_"))
def post_menu_callback(call):
    admin_id = call.from_user.id
    if admin_id != ADMIN_ID:
        return
    bot.answer_callback_query(call.id)
    action = call.data
    draft = get_admin_session(admin_id, "post")

    if not draft and action != "post_cancel":
        return bot.send_message(
            call.message.chat.id, "❌ Черновик не найден. Начни заново через /post."
        )

    if action == "post_cancel":
        set_admin_session(admin_id, "post", None)
        bot.edit_message_text(
            "❌ Создание поста отменено.", call.message.chat.id, call.message.message_id
        )
    elif action == "post_add_btns":
        text = (
            "🛠 <b>Настройка нижних URL-кнопок</b>\n\n"
            "Отправь список кнопок в таком формате:\n"
            "<code>Кнопка 1 - https://t.me/durov</code>\n\n"
            "💡 <b>Полезные фишки:</b>\n"
            "1️⃣ <b>Кнопки в ряд:</b> используй разделитель <code>|</code>\n"
            "<code>Кнопка 1 - http://... | Кнопка 2 - http://...</code>\n\n"
            "2️⃣ <b>Цветные кнопки:</b> добавь тег перед названием\n"
            "🔵 <code>(primary)</code> — синяя (основная)\n"
            "🟢 <code>(success)</code> — зеленая (успех/скачать)\n"
            "🔴 <code>(danger)</code> — красная (удалить/отмена)\n\n"
            "3️⃣ <b>Анимированные стикеры/эмодзи:</b> просто вставь премиум-эмодзи перед текстом кнопки!\n"
            "<i>Пример:</i> <code>(success) 💎 Скачать - https://... | 💬 Чат - https://...</code>\n\n"
            "❌ Напиши <code>0</code> (ноль), чтобы удалить все кнопки."
        )
        msg = bot.send_message(call.message.chat.id, text, parse_mode="HTML")
        bot.register_next_step_handler(msg, process_post_buttons)
    elif action == "post_preview":
        bot.send_message(
            call.message.chat.id,
            f"👀 <b>Предпросмотр поста для {draft['channel']}:</b>",
            parse_mode="HTML",
        )
        try:
            markup = (
                modern_markup_from_json(draft.get("buttons"))
                if draft.get("buttons")
                else None
            )
            publish_post_draft(call.message.chat.id, admin_id, draft, markup)
        except Exception as e:
            bot.send_message(call.message.chat.id, f"❌ Ошибка предпросмотра: {e}")
        show_post_menu(admin_id, call.message.chat.id)
    elif action == "post_publish":
        bot.edit_message_text(
            "🚀 Публикую...", call.message.chat.id, call.message.message_id
        )
        try:
            markup = (
                modern_markup_from_json(draft.get("buttons"))
                if draft.get("buttons")
                else None
            )
            publish_post_draft(draft["channel"], admin_id, draft, markup)
            bot.send_message(
                call.message.chat.id,
                f"✅ Сообщение успешно опубликовано в <b>{draft['channel']}</b>!",
                parse_mode="HTML",
            )
            set_admin_session(admin_id, "post", None)
        except Exception as e:
            bot.send_message(
                call.message.chat.id,
                f"❌ Ошибка публикации.\nУбедись, что бот является администратором канала.\n\n<code>{e}</code>",
                parse_mode="HTML",
            )
    elif action == "post_schedule":
        text = "⏳ <b>Отложенная публикация</b>\n\nОтправь дату и время по <b>Московскому времени</b> (UTC+3).\n\nФормат: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>\nПример: <code>25.12.2023 15:30</code>\n\n❌ Напиши <code>0</code> (ноль), чтобы отменить."
        msg = bot.send_message(call.message.chat.id, text, parse_mode="HTML")
        bot.register_next_step_handler(msg, process_schedule_time)


def process_post_buttons(message):
    admin_id = message.from_user.id
    draft = get_admin_session(admin_id, "post")
    if not draft:
        return

    if message.text.lower().strip() in ["0", "отмена"]:
        draft["buttons"] = None
        set_admin_session(admin_id, "post", draft)
        bot.send_message(message.chat.id, "🗑 Нижние кнопки успешно удалены.")
        show_post_menu(admin_id, message.chat.id)
        return

    try:
        markup = parse_buttons_input(message.text, message.entities)
        draft["buttons"] = markup.to_json() if markup else None
        set_admin_session(admin_id, "post", draft)
        bot.send_message(
            message.chat.id, "✅ Кнопки с анимированными стикерами успешно сохранены!"
        )
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка форматирования кнопок: {e}")
    show_post_menu(admin_id, message.chat.id)


def process_schedule_time(message):
    admin_id = message.from_user.id
    draft = get_admin_session(admin_id, "post")
    if not draft:
        return
    if message.text.lower().strip() in ["0", "отмена", "cancel"]:
        return show_post_menu(admin_id, message.chat.id)

    try:
        dt = datetime.strptime(message.text.strip(), "%d.%m.%Y %H:%M")
        MSK = timezone(timedelta(hours=3))
        publish_time = int(dt.replace(tzinfo=MSK).timestamp())

        if publish_time <= time.time():
            msg = bot.send_message(
                message.chat.id,
                "❌ <b>Это время уже прошло!</b>\nПожалуйста, введи время в будущем (или напиши <code>0</code> для отмены):",
                parse_mode="HTML",
            )
            return bot.register_next_step_handler(msg, process_schedule_time)

        buttons_json = draft.get("buttons")
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """INSERT INTO scheduled_posts (admin_id, channel, message_id, buttons, publish_time, draft_data) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                admin_id,
                draft["channel"],
                draft["message_id"],
                buttons_json,
                publish_time,
                json.dumps(draft),
            ),
        )
        conn.commit()

        bot.send_message(
            message.chat.id,
            f"✅ <b>Пост успешно отложен!</b>\nОн будет автоматически опубликован в <b>{draft['channel']}</b> ровно в <code>{message.text}</code> (по Москве).",
            parse_mode="HTML",
        )
        set_admin_session(admin_id, "post", None)
    except ValueError:
        msg = bot.send_message(
            message.chat.id,
            "❌ <b>Неверный формат времени!</b>\nПожалуйста, используй строгий формат: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>\nПример: <code>25.12.2023 15:30</code>\n\nПопробуй еще раз (или напиши <code>0</code> для отмены):",
            parse_mode="HTML",
        )
        bot.register_next_step_handler(msg, process_schedule_time)


# --- УПРАВЛЕНИЕ ОТЛОЖЕННЫМИ ПОСТАМИ ---
@bot.message_handler(commands=["scheduled", "planned"])
def cmd_scheduled(message):
    if message.from_user.id != ADMIN_ID:
        return
    show_scheduled_list(message.chat.id)


def show_scheduled_list(chat_id, message_id=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, channel, publish_time FROM scheduled_posts ORDER BY publish_time ASC"
    )
    posts = cursor.fetchall()

    if not posts:
        text = "📭 <b>Отложенных постов нет.</b>"
        if message_id:
            bot.edit_message_text(text, chat_id, message_id, parse_mode="HTML")
        else:
            bot.send_message(chat_id, text, parse_mode="HTML")
        return

    text = (
        f"⏳ <b>Отложенные посты ({len(posts)} шт.):</b>\nВыберите пост для управления:"
    )
    markup = InlineKeyboardMarkup(row_width=1)
    MSK = timezone(timedelta(hours=3))
    for p in posts:
        p_id, channel, pub_time = p
        dt = datetime.fromtimestamp(pub_time, MSK).strftime("%d.%m %H:%M")
        markup.add(
            InlineKeyboardButton(f"[{dt}] {channel}", callback_data=f"sch_{p_id}")
        )

    if message_id:
        bot.edit_message_text(
            text, chat_id, message_id, reply_markup=markup, parse_mode="HTML"
        )
    else:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")


@bot.callback_query_handler(func=lambda call: call.data.startswith("sch_"))
def sched_callback(call):
    if call.from_user.id != ADMIN_ID:
        return
    action = call.data
    bot.answer_callback_query(call.id)

    if action == "sch_back":
        return show_scheduled_list(call.message.chat.id, call.message.message_id)

    parts = action.split("_")
    if len(parts) == 2:
        p_id = parts[1]
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT channel, publish_time FROM scheduled_posts WHERE id = ?", (p_id,)
        )
        res = cursor.fetchone()
        if not res:
            return bot.edit_message_text(
                "❌ Пост уже опубликован или удален.",
                call.message.chat.id,
                call.message.message_id,
            )

        channel, pub_time = res
        dt = datetime.fromtimestamp(pub_time, timezone(timedelta(hours=3))).strftime(
            "%d.%m.%Y %H:%M"
        )

        text = f"⚙️ <b>Управление постом #{p_id}</b>\n📢 Канал: <b>{channel}</b>\n⏳ Время: <code>{dt}</code> (МСК)"
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton("👁 Предпросмотр", callback_data=f"sch_prv_{p_id}")
        )
        markup.add(
            InlineKeyboardButton("⏳ Изменить время", callback_data=f"sch_time_{p_id}")
        )
        markup.add(
            InlineKeyboardButton(
                "📝 Заменить текст/медиа", callback_data=f"sch_txt_{p_id}"
            )
        )
        markup.add(
            InlineKeyboardButton(
                "🗑 Отменить публикацию", callback_data=f"sch_del_{p_id}"
            )
        )
        markup.add(InlineKeyboardButton("🔙 К списку", callback_data="sch_back"))

        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=markup,
            parse_mode="HTML",
        )

    elif parts[1] == "del":
        p_id = parts[2]
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM scheduled_posts WHERE id = ?", (p_id,))
        conn.commit()
        bot.send_message(call.message.chat.id, "🗑 Пост успешно отменен и удален!")
        show_scheduled_list(call.message.chat.id, call.message.message_id)

    elif parts[1] == "prv":
        p_id = parts[2]
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT message_id, buttons, draft_data FROM scheduled_posts WHERE id = ?",
            (p_id,),
        )
        res = cursor.fetchone()
        if res:
            m_id, btns, draft_data_json = res
            try:
                markup = modern_markup_from_json(btns) if btns else None
                if draft_data_json:
                    draft = json.loads(draft_data_json)
                    publish_post_draft(call.message.chat.id, ADMIN_ID, draft, markup)
                else:
                    bot.copy_message(
                        call.message.chat.id, ADMIN_ID, m_id, reply_markup=markup
                    )
            except Exception as e:
                bot.send_message(call.message.chat.id, f"❌ Ошибка предпросмотра: {e}")

    elif parts[1] == "time":
        p_id = parts[2]
        set_admin_session(ADMIN_ID, "sched_edit", {"type": "time", "id": p_id})
        msg = bot.send_message(
            call.message.chat.id,
            "⏳ Отправь новую дату и время (МСК) в формате:\n<code>ДД.ММ.ГГГГ ЧЧ:ММ</code>\n\n❌ Напиши <b>/cancel</b> или <b>отмена</b> для отмены.",
            parse_mode="HTML",
        )
        bot.register_next_step_handler(msg, process_edit_sched_time)

    elif parts[1] == "txt":
        p_id = parts[2]
        set_admin_session(ADMIN_ID, "sched_edit", {"type": "text", "id": p_id})
        msg = bot.send_message(
            call.message.chat.id,
            "📝 Отправь новое сообщение для этого поста (текст, фото, видео и т.д.).\n\n<i>Внимание: URL-кнопки останутся старыми. Если их тоже нужно изменить, лучше удали этот пост и создай заново через /post.</i>\n\n❌ Напиши <b>/cancel</b> или <b>отмена</b> для отмены.",
            parse_mode="HTML",
        )
        bot.register_next_step_handler(msg, process_edit_sched_text)


def process_edit_sched_time(message):
    if message.text and message.text.lower() in ["/cancel", "отмена"]:
        set_admin_session(ADMIN_ID, "sched_edit", None)
        return bot.send_message(message.chat.id, "❌ Редактирование отменено.")

    draft = get_admin_session(ADMIN_ID, "sched_edit")
    if not draft or draft.get("type") != "time":
        return

    try:
        dt = datetime.strptime(message.text.strip(), "%d.%m.%Y %H:%M")
        MSK = timezone(timedelta(hours=3))
        publish_time = int(dt.replace(tzinfo=MSK).timestamp())

        if publish_time <= time.time():
            msg = bot.send_message(
                message.chat.id,
                "❌ <b>Это время уже прошло!</b>\nВведи время в будущем или напиши /cancel:",
                parse_mode="HTML",
            )
            return bot.register_next_step_handler(msg, process_edit_sched_time)

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE scheduled_posts SET publish_time = ? WHERE id = ?",
            (publish_time, draft["id"]),
        )
        conn.commit()

        bot.send_message(
            message.chat.id,
            f"✅ Время публикации успешно изменено на <code>{message.text}</code>",
            parse_mode="HTML",
        )
        set_admin_session(ADMIN_ID, "sched_edit", None)
        show_scheduled_list(message.chat.id)

    except ValueError:
        msg = bot.send_message(
            message.chat.id,
            "❌ <b>Неверный формат!</b>\nНужно: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>\nПопробуй еще раз или напиши /cancel:",
            parse_mode="HTML",
        )
        bot.register_next_step_handler(msg, process_edit_sched_time)


def process_edit_sched_text(message):
    if message.text and message.text.lower() in ["/cancel", "отмена"]:
        set_admin_session(ADMIN_ID, "sched_edit", None)
        return bot.send_message(message.chat.id, "❌ Редактирование отменено.")

    draft = get_admin_session(ADMIN_ID, "sched_edit")
    if not draft or draft.get("type") != "text":
        return

    new_message_id = message.message_id
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE scheduled_posts SET message_id = ? WHERE id = ?",
        (new_message_id, draft["id"]),
    )
    conn.commit()

    bot.send_message(
        message.chat.id,
        "✅ Содержимое поста успешно обновлено!\n(Кстати, если ты хочешь обновить внутритекстовые кнопки у этого поста, лучше удали его и создай заново через /post, так как тут обновляется только текст).",
    )
    set_admin_session(ADMIN_ID, "sched_edit", None)
    show_scheduled_list(message.chat.id)


def scheduler_loop():
    last_cleanup = 0
    while True:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()

            current_time = int(time.time())
            cursor.execute(
                "SELECT id, admin_id, channel, message_id, buttons, draft_data FROM scheduled_posts WHERE publish_time <= ?",
                (current_time,),
            )
            posts = cursor.fetchall()
            for post in posts:
                (
                    post_id,
                    admin_id,
                    channel,
                    message_id,
                    buttons_json,
                    draft_data_json,
                ) = post
                markup = modern_markup_from_json(buttons_json)
                try:
                    if draft_data_json:
                        draft = json.loads(draft_data_json)
                        publish_post_draft(channel, admin_id, draft, markup)
                    else:
                        bot.copy_message(
                            channel, admin_id, message_id, reply_markup=markup
                        )

                    bot.send_message(
                        admin_id,
                        f"✅ Отложенный пост автоматически опубликован в <b>{channel}</b>!",
                        parse_mode="HTML",
                    )
                except Exception as e:
                    bot.send_message(
                        admin_id,
                        f"❌ Ошибка автоматической публикации поста в {channel}: {e}",
                    )
                cursor.execute("DELETE FROM scheduled_posts WHERE id = ?", (post_id,))

            if current_time - last_cleanup > 86400:
                one_year_ago = (datetime.now() - timedelta(days=365)).strftime(
                    "%Y-%m-%d"
                )
                cursor.execute(
                    "DELETE FROM daily_downloads WHERE date < ?", (one_year_ago,)
                )

                try:
                    cursor.execute("PRAGMA wal_checkpoint(PASSIVE);")
                except Exception:
                    pass

                expired = [k for k, v in _sub_cache.items() if v < current_time]
                for k in expired:
                    _sub_cache.pop(k, None)

                last_cleanup = current_time

            conn.commit()
        except Exception as e:
            logging.error(f"Системная ошибка в планировщике: {e}", exc_info=True)
        time.sleep(30)


threading.Thread(target=scheduler_loop, daemon=True).start()


def restore_database(message):
    bot.reply_to(message, "⏳ Загружаю старую базу данных на сервер...")
    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        with open("prod_database.db", "wb") as new_file:
            new_file.write(downloaded_file)
        bot.reply_to(message, "✅ База данных успешно восстановлена!")
    except Exception as e:
        logging.error(f"Ошибка при восстановлении базы данных: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Ошибка: {e}")


@bot.message_handler(commands=["stats"])
def stats_command(message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        today = datetime.now().strftime("%Y-%m-%d")
        this_month = datetime.now().strftime("%Y-%m")

        cursor.execute("SELECT COUNT(user_id) FROM users")
        total_users = cursor.fetchone()[0]

        cursor.execute(
            "SELECT COUNT(user_id) FROM users WHERE join_date LIKE ?", (f"{today}%",)
        )
        new_today = cursor.fetchone()[0]

        cursor.execute(
            "SELECT COUNT(user_id) FROM users WHERE last_active LIKE ?", (f"{today}%",)
        )
        dau = cursor.fetchone()[0]

        cursor.execute(
            "SELECT COUNT(user_id) FROM users WHERE last_active LIKE ?",
            (f"{this_month}%",),
        )
        mau = cursor.fetchone()[0]

        cursor.execute(
            "SELECT code, downloads FROM files ORDER BY downloads DESC LIMIT 10"
        )
        top_files_all = cursor.fetchall()

        cursor.execute(
            """
            SELECT code, SUM(count) as total 
            FROM daily_downloads 
            WHERE date LIKE ? 
            GROUP BY code 
            ORDER BY total DESC 
            LIMIT 10
        """,
            (f"{this_month}%",),
        )
        top_files_month = cursor.fetchall()

        text = (
            "📊 <b>Статистика бота:</b>\n\n"
            f"👥 Всего пользователей: <code>{total_users}</code>\n"
            f"📈 Новых за сегодня: <code>{new_today}</code>\n"
            f"🔥 Активных сегодня (DAU): <code>{dau}</code>\n"
            f"📅 Активных за месяц (MAU): <code>{mau}</code>\n\n"
            "🏆 <b>Топ-10 скачиваний (Всё время):</b>\n"
        )

        if not top_files_all:
            text += "<i>Скачиваний пока нет.</i>\n"
        else:
            for i, (code, count) in enumerate(top_files_all, 1):
                text += f"{i}. <code>{code}</code> — {count} раз\n"

        text += "\n🔥 <b>Топ-10 скачиваний (Текущий месяц):</b>\n"

        if not top_files_month:
            text += "<i>В этом месяце скачиваний нет.</i>"
        else:
            for i, (code, count) in enumerate(top_files_month, 1):
                text += f"{i}. <code>{code}</code> — {count} раз\n"

        bot.send_message(message.chat.id, text, parse_mode="HTML")

    except Exception as e:
        logging.error(f"Ошибка при формировании статистики: {e}", exc_info=True)
        bot.send_message(
            message.chat.id, "❌ Произошла системная ошибка при загрузке статистики."
        )


def setup_bot_commands():
    try:
        bot.set_my_commands(
            [BotCommand("/start", "Запустить бота")], scope=BotCommandScopeDefault()
        )
        admin_commands = [
            BotCommand("/start", "Запустить бота"),
            BotCommand("/post", "Создать пост в канал"),
            BotCommand("/scheduled", "Отложенные посты"),
            BotCommand("/cancel", "Отменить операцию"),
            BotCommand("/broadcast", "Сделать рассылку"),
            BotCommand("/stats", "Статистика бота"),
            BotCommand("/save", "Сохранить файл"),
            BotCommand("/delete", "Удалить файл"),
            BotCommand("/ads", "Управление рекламой"),
            BotCommand("/sub", "Обязательная подписка"),
            BotCommand("/addchannel", "Добавить канал"),
            BotCommand("/delchannel", "Удалить канал"),
        ]
        bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=ADMIN_ID))
    except Exception as e:
        logging.error(f"Ошибка при установке команд бота: {e}", exc_info=True)


setup_bot_commands()

# --- ПАРСЕР ПОПОЛНЕНИЙ GOOGLE ТАБЛИЦ ---
GOOGLE_WEBHOOK_URL = "https://script.google.com/macros/s/AKfycbxNcGv1hj4SVmmafaKeSxjWhhnTlcH3zG8hSCTWQRBaFPwxMd1IgH34YbrH5KB454bH/exec"
TARGET_CHAT_ID = -1003748001622
TARGET_THREAD_ID = 409


def save_transaction_to_queue(payload_dict):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO transactions_queue (payload, status) VALUES (?, ?)",
            (json.dumps(payload_dict), "pending"),
        )
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logging.error(f"Ошибка сохранения транзакции: {e}", exc_info=True)
        return False


def get_pending_transactions():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT id, payload FROM transactions_queue WHERE status = "pending"'
    )
    return cursor.fetchall()


def mark_transaction_sent(db_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        'UPDATE transactions_queue SET status = "sent" WHERE id = ?', (db_id,)
    )
    conn.commit()


def google_sheets_sync_loop():
    while True:
        try:
            pending = get_pending_transactions()
            if pending:
                logging.info(
                    f"🔄 Очередь: Найдено {len(pending)} транзакций для отправки в Google Sheets..."
                )
                for db_id, payload_str in pending:
                    payload = json.loads(payload_str)
                    try:
                        response = requests.post(
                            GOOGLE_WEBHOOK_URL, json=payload, timeout=10
                        )
                        if response.status_code == 200:
                            mark_transaction_sent(db_id)
                            logging.info(
                                f"✅ Транзакция {payload['transaction_id']} успешно выгружена."
                            )
                        else:
                            logging.error(f"Ошибка Google: {response.status_code}.")
                    except requests.exceptions.RequestException as e:
                        logging.error(f"Нет связи с Google: {e}.")
                        break
        except Exception as e:
            logging.error(
                f"Системная ошибка в воркере Google Sheets: {e}", exc_info=True
            )
        time.sleep(60)


threading.Thread(target=google_sheets_sync_loop, daemon=True).start()


AMOUNT_REGEX = re.compile(r"💵\s*([\d\s]+)\s*₽")
USER_REGEX = re.compile(r"👤\s*(.*?)\s*\((\d+)\)")
TX_REGEX = re.compile(r"ID\s*транзакции:\s*(\d+)")

@bot.message_handler(
    content_types=["text", "photo", "document"],
    func=lambda message: bool(
        (message.text and "ПОПОЛНЕНИЕ БАЛАНСА" in message.text)
        or (message.caption and "ПОПОЛНЕНИЕ БАЛАНСА" in message.caption)
    ),
)
def parse_finance_message(message):
    if TARGET_CHAT_ID and message.chat.id != TARGET_CHAT_ID:
        return
    if TARGET_THREAD_ID and message.message_thread_id != TARGET_THREAD_ID:
        return

    text = message.text or message.caption
    try:
        amount_match = AMOUNT_REGEX.search(text)
        amount = int(amount_match.group(1).replace(" ", "")) if amount_match else 0

        user_match = USER_REGEX.search(text)
        user_name, user_id = "Неизвестно", "0"
        if user_match:
            user_name = user_match.group(1).strip() or "Без имени"
            user_id = user_match.group(2)

        tx_match = TX_REGEX.search(text)
        tx_id = tx_match.group(1) if tx_match else "0"

        payment_type = (
            "Первое"
            if "🆕 Первое пополнение" in text
            else ("Повторное" if "🔄 Пополнение" in text else "Неизвестно")
        )
        commission = 12 if "Карты" in text else 11

        MSK = timezone(timedelta(hours=3))
        current_date = datetime.now(MSK).strftime("%d.%m.%Y")

        payload = {
            "date": current_date,
            "transaction_id": tx_id,
            "user_id": user_id,
            "user_name": user_name,
            "payment_type": payment_type,
            "amount": amount,
            "currency": "RUB",
            "commission": commission,
        }

        if save_transaction_to_queue(payload):
            logging.info(
                f"⏳ Транзакция {tx_id} ({amount} ₽) спасена в локальную очередь."
            )

    except Exception as e:
        logging.error(f"Ошибка при парсинге сообщения в группе: {e}", exc_info=True)


# --- НАСТРОЙКИ ДЛЯ РЕЖИМА WEBHOOK ---
@app.route("/", methods=["GET", "HEAD"])
def index():
    return """<html><body style="padding: 2rem; text-align: center; background-color: #f4f4f9;">
        <h1 style="color: #4CAF50;">✅ Бот успешно запущен!</h1>
        <br><a href="/set_webhook" style="padding: 15px 25px; background-color: #0088cc; color: white; border-radius: 8px;">🔗 Привязать Webhook</a>
        </body></html>"""


@app.route("/set_webhook", methods=["GET"])
def set_webhook():
    bot.remove_webhook()
    time.sleep(1)
    host_url = request.host_url.rstrip("/")
    webhook_url = f"{host_url}/{TOKEN}"
    success = bot.set_webhook(url=webhook_url)

    if success:
        return f'<body style="text-align: center;"><h2 style="color:green;">✅ Webhook успешно установлен!</h2><p><b>{webhook_url}</b></p></body>'
    else:
        return '<h3 style="color:red; text-align:center;">❌ Ошибка установки Webhook!</h3>'


@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        json_string = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_string)
        webhook_pool.submit(bot.process_new_updates, [update])
        return "", 200
    else:
        return "error", 403


if __name__ == "__main__":
    webhook_url = f"{WEBHOOK_HOST}/{TOKEN}"

    try:
        webhook_info = bot.get_webhook_info()
        if webhook_info.url != webhook_url:
            logging.info("🔄 Webhook URL изменился или отсутствует. Обновляем...")
            bot.remove_webhook()
            time.sleep(1)
            bot.set_webhook(url=webhook_url)
        else:
            logging.info(
                "✅ Webhook уже установлен корректно. Пропускаем обновление, чтобы не потерять сообщения."
            )
    except Exception as e:
        logging.error(
            f"⚠️ Не удалось проверить вебхук: {e}. Пробуем установить принудительно..."
        )
        bot.remove_webhook()
        time.sleep(1)
        bot.set_webhook(url=webhook_url)

    port = int(os.environ.get("PORT", 3000))
    logging.info(f"🚀 Запуск WSGI-сервера Waitress на порту {port}...")
    import signal
    import sys

    def handle_sigterm(signum, frame):
        logging.info("Остановка по SIGTERM (Bothost).")
        os._exit(0)

    try:
        signal.signal(signal.SIGTERM, handle_sigterm)
    except Exception:
        pass

    serve(app, host="0.0.0.0", port=port)
