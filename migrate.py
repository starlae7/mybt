import sqlite3

# --- ТВОИ СТАРЫЕ ФАЙЛЫ ---
# Если у тебя были еще файлы, добавь их сюда в таком же формате: 'код': 'id',
old_files = {
   '1': 'BQACAgIAAxkBAAMEaThuxZMmVsuaD0kuDDLlTzLH4ecAAn2XAAJNsMFJD6AugwWfI0w2BA', 
    '2': 'BQACAgIAAxkBAAMyaTiW3Mwvg5PMyKXUb6yAykf56YEAAiGZAAJNsMFJpz2Bdlh9_EQ2BA',    
    '3':'BQACAgIAAxkBAANiaTlb2gRxBHq_LF-DOJ3n4PDF9RAAAsWJAAJNsMlJaUx4Ad9_1YQ2BA', 
  '4':'BQACAgIAAxkBAAN6aTljM85bQ3NFc75dnwILndE2QIEAAlGKAAJNsMlJKwJtkHT3z2M2BA',
   '5':'BQACAgIAAxkBAAIBhmk6-kRwN8GD5XO_69MRsj7dB_4VAAJYiwACMkvYSS3YKczhUOPpNgQ',
    '6':'BQACAgIAAxkBAAIC52k9bmzdAAFrbjxtV_FEMvb1TBQNXQACfooAAo7g6Un33Hd0g8wF7jYE',
    '7':'BQACAgIAAxkBAAIDvWk9vjEsHkPONKQqK4vVQ8xAk9CKAAJ3kQACjuDxSYnpib43E7jVNgQ',
    '8':'BQACAgIAAxkBAAIJdmlBwpsLM5AXjuGAafLsQPy_NlFkAALnkAACetQRSh2g1pmj2_5dNgQ',
    '9':'BQACAgIAAxkBAAIME2lFER9bsvurAAHLbs3Vm2Y1r1W5EAAC2IYAApnNMUom3d9VbFfO6zYE',
    '10':'BQACAgIAAxkBAAIQdWlKV4lF6z-CYlnmTtP_4FIivvziAAJ3gQACk6FRSmAo6l6x6azbNgQ',
    '11':'BQACAgIAAxkBAAIUBWlOXMPio3TqZbv4_hALs41MsWU8AAIOjAACw6dxSt64NSJv93QTNgQ',
    '12':'BQACAgIAAxkBAAIYnGlSY7sDbb82UFaJLDqucHJKa_gGAALzigACkMWQSspRmjhDkIx9NgQ',
    '13':'BQACAgIAAxkBAAIfDGlY0jXBToVOG_9GotWMe_H4kSYUAAJZhgACqdXJSqLCtRmA5ncmOAQ',
    '14':'BQACAgIAAxkBAAIj7mlcvuF0F_NTI_Xy_2a6PPP0B_u5AAKshgAChV_pSkE82UPnOK3nOAQ',
    '15':'BQACAgIAAxkBAAImCWlerYEZGV6_KsB_hcpNnQcEfee-AAJCjAACNuX4Si4udweYWehKOAQ',

'16':'BQACAgIAAxkBAAIq62livzMURt6J7qDKTKv4K_VtMeuoAAJalAACX34ZS-UIv2mUwKJUOAQ',
    '17':'BQACAgIAAxkBAAIxXGloxYJ-ua80L6SAv1TrPczL38P5AAIjoAACjdlJSxjdr7Y7t5M1OAQ',
    '18':'BQACAgIAAxkBAAIznmlrfWlernxu_G_5uLtxhHq4ItMQAALqkwACCvVZS-o9YbjTbOScOAQ',
    '19':'BQACAgIAAxkBAAI3OmlvXrAW4o7Cj0ja-RQWvlQQYAU0AAJslwACev94S6jVduL4HkHIOAQ',
    '20':'BQACAgIAAxkBAAI5hGlx-rebJCEsT2vULA-fhxK7OyAbAAIdjgACMpuRS0094GkjhgptOAQ',

'21':'BQACAgIAAxkBAAI8QWl0pCYryL9w9a92_DgEZKvmr8yHAALxjgACZpupS4Ct-lq4tpmdOAQ',
    '22':'BQACAgIAAxkBAAJA8Wl4d5fgbmrWysQcjwspL64CMxaXAALSkQACUsfASyKlFxnwpoRYOAQ',
    '23':'BQACAgIAAxkBAAJFl2l8gbhIIr6d1x08LuZq8Y_A41p_AALykQACCGngSyLIIUYPZYoPOAQ',
    '24':'BQACAgIAAxkBAAJJ8ml_JEGYl-koBLJil8Ns9iC6Wix1AALEjAACNKz5S1x_QU8pUM3oOAQ',
    '25':'BQACAgIAAxkBAAJMfmmAXdpHtsk355Vltn64Pel8DJeCAAKEkQACnsUJSPRhz8z-X6a1OAQ',
    '26':'BQACAgIAAxkBAAJXbWmEPEY7e_m9UmQ3eO0bUe6Yl60IAALZhgACd2UhSMWrakdcHAABcTgE',
    '27':'BQACAgIAAxkBAAJb32mFxdRFqJnMrqEnCEoK90oF0_FZAAKLjwACd2UxSMyUzPT6G_0zOAQ',
    '28':'BQACAgIAAxkBAAJgpmmHKJB5JNfkdAcTgz5QXtnnjneRAALuowAC5T9BSFyiH-IkUwAB7zoE',

'29':'BQACAgIAAxkBAAJpjmmKA7qZLFnKlIDISA9_-feD4QFhAAKmkQACWFxRSDnU0hLMZTVrOgQ',

'30':'BQACAgIAAxkBAAJuDWmMlRwjINkK4BEbHhTqvJu-zF1UAAKqmQACGZtgSAkJrM9bS-Y-OgQ',

'31':'BQACAgIAAxkBAAJxxGmPCrctxYDEM--R5cGVWzxlIaVLAAK6kAACHmt5SOVXlzPaw1zIOgQ',

'32':'BQACAgIAAxkBAAJz0mmQIlaz8PFpm6-D_zQVR5XTqb7EAALFmgACu4qASMCNzqVzuCXVOgQ',

'33':'BQACAgIAAxkBAAJ2T2mRf3AvMUJ8rUQx9yuj3arfTOEJAALZiwACu4qQSHab3SBFNonCOgQ',

'34':'BQACAgIAAxkBAAJ7Z2mUP2YhQjJT9Z8p9WV0JMijGvBVAALUjAAC7YihSAdM4AbMiukJOgQ',
}

# Стандартная подпись (как в боте)
CAPTION = "🗂 Держи свой файл!\nНе забудь поставить реакцию на канал 💙\n@AppVault7"

def migrate():
    print("Подключаюсь к базе данных...")
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()

    count = 0
    for code, file_id in old_files.items():
        # Мы записываем их как 'document', так как в старом коде 
        # ты использовал send_document для всего.
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO files (code, file_id, file_type, caption) 
                VALUES (?, ?, ?, ?)
            ''', (code, file_id, 'document', CAPTION))
            print(f"✅ Добавлен файл: {code}")
            count += 1
        except Exception as e:
            print(f"❌ Ошибка с файлом {code}: {e}")

    conn.commit()
    conn.close()
    print(f"\n--- ГОТОВО! Перенесено файлов: {count} ---")

if name == 'main':
    migrate()
