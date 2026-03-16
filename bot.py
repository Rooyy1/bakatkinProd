import sqlite3
import asyncio
import logging
import re
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.bot import DefaultBotProperties
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import os
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID'))

# 🎥 FILE_ID видео (основное)
VIDEO_FILE_ID = "BAACAgIAAxkBAAICmGm4DyUF73YuS0Y0E0JH8QeasxL6AAIVkAACN0zASa2wzY7Zy0hwOgQ"

# 🎥 FILE_ID эфира (после кнопки)
EFIR_FILE_ID = "BAACAgIAAxkBAAICzGm4MnFzHcgUowhyfOCoaASmYgQRAAL4nwACFYxYSeYdAwEmqlZmOgQ"

# Ссылки на КЭШАП
KESHAP_LINK_1 = "https://s.bothelp.io/r/l1hexo.d1"
KESHAP_LINK_2 = "https://s.bothelp.io/r/l1hk13.d1"

# Ускоренная сессия для бота
session = AiohttpSession(timeout=30)
bot = Bot(
    token=BOT_TOKEN, 
    session=session,
    default=DefaultBotProperties(parse_mode="HTML")
)
dp = Dispatcher()

# --- Состояния для создания рассылки с кнопками ---
class MailingStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_button = State()
    waiting_for_button_text = State()
    waiting_for_button_url = State()
    waiting_for_second_button = State()
    waiting_for_second_button_text = State()
    waiting_for_second_button_url = State()

