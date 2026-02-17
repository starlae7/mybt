import telebot

# ВСТАВЬТЕ СЮДА ВАШ ТОКЕН ОТ BOTFATHER
TOKEN = '8305187664:AAEFr22uqyFxodm5Uj36kTWZlU-689m3FSw'
bot = telebot.TeleBot(TOKEN)

# Это наша "база данных".
# Слева - кодовое слово для ссылки, Справа - file_id файла в Телеграм.
# Пока оставим пустым, я объясню ниже, как заполнить.
files = {
    '1': 'BQACAgIAAxkBAAMEaThuxZMmVsuaD0kuDDLlTzLH4ecAAn2XAAJNsMFJD6AugwWfI0w2BA', 
    '2': 'BQACAgIAAxkBAAMyaTiW3Mwvg5PMyKXUb6yAykf56YEAAiGZAAJNsMFJpz2Bdlh9_EQ2BA',    # Пример ID
}

@bot.message_handler(commands=['start'])
def start_message(message):
    # Получаем текст, который идет после /start
    # Например, если ссылка t.me/bot?start=minecraft, то args будет 'minecraft'
    args = message.text.split()
    
    if len(args) > 1:
        # Если есть аргумент (код файла)
        code = args[1]
        
        if code in files:
            # Если такой код есть в нашем списке, отправляем файл
            file_to_send = files[code]
            try:
                # Отправляем документ. Если это фото/видео, команда будет другой.
                bot.send_document(message.chat.id, file_to_send, caption="🗂 Держи свой файл!\nНе забудь поставить реакцию\nна канал 💙\n@AppVault7")
            except Exception as e:
                bot.send_message(message.chat.id, "Ой, что-то пошло не так при отправке файла.")
        else:
            bot.send_message(message.chat.id, "Файл не найден. Проверьте ссылку.")
    else:
        # Если человек просто нажал /start без ссылки
        bot.send_message(message.chat.id, 'Привет! Я бот для скачивания файлов. Переходи по ссылкам из канала <a href="https://t.me/AppVault7">AppVault</a>.',parse_mode='HTML' )

# Маленькая хитрость: этот кусок кода поможет вам узнать file_id
# Просто перешлите боту любой файл, и он пришлет вам его ID в ответ.
@bot.message_handler(content_types=['document', 'video', 'audio', 'photo'])
def get_file_id(message):
    # Проверяем тип файла и берем нужный ID
    if message.document:
        file_id = message.document.file_id
    elif message.video:
        file_id = message.video.file_id
    elif message.photo:
        # У фото несколько размеров, берем самый большой (-1)
        file_id = message.photo[-1].file_id
        
    bot.send_message(message.chat.id, f"ID этого файла:\n`{file_id}`", parse_mode='Markdown')

# Запуск бота (чтобы он не выключался)

bot.infinity_polling()