# --- База данных SQLite ---
class Database:
    def __init__(self):
        self.conn = None
        self.cursor = None
    
    def connect(self):
        self.conn = sqlite3.connect('bot_database.db', isolation_level=None)
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.cursor = self.conn.cursor()
        self.create_tables()
        print("✅ База данных SQLite подключена")
    
    def create_tables(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_activity TIMESTAMP,
                is_active INTEGER DEFAULT 1,
                started_at TIMESTAMP,
                reminder_sent INTEGER DEFAULT 0
            )
        ''')
        self.conn.commit()
    
    def add_user(self, user_id, username, first_name):
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.cursor.execute('''
            INSERT OR REPLACE INTO users (user_id, username, first_name, last_activity, started_at, reminder_sent)
            VALUES (?, ?, ?, ?, ?, COALESCE((SELECT reminder_sent FROM users WHERE user_id = ?), 0))
        ''', (user_id, username, first_name, now, now, user_id))
        self.conn.commit()
    
    def get_all_users(self):
        self.cursor.execute('SELECT user_id FROM users WHERE is_active = 1')
        return [row[0] for row in self.cursor.fetchall()]
    
    def get_users_for_reminder(self):
        now = datetime.now()
        one_day_ago = (now - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
        
        self.cursor.execute('''
            SELECT user_id FROM users 
            WHERE is_active = 1 
            AND reminder_sent = 0
            AND started_at <= ?
        ''', (one_day_ago,))
        return [row[0] for row in self.cursor.fetchall()]
    
    def mark_reminder_sent(self, user_id):
        self.cursor.execute('''
            UPDATE users SET reminder_sent = 1 WHERE user_id = ?
        ''', (user_id,))
        self.conn.commit()
    
    def get_stats(self):
        self.cursor.execute('SELECT COUNT(*) FROM users WHERE is_active = 1')
        return self.cursor.fetchone()[0]
    
    def deactivate_user(self, user_id):
        self.cursor.execute(
            "UPDATE users SET is_active = 0 WHERE user_id = ?",
            (user_id,)
        )
        self.conn.commit()
        logging.info(f"Пользователь {user_id} деактивирован")
    
    def close(self):
        if self.conn:
            self.conn.close()

db = Database()

# --- Клавиатуры ---
def get_watch_keyboard():
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👀 ХОЧУ ИЗУЧАТЬ AI", callback_data="watch")]
        ]
    )
    return keyboard

def get_efir_keyboard():
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="ЗАБРАТЬ ЭФИР ПРО AI", callback_data="get_efir")]
        ]
    )
    return keyboard

def get_reminder_keyboard():
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔗 УЗНАТЬ БОЛЬШЕ О КЭШАП", url=KESHAP_LINK_2)]
        ]
    )
    return keyboard

def get_button_choice_keyboard():
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Добавить кнопку", callback_data="add_button")],
            [InlineKeyboardButton(text="🚀 Отправить сразу", callback_data="send_now")]
        ]
    )
    return keyboard

def get_second_button_choice_keyboard():
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Добавить вторую кнопку", callback_data="add_second_button")],
            [InlineKeyboardButton(text="🚀 Отправить с одной", callback_data="send_with_one")]
        ]
    )
    return keyboard

# --- Функция для отправки одному пользователю ---
async def send_message_to_user(bot, user_id, message, reply_markup=None):
    try:
        if message.text:
            await bot.send_message(
                user_id, 
                message.text, 
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        elif message.photo:
            await bot.send_photo(
                user_id, 
                message.photo[-1].file_id, 
                caption=message.caption,
                parse_mode="HTML" if message.caption else None,
                reply_markup=reply_markup
            )
        elif message.video:
            await bot.send_video(
                user_id, 
                message.video.file_id, 
                caption=message.caption,
                parse_mode="HTML" if message.caption else None,
                reply_markup=reply_markup
            )
        elif message.video_note:
            await bot.send_video_note(user_id, message.video_note.file_id)
        elif message.document:
            await bot.send_document(
                user_id, 
                message.document.file_id, 
                caption=message.caption,
                parse_mode="HTML" if message.caption else None,
                reply_markup=reply_markup
            )
        elif message.audio:
            await bot.send_audio(
                user_id, 
                message.audio.file_id, 
                caption=message.caption,
                parse_mode="HTML" if message.caption else None,
                reply_markup=reply_markup
            )
        elif message.voice:
            await bot.send_voice(
                user_id, 
                message.voice.file_id, 
                caption=message.caption,
                parse_mode="HTML" if message.caption else None,
                reply_markup=reply_markup
            )
        elif message.sticker:
            await bot.send_sticker(user_id, message.sticker.file_id)
        elif message.animation:
            await bot.send_animation(
                user_id, 
                message.animation.file_id, 
                caption=message.caption,
                parse_mode="HTML" if message.caption else None,
                reply_markup=reply_markup
            )
        return True
    except Exception as e:
        error_text = str(e).lower()
        
        if any(err in error_text for err in [
            "blocked by the user",
            "user is deactivated",
            "chat not found",
            "can't send to this user",
            "bot was blocked"
        ]):
            db.deactivate_user(user_id)
            return "blocked"
        else:
            if "flood" in error_text or "too many requests" in error_text:
                wait_match = re.search(r"(\d+)", error_text)
                if wait_match:
                    wait_time = int(wait_match.group(1))
                    logging.warning(f"FloodWait: нужно подождать {wait_time} сек")
                    return f"flood_{wait_time}"
            return False

# --- Функция для массовой рассылки ---
async def send_mailing_to_all(message: types.Message, reply_markup=None):
    users = db.get_all_users()
    
    if not users:
        return
    
    logging.info(f"Начало рассылки для {len(users)} пользователей")
    
    batch_size = 20
    sent = 0
    blocked = 0
    failed = 0
    
    for i in range(0, len(users), batch_size):
        batch = users[i:i + batch_size]
        tasks = []
        
        for user_id in batch:
            tasks.append(send_message_to_user(bot, user_id, message, reply_markup))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if result is True:
                sent += 1
            elif result == "blocked":
                blocked += 1
            elif isinstance(result, str) and result.startswith("flood_"):
                try:
                    wait_time = int(result.split("_")[1])
                    logging.warning(f"Получен FloodWait на {wait_time} сек")
                    await asyncio.sleep(wait_time)
                except:
                    await asyncio.sleep(1)
                failed += 1
            else:
                failed += 1
        
        await asyncio.sleep(1.0)
        
        if (i + batch_size) % 100 == 0 or (i + batch_size) >= len(users):
            logging.info(f"Прогресс: {min(i + batch_size, len(users))}/{len(users)}, "
                        f"отправлено: {sent}, заблокировано: {blocked}, ошибок: {failed}")
    
    logging.info(f"Рассылка завершена. Итого: отправлено {sent}, "
                f"заблокировано {blocked}, ошибок {failed}")

# --- Функция для отправки напоминания одному пользователю ---
async def send_reminder_to_user(user_id):
    try:
        text = """<b>250 000 в 15 лет</b>
именно столько заработал Глеб всего за 1 месяц

<b>Глеб</b> пришёл ко мне на <b>обучение 1,5 месяца назад КЭШАП</b>
до этого он делал мелкие выручки за копейки и не понимал, где теряет деньги

мы вместе разобрали ошибки, я показал, как выстраивать прогрев, дал ему команду продажников и курировал каждую неделю

<b>результат?</b>
— 620.000₽ выручки
— 250.000₽ чистыми за 29 дней

(а если посчитать ещё не дойдущие платежи — почти 900.000₽)

да, парню 15 лет

если хочешь так же — жду тебя в КЭШАП
забирай место по кнопке ниже"""

        await bot.send_message(
            user_id,
            text,
            parse_mode="HTML",
            reply_markup=get_reminder_keyboard()
        )
        
        db.mark_reminder_sent(user_id)
        logging.info(f"Догрев отправлен пользователю {user_id}")
        return True
        
    except Exception as e:
        error_text = str(e).lower()
        if any(err in error_text for err in [
            "blocked by the user",
            "user is deactivated",
            "chat not found",
            "can't send to this user",
            "bot was blocked"
        ]):
            db.deactivate_user(user_id)
            logging.info(f"Пользователь {user_id} деактивирован (догрев)")
        else:
            logging.error(f"Ошибка отправки догрева {user_id}: {e}")
        return False

# --- Фоновая задача для проверки и отправки догревов ---
async def reminder_checker():
    while True:
        try:
            users_for_reminder = db.get_users_for_reminder()
            
            if users_for_reminder:
                logging.info(f"Найдено {len(users_for_reminder)} пользователей для догрева")
                
                for user_id in users_for_reminder:
                    await send_reminder_to_user(user_id)
                    await asyncio.sleep(0.5)
            
            await asyncio.sleep(60)
            
        except Exception as e:
            logging.error(f"Ошибка в reminder_checker: {e}")
            await asyncio.sleep(60)

# --- Обработчик /start ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user = message.from_user
    db.add_user(user.id, user.username, user.first_name)
    
    # 1. Сразу видео
    await message.answer_video(video=VIDEO_FILE_ID)
    
    # Ждем 2 секунды
    await asyncio.sleep(2)
    
    # 2. Текст с кнопкой "ХОЧУ ИЗУЧАТЬ AI"
    text1 = """<b>ПЛАН ВЫХОДА НА ПЕРВЫЕ 100-200к
в AI индустрии</b>

Внутри ролика рассказал:
• кто я такой и на чем делаю 400-500к/мес в 15
• про AI индустрию и что это такое
• основные направления индустрии
• как выйти на первые деньги в индустрии с полного 0

<i>Смотри теорию, фиксируй информацию и применяй на практике</i>"""
    
    await message.answer(text1, reply_markup=get_watch_keyboard(), parse_mode="HTML")

@dp.callback_query(F.data == "watch")
async def process_watch_callback(callback_query: types.CallbackQuery):
    await callback_query.answer()
    
    text2 = """<b>Кайф, я рад)</b>

Нажимай на кнопку и забирай часовую запись эфира, где я разобрал как проще всего выйти на первые 100-200к в AI с 0

Ставь на 1,5x скорость и впитывай🔥"""
    
    await callback_query.message.answer(text2, reply_markup=get_efir_keyboard(), parse_mode="HTML")

@dp.callback_query(F.data == "get_efir")
async def process_efir_callback(callback_query: types.CallbackQuery):
    await callback_query.answer()
    
    # Отправляем видео с подписью
    await callback_query.message.answer_video(
        video=EFIR_FILE_ID,
        caption="<b>Эфир 1</b> — запись",
        parse_mode="HTML"
    )

# --- Админ-команды ---
@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        total = db.get_stats()
        await message.answer(f"{total}")

@dp.message(Command("rasilka"))
async def cmd_rasilka(message: types.Message, state: FSMContext):
    if message.from_user.id == ADMIN_ID:
        help_text = """
📝 <b>Отправь сообщение для рассылки</b>

<b>Доступные теги форматирования HTML:</b>

• <b>жирный</b> - <code>&lt;b&gt;текст&lt;/b&gt;</code>
• <i>курсив</i> - <code>&lt;i&gt;текст&lt;/i&gt;</code>
• <u>подчеркнутый</u> - <code>&lt;u&gt;текст&lt;/u&gt;</code>
• <s>зачеркнутый</s> - <code>&lt;s&gt;текст&lt;/s&gt;</code>
• <span class="tg-spoiler">спойлер</span> - <code>&lt;span class="tg-spoiler"&gt;текст&lt;/span&gt;</code>
• <blockquote>цитата</blockquote> - <code>&lt;blockquote&gt;текст&lt;/blockquote&gt;</code>
• <a href="https://example.com">ссылка</a> - <code>&lt;a href="URL"&gt;текст&lt;/a&gt;</code>

<i>Пример: &lt;b&gt;Важное объявление!&lt;/b&gt;</i>

После отправки текста можно будет добавить кнопки"""
        
        await message.answer(help_text, parse_mode="HTML")
        await state.set_state(MailingStates.waiting_for_text)

# --- Обработчик получения текста рассылки ---
@dp.message(MailingStates.waiting_for_text)
async def process_mailing_text(message: types.Message, state: FSMContext):
    await state.update_data(message=message)
    await message.answer(
        "Добавить кнопку?",
        reply_markup=get_button_choice_keyboard()
    )
    await state.set_state(MailingStates.waiting_for_button)

# --- Обработчик выбора кнопки ---
@dp.callback_query(MailingStates.waiting_for_button)
async def process_button_choice(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    
    if callback.data == "add_button":
        await callback.message.answer("Текст кнопки:")
        await state.set_state(MailingStates.waiting_for_button_text)
    
    elif callback.data == "send_now":
        data = await state.get_data()
        original_message = data.get('message')
        await send_mailing_to_all(original_message)
        await state.clear()

# --- Обработчик текста первой кнопки ---
@dp.message(MailingStates.waiting_for_button_text)
async def process_button_text(message: types.Message, state: FSMContext):
    await state.update_data(button_text=message.text)
    await message.answer("Ссылка:")
    await state.set_state(MailingStates.waiting_for_button_url)

# --- Обработчик ссылки первой кнопки ---
@dp.message(MailingStates.waiting_for_button_url)
async def process_button_url(message: types.Message, state: FSMContext):
    url = message.text
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    await state.update_data(button_url=url)
    await message.answer(
        "Добавить вторую кнопку?",
        reply_markup=get_second_button_choice_keyboard()
    )
    await state.set_state(MailingStates.waiting_for_second_button)

# --- Обработчик выбора второй кнопки ---
@dp.callback_query(MailingStates.waiting_for_second_button)
async def process_second_button_choice(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    
    if callback.data == "add_second_button":
        await callback.message.answer("Текст второй кнопки:")
        await state.set_state(MailingStates.waiting_for_second_button_text)
    
    elif callback.data == "send_with_one":
        data = await state.get_data()
        original_message = data.get('message')
        button_text = data.get('button_text')
        button_url = data.get('button_url')
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=button_text, url=button_url)]
            ]
        )
        
        await send_mailing_to_all(original_message, keyboard)
        await state.clear()

# --- Обработчик текста второй кнопки ---
@dp.message(MailingStates.waiting_for_second_button_text)
async def process_second_button_text(message: types.Message, state: FSMContext):
    await state.update_data(second_button_text=message.text)
    await message.answer("Ссылка для второй кнопки:")
    await state.set_state(MailingStates.waiting_for_second_button_url)

# --- Обработчик ссылки второй кнопки ---
@dp.message(MailingStates.waiting_for_second_button_url)
async def process_second_button_url(message: types.Message, state: FSMContext):
    url = message.text
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    data = await state.get_data()
    original_message = data.get('message')
    button_text = data.get('button_text')
    button_url = data.get('button_url')
    second_button_text = data.get('second_button_text')
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=button_text, url=button_url)],
            [InlineKeyboardButton(text=second_button_text, url=url)]
        ]
    )
    
    await send_mailing_to_all(original_message, keyboard)
    await state.clear()

async def main():
    db.connect()
    
    # Запускаем фоновую задачу для проверки догревов
    asyncio.create_task(reminder_checker())
    
    print("\n" + "="*60)
    print("🚀 БОТ ЗАПУЩЕН")
    print("="*60)
    print(f"👑 Админ ID: {ADMIN_ID}")
    print("="*60)
    print("📢 Команды админа:")
    print("  /stats - статистика (только число)")
    print("  /rasilka - рассылка с кнопками и подсказкой по тегам")
    print("="*60)
    print("⏰ Догревы: автоматически через 24 часа после /start")
    print("="*60 + "\n")
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n❌ Бот остановлен")
        db.close()