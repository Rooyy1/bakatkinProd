import os
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import aiosqlite
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ChatJoinRequest
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.bot import DefaultBotProperties
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from dotenv import load_dotenv

load_dotenv()

# ========================================================================
# 📁 ПУТЬ К БАЗЕ ДАННЫХ
# ========================================================================
DB_PATH = '/data/bot_database.db'
print(f"📁 Путь к базе данных: {DB_PATH}")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID_RAW = os.getenv('ADMIN_ID')

if not BOT_TOKEN:
    raise RuntimeError("❌ Не задан BOT_TOKEN в переменных окружения (.env)")
if not ADMIN_ID_RAW:
    raise RuntimeError("❌ Не задан ADMIN_ID в переменных окружения (.env)")
ADMIN_ID = int(ADMIN_ID_RAW)

# ========================================================================
# 🗂 FILE_ID И ССЫЛКИ
# ========================================================================
WELCOME_PHOTO_ID_1 = "AgACAgIAAxkBAAIr9GpHsdKMgJ8UlxuUttQT-hTYv1dXAAIMGmsbEO8wSgABoZfmQPxfRAEAAwIAA3kAAzwE"
WELCOME_PHOTO_ID_2 = "AgACAgIAAxkBAAIr9mpHseKLh3st5D2u6h7L9xdlfH9lAAINGmsbEO8wSkt5SmjIn7F6AQADAgADeQADPAQ"
VIDEO_FILE_ID = "BAACAgIAAxkBAAIsCGpHtmIi4bAP3EUdC-eC1SWnWvdqAAIVkAACN0zASfSfPr_5LMWjPAQ"
LESSON_2_VIDEO_ID = "BAACAgIAAxkBAAIsBGpHs8IC46o7TVjYgcetq5wNjJNAAAJshwACKihgSvQj7Ke38kIwPAQ"
PRACTICE_PHOTO_ID = "AgACAgIAAxkBAAIr-GpHsmqMXkICmIBNhl76OxfYTk_BAAKkG2sb4yowSsEb41WVmvA9AQADAgADeQADPAQ"
LESSON_1_VOICE_ID = "AwACAgIAAxkBAAIsBmpHs935yzvAwmjQor91iu2ERsvEAAJzkQACR2NYStrXr8wnQA3aPAQ"
REMINDER_PHOTO_1_ID = "AgACAgIAAxkBAAIsAAFqR7Okr4AlszGevrsJLnUWY6s38wACDxprG-MqQEpL9rjfUhpcrgEAAwIAA3kAAzwE"
REMINDER_PHOTO_2_ID = "AgACAgIAAxkBAAIsAmpHs676r-pNRthYxDxwLJ26pd0LAAIRGmsb4ypASiuCuYCe_STuAQADAgADeQADPAQ"
REMINDER_PHOTO_3_ID = "AgACAgIAAxkBAAIr9mpHseKLh3st5D2u6h7L9xdlfH9lAAINGmsbEO8wSkt5SmjIn7F6AQADAgADeQADPAQ"

CONSULTATION_LINK = "https://t.me/m/hYepSQG7ZDEy"
CHANNEL_INVITE_LINK = "https://t.me/+Rcgrh5DV5Tw4ZjEy"
CHANNEL_ID = -1003818945402

# ========================================================================
# ⏰ ИНТЕРВАЛЫ ДОГРЕВОВ
# ========================================================================
TEST_MODE = False  # ← МЕНЯЕМ НА False

if TEST_MODE:
    REMINDER_1_DELAY = 30
    REMINDER_2_DELAY = 50
    REMINDER_3_DELAY = 70
    REMINDER_4_DELAY = 60
    REMINDER_5_DELAY = 30
    CHANNEL_WAIT_TIMEOUT = 60
    CHECK_INTERVAL = 5
else:
    REMINDER_1_DELAY = 3 * 3600      # 3 часа
    REMINDER_2_DELAY = 3 * 3600      # 3 часа
    REMINDER_3_DELAY = 3 * 3600      # 3 часа
    REMINDER_4_DELAY = 24 * 3600     # 24 часа
    REMINDER_5_DELAY = 30 * 60       # 30 минут
    CHANNEL_WAIT_TIMEOUT = 3 * 86400 # 3 дня ждём вступление в канал
    CHECK_INTERVAL = 60              # проверка раз в минуту

session = AiohttpSession(timeout=30)
bot = Bot(
    token=BOT_TOKEN,
    session=session,
    default=DefaultBotProperties(parse_mode="HTML")
)
dp = Dispatcher()


def now_str() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def parse_dt(value: str) -> datetime:
    return datetime.strptime(value, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)


# --- Состояния ---
class MailingStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_button = State()
    waiting_for_button_text = State()
    waiting_for_button_url = State()
    waiting_for_second_button = State()
    waiting_for_second_button_text = State()
    waiting_for_second_button_url = State()


class SurveyStates(StatesGroup):
    waiting_for_q1 = State()
    waiting_for_q2 = State()


# ========================================================================
# 🗄 БАЗА ДАННЫХ
# ========================================================================
class Database:
    def __init__(self):
        self.conn: aiosqlite.Connection | None = None

    async def connect(self):
        self.conn = await aiosqlite.connect(DB_PATH)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute('PRAGMA journal_mode=WAL')
        await self.create_tables()
        await self.migrate_tables()
        await self.conn.execute('CREATE INDEX IF NOT EXISTS idx_lesson1 ON users(lesson_1_sent_at, reminder_1_sent)')
        await self.conn.execute('CREATE INDEX IF NOT EXISTS idx_lesson2 ON users(lesson_2_sent_at, reminder_2_sent)')
        await self.conn.execute('CREATE INDEX IF NOT EXISTS idx_lesson3 ON users(lesson_3_sent_at, reminder_3_sent)')
        await self.conn.execute('CREATE INDEX IF NOT EXISTS idx_awaiting ON users(awaiting_channel_since, practice_sent)')
        await self.conn.commit()
        print("✅ База данных подключена (aiosqlite)")

    async def create_tables(self):
        await self.conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_activity TIMESTAMP,
                is_active INTEGER DEFAULT 1,
                started_at TIMESTAMP,
                survey_q1 TEXT,
                survey_q2 TEXT,
                lesson_1_sent_at TIMESTAMP,
                lesson_2_sent_at TIMESTAMP,
                lesson_3_sent_at TIMESTAMP,
                lesson_3_received_at TIMESTAMP,
                lesson_complete_at TIMESTAMP,
                awaiting_channel_since TIMESTAMP,
                practice_sent INTEGER DEFAULT 0,
                reminder_1_sent INTEGER DEFAULT 0,
                reminder_1_sent_at TIMESTAMP,
                reminder_2_sent INTEGER DEFAULT 0,
                reminder_3_sent INTEGER DEFAULT 0,
                reminder_4_sent INTEGER DEFAULT 0,
                reminder_5_sent INTEGER DEFAULT 0
            )
        ''')
        await self.conn.execute('''
            CREATE TABLE IF NOT EXISTS channel_subscribers (
                user_id INTEGER PRIMARY KEY,
                subscribed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
            )
        ''')
        await self.conn.commit()

    async def migrate_tables(self):
        cursor = await self.conn.execute("PRAGMA table_info(users)")
        columns = [row[1] for row in await cursor.fetchall()]

        needed = {
            'survey_q1': 'TEXT',
            'survey_q2': 'TEXT',
            'lesson_1_sent_at': 'TIMESTAMP',
            'lesson_2_sent_at': 'TIMESTAMP',
            'lesson_3_sent_at': 'TIMESTAMP',
            'lesson_3_received_at': 'TIMESTAMP',
            'lesson_complete_at': 'TIMESTAMP',
            'awaiting_channel_since': 'TIMESTAMP',
            'practice_sent': 'INTEGER DEFAULT 0',
            'reminder_1_sent': 'INTEGER DEFAULT 0',
            'reminder_1_sent_at': 'TIMESTAMP',
            'reminder_2_sent': 'INTEGER DEFAULT 0',
            'reminder_3_sent': 'INTEGER DEFAULT 0',
            'reminder_4_sent': 'INTEGER DEFAULT 0',
            'reminder_5_sent': 'INTEGER DEFAULT 0',
        }
        for col, col_type in needed.items():
            if col not in columns:
                await self.conn.execute(f'ALTER TABLE users ADD COLUMN {col} {col_type}')
                print(f"✅ Добавлена колонка {col}")
        await self.conn.commit()

    async def add_user(self, user_id, username, first_name):
        try:
            now = now_str()
            await self.conn.execute('''
                INSERT INTO users (user_id, username, first_name, last_activity, started_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    last_activity = excluded.last_activity,
                    started_at = COALESCE(users.started_at, excluded.started_at)
            ''', (user_id, username, first_name, now, now))
            await self.conn.commit()
        except Exception as e:
            logging.error(f"Ошибка добавления пользователя {user_id}: {e}")

    async def update_survey_answers(self, user_id, q1, q2):
        await self.conn.execute('UPDATE users SET survey_q1 = ?, survey_q2 = ? WHERE user_id = ?', (q1, q2, user_id))
        await self.conn.commit()

    async def get_all_users(self):
        cursor = await self.conn.execute('SELECT user_id FROM users WHERE is_active = 1')
        return [row[0] for row in await cursor.fetchall()]

    async def get_user_progress(self, user_id):
        cursor = await self.conn.execute('''
        SELECT lesson_2_sent_at, lesson_3_sent_at, lesson_3_received_at, lesson_complete_at
        FROM users WHERE user_id = ?
    ''', (user_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def update_lesson_1_sent(self, user_id):
        await self.conn.execute('UPDATE users SET lesson_1_sent_at = ? WHERE user_id = ?', (now_str(), user_id))
        await self.conn.commit()

    async def update_lesson_2_sent(self, user_id):
        await self.conn.execute('UPDATE users SET lesson_2_sent_at = ? WHERE user_id = ?', (now_str(), user_id))
        await self.conn.commit()

    async def update_lesson_3_sent(self, user_id):
        await self.conn.execute('UPDATE users SET lesson_3_sent_at = ? WHERE user_id = ?', (now_str(), user_id))
        await self.conn.commit()

    async def update_lesson_3_received(self, user_id):
        await self.conn.execute('UPDATE users SET lesson_3_received_at = ? WHERE user_id = ?', (now_str(), user_id))
        await self.conn.commit()

    async def mark_lesson_complete(self, user_id):
        await self.conn.execute('UPDATE users SET lesson_complete_at = ? WHERE user_id = ?', (now_str(), user_id))
        await self.conn.commit()

    async def set_awaiting_channel(self, user_id):
        await self.conn.execute(
            'UPDATE users SET awaiting_channel_since = ?, practice_sent = 0 WHERE user_id = ?',
            (now_str(), user_id)
        )
        await self.conn.commit()

    async def get_users_awaiting_channel(self):
        cursor = await self.conn.execute('''
            SELECT user_id, awaiting_channel_since FROM users
            WHERE is_active = 1 AND awaiting_channel_since IS NOT NULL AND practice_sent = 0
        ''')
        return await cursor.fetchall()

    async def mark_practice_sent(self, user_id, expired=False):
        await self.conn.execute(
            'UPDATE users SET practice_sent = ? WHERE user_id = ?',
            (-1 if expired else 1, user_id)
        )
        await self.conn.commit()

    async def mark_reminder_1_sent(self, user_id):
        await self.conn.execute(
            'UPDATE users SET reminder_1_sent = 1, reminder_1_sent_at = ? WHERE user_id = ?',
            (now_str(), user_id)
        )
        await self.conn.commit()

    async def mark_reminder_2_sent(self, user_id):
        await self.conn.execute('UPDATE users SET reminder_2_sent = 1 WHERE user_id = ?', (user_id,))
        await self.conn.commit()

    async def mark_reminder_3_sent(self, user_id):
        await self.conn.execute('UPDATE users SET reminder_3_sent = 1 WHERE user_id = ?', (user_id,))
        await self.conn.commit()

    async def mark_reminder_4_sent(self, user_id):
        await self.conn.execute('UPDATE users SET reminder_4_sent = 1 WHERE user_id = ?', (user_id,))
        await self.conn.commit()

    async def mark_reminder_5_sent(self, user_id):
        await self.conn.execute('UPDATE users SET reminder_5_sent = 1 WHERE user_id = ?', (user_id,))
        await self.conn.commit()

    async def get_users_for_reminder_1(self):
        time_ago = (datetime.now(timezone.utc) - timedelta(seconds=REMINDER_1_DELAY)).strftime('%Y-%m-%d %H:%M:%S')
        cursor = await self.conn.execute('''
            SELECT user_id FROM users
            WHERE is_active = 1
            AND lesson_1_sent_at IS NOT NULL
            AND lesson_1_sent_at <= ?
            AND reminder_1_sent = 0
            AND lesson_2_sent_at IS NULL
            AND user_id NOT IN (SELECT user_id FROM channel_subscribers)
        ''', (time_ago,))
        return [row[0] for row in await cursor.fetchall()]

    async def get_users_for_reminder_2(self):
        time_ago = (datetime.now(timezone.utc) - timedelta(seconds=REMINDER_2_DELAY)).strftime('%Y-%m-%d %H:%M:%S')
        cursor = await self.conn.execute('''
            SELECT user_id FROM users
            WHERE is_active = 1
            AND lesson_2_sent_at IS NOT NULL
            AND lesson_2_sent_at <= ?
            AND reminder_2_sent = 0
            AND lesson_3_sent_at IS NULL
        ''', (time_ago,))
        return [row[0] for row in await cursor.fetchall()]

    async def get_users_for_reminder_3(self):
        """Догрев 3: пользователь получил урок 2, но ещё НЕ открывал урок 3
        (lesson_3_received_at ставится при клике 'Следующий урок' после урока 2)."""
        time_ago = (datetime.now(timezone.utc) - timedelta(seconds=REMINDER_3_DELAY)).strftime('%Y-%m-%d %H:%M:%S')
        cursor = await self.conn.execute('''
            SELECT user_id FROM users
            WHERE is_active = 1
            AND lesson_3_sent_at IS NOT NULL
            AND lesson_3_sent_at <= ?
            AND reminder_3_sent = 0
            AND lesson_3_received_at IS NULL
        ''', (time_ago,))
        return [row[0] for row in await cursor.fetchall()]

    async def get_users_for_reminder_4(self):
        time_ago = (datetime.now(timezone.utc) - timedelta(seconds=REMINDER_4_DELAY)).strftime('%Y-%m-%d %H:%M:%S')
        cursor = await self.conn.execute('''
            SELECT u.user_id FROM users u
            WHERE u.is_active = 1
            AND u.reminder_1_sent_at IS NOT NULL
            AND u.reminder_1_sent_at <= ?
            AND u.reminder_4_sent = 0
            AND u.user_id NOT IN (SELECT user_id FROM channel_subscribers)
        ''', (time_ago,))
        return [row[0] for row in await cursor.fetchall()]

    async def get_users_for_reminder_5(self):
        """Догрев 5: пользователь получил урок 3, но ещё НЕ завершил курс"""
        time_ago = (datetime.now(timezone.utc) - timedelta(seconds=REMINDER_5_DELAY)).strftime('%Y-%m-%d %H:%M:%S')
        cursor = await self.conn.execute('''
            SELECT user_id FROM users
            WHERE is_active = 1
            AND lesson_complete_at IS NULL
            AND reminder_5_sent = 0
            AND lesson_3_received_at IS NOT NULL
            AND lesson_3_received_at <= ?
        ''', (time_ago,))
        return [row[0] for row in await cursor.fetchall()]

    async def get_stats(self):
        cursor = await self.conn.execute('SELECT COUNT(*) FROM users WHERE is_active = 1')
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def deactivate_user(self, user_id):
        await self.conn.execute('UPDATE users SET is_active = 0 WHERE user_id = ?', (user_id,))
        await self.conn.commit()
        logging.info(f"Пользователь {user_id} деактивирован")

    async def add_channel_subscriber(self, user_id):
        await self.conn.execute('''
            INSERT OR REPLACE INTO channel_subscribers (user_id, subscribed_at) VALUES (?, ?)
        ''', (user_id, now_str()))
        await self.conn.commit()
        logging.info(f"✅ Пользователь {user_id} добавлен в channel_subscribers")

    async def get_all_users_full(self):
        cursor = await self.conn.execute('''
            SELECT user_id, username, first_name, is_active, started_at, survey_q1, survey_q2
            FROM users ORDER BY started_at DESC
        ''')
        return await cursor.fetchall()

    async def close(self):
        if self.conn:
            await self.conn.close()


db = Database()


# --- Клавиатуры ---
def get_lesson_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Смотреть урок", callback_data="watch_lesson")]])


def get_channel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Попасть в приватный телеграм канал", url=CHANNEL_INVITE_LINK)]])


def get_channel_keyboard_with_check():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Попасть в приватный телеграм канал", url=CHANNEL_INVITE_LINK)],
        [InlineKeyboardButton(text="✅ Я уже подписан", callback_data="already_subscribed")]
    ])


def get_open_lesson_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть урок", callback_data="lesson_1_opened")]])


def get_next_lesson_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Следующий урок ➡️", callback_data="lesson_2_opened")]])


def get_next_to_lesson_3_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Следующий урок ➡️", callback_data="lesson_3_opened")]])


def get_final_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Я изучил все 3 урока", callback_data="lesson_complete")]])


def get_consultation_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Оставить заявку", url=CONSULTATION_LINK)]])


def get_button_choice_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Добавить кнопку", callback_data="add_button")],
        [InlineKeyboardButton(text="🚀 Отправить сразу", callback_data="send_now")]
    ])


def get_second_button_choice_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Добавить вторую кнопку", callback_data="add_second_button")],
        [InlineKeyboardButton(text="🚀 Отправить с одной", callback_data="send_with_one")]
    ])


# --- Общая обработка ошибок отправки ---
async def _handle_send_error(user_id: int, e: Exception, context: str) -> bool:
    if isinstance(e, TelegramForbiddenError):
        await db.deactivate_user(user_id)
        logging.info(f"Пользователь {user_id} деактивирован ({context}: бот заблокирован)")
        return False
    if isinstance(e, TelegramRetryAfter):
        logging.warning(f"FloodWait {e.retry_after}s ({context}, user {user_id})")
        await asyncio.sleep(e.retry_after)
        return True
    if isinstance(e, TelegramBadRequest):
        text = str(e).lower()
        if any(s in text for s in ["chat not found", "user is deactivated", "can't send to this user"]):
            await db.deactivate_user(user_id)
            logging.info(f"Пользователь {user_id} деактивирован ({context}: {e})")
            return False
    logging.error(f"Ошибка отправки ({context}) пользователю {user_id}: {e}")
    return False


# --- Функции отправки (рассылка) ---
async def send_message_to_user(bot, user_id, message, reply_markup=None):
    try:
        if message.text:
            await bot.send_message(user_id, message.text, parse_mode="HTML", reply_markup=reply_markup)
        elif message.photo:
            await bot.send_photo(user_id, message.photo[-1].file_id, caption=message.caption, parse_mode="HTML" if message.caption else None, reply_markup=reply_markup)
        elif message.video:
            await bot.send_video(user_id, message.video.file_id, caption=message.caption, parse_mode="HTML" if message.caption else None, reply_markup=reply_markup)
        elif message.video_note:
            await bot.send_video_note(user_id, message.video_note.file_id)
        elif message.document:
            await bot.send_document(user_id, message.document.file_id, caption=message.caption, parse_mode="HTML" if message.caption else None, reply_markup=reply_markup)
        elif message.audio:
            await bot.send_audio(user_id, message.audio.file_id, caption=message.caption, parse_mode="HTML" if message.caption else None, reply_markup=reply_markup)
        elif message.voice:
            await bot.send_voice(user_id, message.voice.file_id, caption=message.caption, parse_mode="HTML" if message.caption else None, reply_markup=reply_markup)
        elif message.sticker:
            await bot.send_sticker(user_id, message.sticker.file_id)
        elif message.animation:
            await bot.send_animation(user_id, message.animation.file_id, caption=message.caption, parse_mode="HTML" if message.caption else None, reply_markup=reply_markup)
        return True
    except TelegramRetryAfter as e:
        return f"flood_{e.retry_after}"
    except (TelegramForbiddenError, TelegramBadRequest) as e:
        await _handle_send_error(user_id, e, "рассылка")
        return "blocked"
    except Exception as e:
        logging.error(f"Ошибка рассылки {user_id}: {e}")
        return False


async def send_mailing_to_all(message: types.Message, reply_markup=None):
    try:
        users = await db.get_all_users()
        if not users:
            return
        logging.info(f"Начало рассылки для {len(users)} пользователей")
        batch_size = 20
        sent = blocked = failed = 0
        for i in range(0, len(users), batch_size):
            batch = users[i:i + batch_size]
            results = await asyncio.gather(
                *[send_message_to_user(bot, uid, message, reply_markup) for uid in batch],
                return_exceptions=True
            )
            for result in results:
                if result is True:
                    sent += 1
                elif result == "blocked":
                    blocked += 1
                elif isinstance(result, str) and result.startswith("flood_"):
                    try:
                        await asyncio.sleep(int(result.split("_")[1]))
                    except Exception:
                        await asyncio.sleep(1)
                    failed += 1
                else:
                    failed += 1
            await asyncio.sleep(1.0)
            if (i + batch_size) % 100 == 0 or (i + batch_size) >= len(users):
                logging.info(f"Прогресс: {min(i + batch_size, len(users))}/{len(users)}, "
                             f"отправлено: {sent}, заблокировано: {blocked}, ошибок: {failed}")
        logging.info(f"Рассылка завершена. Итого: отправлено {sent}, заблокировано {blocked}, ошибок {failed}")
    except Exception as e:
        logging.error(f"Критическая ошибка в рассылке: {e}")


# --- Догревы ---
async def send_reminder_1_to_user(user_id):
    try:
        progress = await db.get_user_progress(user_id)
        if not progress:
            return False
        if progress['lesson_2_sent_at'] is not None or await is_user_subscribed(user_id):
            await db.mark_reminder_1_sent(user_id)
            return True

        text = """<b>Ты уже получил доступ к вводному уроку, но пока не начал( </b>

На его просмотр уйдет около 20 минут, а после него откроется бесплатная часть AI Academy с дополнительными материалами и доступом к закрытому Telegram-каналу

Когда будешь готов — просто продолжай с того места, где остановился."""
        await bot.send_photo(chat_id=user_id, photo=REMINDER_PHOTO_1_ID, caption=text,
                              parse_mode="HTML", reply_markup=get_channel_keyboard_with_check())
        await db.mark_reminder_1_sent(user_id)
        logging.info(f"Догрев 1 отправлен пользователю {user_id}")
        return True
    except Exception as e:
        await _handle_send_error(user_id, e, "догрев 1")
        return False


async def send_reminder_2_to_user(user_id):
    try:
        progress = await db.get_user_progress(user_id)
        if not progress:
            return False
        if progress['lesson_3_sent_at'] is not None:
            await db.mark_reminder_2_sent(user_id)
            return True

        text = """<b>Ты уже познакомился с тем, как устроен рынок AI</b>

Следующий урок поможет разобраться, почему одни специалисты быстро находят клиентов, а другие остаются без заказов, даже имея хорошие навыки

Продолжай обучение — впереди один из самых важных материалов бесплатной программы"""
        await bot.send_photo(chat_id=user_id, photo=REMINDER_PHOTO_2_ID, caption=text,
                              parse_mode="HTML", reply_markup=get_next_lesson_keyboard())
        await db.mark_reminder_2_sent(user_id)
        logging.info(f"Догрев 2 отправлен пользователю {user_id}")
        return True
    except Exception as e:
        await _handle_send_error(user_id, e, "догрев 2")
        return False


async def send_reminder_3_to_user(user_id):
    try:
        progress = await db.get_user_progress(user_id)
        if not progress:
            return False
        if progress['lesson_3_received_at'] is not None:
            await db.mark_reminder_3_sent(user_id)
            return True

        text = """<b>Остался последний материал и именно он отвечает на вопрос, который возникает почти у каждого новичка:
«Где брать первых клиентов?»</b>

После его изучения ты завершишь вводную программу и сможешь попасть на закрытый День открытых дверей AI Academy"""

        await bot.send_photo(
            chat_id=user_id, 
            photo=REMINDER_PHOTO_3_ID, 
            caption=text,
            parse_mode="HTML", 
            reply_markup=get_next_to_lesson_3_keyboard()
        )
        await db.mark_reminder_3_sent(user_id)
        logging.info(f"Догрев 3 отправлен пользователю {user_id}")
        return True
    except Exception as e:
        await _handle_send_error(user_id, e, "догрев 3")
        return False


async def send_reminder_4_to_user(user_id):
    try:
        if await is_user_subscribed(user_id):
            await db.mark_reminder_4_sent(user_id)
            return True

        text = """<b>Сейчас доступ к следующему этапу по-прежнему ждет тебя</b>

После вступления в закрытый Telegram-канал ты получишь доступ к бесплатной части AI Academy и сможешь продолжить обучение

Не откладывай — продолжить можно в любой момент."""
        await bot.send_message(chat_id=user_id, text=text, parse_mode="HTML", reply_markup=get_channel_keyboard_with_check())
        await db.mark_reminder_4_sent(user_id)
        logging.info(f"Догрев 4 отправлен пользователю {user_id}")
        return True
    except Exception as e:
        await _handle_send_error(user_id, e, "догрев 4")
        return False


async def send_reminder_5_to_user(user_id):
    try:
        progress = await db.get_user_progress(user_id)
        if not progress:
            return False
        if progress['lesson_complete_at'] is not None:
            await db.mark_reminder_5_sent(user_id)
            logging.info(f"ℹ️ Догрев 5 пропущен для {user_id} (курс уже завершен)")
            return True

        text = """<b>Ты уже на финишной прямой!</b>

Когда закончишь изучать материал 3-го урока — нажимай кнопку и забирай приятный бонус 🎁"""

        await bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode="HTML",
            reply_markup=get_final_keyboard()
        )
        await db.mark_reminder_5_sent(user_id)
        logging.info(f"Догрев 5 отправлен пользователю {user_id}")
        return True
    except Exception as e:
        await _handle_send_error(user_id, e, "догрев 5")
        return False


async def reminder_checker():
    while True:
        try:
            for getter, sender, label in [
                (db.get_users_for_reminder_1, send_reminder_1_to_user, "1"),
                (db.get_users_for_reminder_2, send_reminder_2_to_user, "2"),
                (db.get_users_for_reminder_3, send_reminder_3_to_user, "3"),
                (db.get_users_for_reminder_4, send_reminder_4_to_user, "4"),
                (db.get_users_for_reminder_5, send_reminder_5_to_user, "5"),
            ]:
                users = await getter()
                if users:
                    logging.info(f"Найдено {len(users)} пользователей для догрева {label}")
                    for user_id in users:
                        try:
                            await sender(user_id)
                        except Exception as e:
                            logging.error(f"Ошибка при отправке догрева {label} пользователю {user_id}: {e}")
                        await asyncio.sleep(0.5)
            await asyncio.sleep(CHECK_INTERVAL)
        except Exception as e:
            logging.error(f"Ошибка в reminder_checker: {e}")
            await asyncio.sleep(CHECK_INTERVAL)


# --- Проверка подписки ---
async def is_user_subscribed(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        logging.debug(f"Проверка подписки {user_id}: {e}")
        return False


async def send_practice_post(chat_id: int):
    try:
        practice_text = """<b>Отлично, теперь начинается практика</b>

Я подготовил для тебя несколько уроков, которые помогут не просто понять, что такое AI, а увидеть, как на этом реально начинают зарабатывать

Начнем с самого важного — выбора направления. Именно от этого зависит, насколько быстро ты выйдешь на первые деньги.

👇 Открывай первый урок"""
        await bot.send_photo(chat_id=chat_id, photo=PRACTICE_PHOTO_ID, caption=practice_text,
                              parse_mode="HTML", reply_markup=get_open_lesson_keyboard())
        logging.info(f"✅ Пост практики отправлен пользователю {chat_id}")
    except Exception as e:
        logging.error(f"Ошибка отправки поста практики для {chat_id}: {e}")


async def channel_wait_checker():
    """Проверяет пользователей, ожидающих подписку на канал.
    Только отмечает таймаут, НЕ отправляет пост автоматически!"""
    while True:
        try:
            pending = await db.get_users_awaiting_channel()
            for row in pending:
                user_id = row['user_id']
                awaiting_since = row['awaiting_channel_since']
                try:
                    # Проверяем, не истекло ли время ожидания
                    elapsed = (datetime.now(timezone.utc) - parse_dt(awaiting_since)).total_seconds()
                    if elapsed > CHANNEL_WAIT_TIMEOUT:
                        await db.mark_practice_sent(user_id, expired=True)
                        logging.info(f"❌ {user_id} не подписался за отведенное время, ожидание отменено")
                except Exception as e:
                    logging.error(f"Ошибка проверки ожидания канала для {user_id}: {e}")
                await asyncio.sleep(0.3)
            await asyncio.sleep(CHECK_INTERVAL)
        except Exception as e:
            logging.error(f"Ошибка в channel_wait_checker: {e}")
            await asyncio.sleep(CHECK_INTERVAL)


async def start_survey(chat_id: int, state: FSMContext):
    try:
        await bot.send_message(
            chat_id,
            "<b>Перед тем как получить бесплатный мини курс, ответь на 2 простых вопроса 🙏</b>\n\n"
            "(это нужно чтобы мы лучше понимали какие уроки в будущем добавлять в бота)\n\n"
            "<b>1. Расскажи вкратце о себе (имя, возраст, где живешь, чем занимаешься, какие есть цели?)</b>",
            parse_mode="HTML"
        )
        await state.set_state(SurveyStates.waiting_for_q1)
    except Exception as e:
        logging.error(f"Ошибка запуска опросника для чата {chat_id}: {e}")


# --- Обработчики ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user = message.from_user
    await db.add_user(user.id, user.username, user.first_name)

    welcome_text = """<b>Добро пожаловать 👋

Ты попал в бесплатное мини-обучение по заработку на AI</b>

Я собрал его для тех, кто хочет разобраться в нейросетях не ради интереса, а чтобы выйти на первые стабильные $1–2k в месяц.

<b>Внутри тебя ждут:</b>

<blockquote>• пошаговый урок, с которого стоит начать;
• мой эфир с более глубоким разбором заработка на AI;
• доступ в закрытый Telegram-канал с дополнительными материалами, которых нет в открытом доступе.</blockquote>

Неважно, полный ты новичок или уже пробовал работать с нейросетями — здесь ты поймешь, какие навыки действительно оплачиваются и по какой стратегии быстрее всего выйти на первые деньги

<b>Нажимай кнопку ниже и начинай первый урок</b>"""

    try:
        await message.answer_photo(photo=WELCOME_PHOTO_ID_1, caption=welcome_text,
                                    parse_mode="HTML", reply_markup=get_lesson_keyboard())
    except Exception as e:
        logging.error(f"Ошибка отправки приветственного фото: {e}")
        await message.answer(welcome_text, parse_mode="HTML", reply_markup=get_lesson_keyboard())


@dp.callback_query(F.data == "watch_lesson")
async def process_watch_lesson(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await start_survey(callback.message.chat.id, state)


@dp.message(SurveyStates.waiting_for_q1)
async def process_q1(message: types.Message, state: FSMContext):
    await state.update_data(q1=message.text)
    await message.answer(
        "<b>Супер, спасибо, остался последний вопрос 🤝</b>\n\n"
        "<b>2. Есть ли у тебя опыт в каких-то онлайн направлениях и сферах? Возможно уже текущий доход?</b>",
        parse_mode="HTML"
    )
    await state.set_state(SurveyStates.waiting_for_q2)


@dp.message(SurveyStates.waiting_for_q2)
async def process_q2(message: types.Message, state: FSMContext):
    user = message.from_user
    data = await state.get_data()
    q1 = data.get('q1')
    q2 = message.text

    try:
        await db.update_survey_answers(user.id, q1, q2)
    except Exception as e:
        logging.error(f"Ошибка сохранения ответов: {e}")

    username = f"@{user.username}" if user.username else "не указан"
    report = (f"<b>НОВАЯ ЗАЯВКА</b>\n\n"
              f"Username: {username}\n\n"
              f"1. Расскажи вкратце о себе (имя, возраст, где живешь, чем занимаешься, какие есть цели?)\n{q1}\n\n"
              f"2. Есть ли у тебя опыт в каких-то онлайн направлениях и сферах?\nВозможно уже текущий доход?\n{q2}")
    try:
        await bot.send_message(ADMIN_ID, report, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Ошибка отправки заявки админу: {e}")

    await state.clear()

    try:
        intro_text = """<b>Вводный урок — первый шаг к 1$-2k/мес на AI</b>

Теперь ты понимаешь, как устроена AI-индустрия, где находятся деньги и какие направления сегодня действительно востребованы

<b>Следующий этап я решил сделать в закрытом Telegram-канале</b>

<i>Там я регулярно публикую:</i>
<blockquote>• разборы AI-инструментов;
• закрытые эфиры;
• кейсы учеников;
• дополнительные материалы и инструкции</blockquote>

После вступления тебе сразу откроются 3 бесплатных урока по самым востребованным направлениям AI, чтобы ты смог понять, какое из них подойдет именно тебе"""

        await message.answer_video(video=VIDEO_FILE_ID, caption=intro_text,
                                    parse_mode="HTML", reply_markup=get_channel_keyboard_with_check())

        await db.update_lesson_1_sent(user.id)

        # ✅ Если пользователь уже подписан - удаляем из channel_subscribers
        # Это нужно чтобы он не получил пост автоматически, а нажал "Я уже подписан"
        if await is_user_subscribed(user.id):
            await db.conn.execute('DELETE FROM channel_subscribers WHERE user_id = ?', (user.id,))
            await db.conn.commit()
            logging.info(f"ℹ️ Пользователь {user.id} уже был подписан, удален из channel_subscribers")

        # Ставим в ожидание для всех пользователей
        await db.set_awaiting_channel(user.id)

    except Exception as e:
        logging.error(f"Ошибка отправки вводного урока: {e}")


@dp.callback_query(F.data == "already_subscribed")
async def process_already_subscribed(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    
    if await is_user_subscribed(user_id):
        # Добавляем в базу подписчиков
        await db.add_channel_subscriber(user_id)
        
        # Отправляем пост практики
        await send_practice_post(user_id)
        await db.mark_practice_sent(user_id)
        logging.info(f"✅ Пользователь {user_id} подтвердил подписку, отправлен пост практики")
    else:
        await callback.message.answer(
            "❌ Мы не нашли вас в канале. Пожалуйста, сначала подпишитесь по кнопке выше, "
            "а затем нажмите 'Я уже подписан'",
            reply_markup=get_channel_keyboard_with_check()
        )


@dp.callback_query(F.data == "lesson_1_opened")
async def process_lesson_1(callback: types.CallbackQuery):
    await callback.answer()
    await db.update_lesson_2_sent(callback.from_user.id)

    lesson1_text = """<b>Урок 1. Рынок Digital & AI</b>

<b>Начнем с самого важного: большинство думают, что заработать в AI можно только после нескольких месяцев обучения, хотя на практике это не так</b>

Главное — понять, где находятся деньги и почему одни специалисты берут 20 тысяч за проект, а другие — 200+ тысяч. Именно с этого начинается академия (далее отправлю тебе первый урок второго модуля моей платной AI академии)

Посмотри следующий урок и переходи дальше"""

    await callback.message.answer_voice(voice=LESSON_1_VOICE_ID, caption=lesson1_text,
                                         parse_mode="HTML", reply_markup=get_next_lesson_keyboard())


@dp.callback_query(F.data == "lesson_2_opened")
async def process_lesson_2(callback: types.CallbackQuery):
    await callback.answer()
    await db.update_lesson_3_sent(callback.from_user.id)

    lesson2_text = """<b>Урок 2. Позиционирование и упаковка</b>

В этом уроке ты разберешься:
<blockquote>• почему позиционирование напрямую влияет на доход;
• как определить своего идеального клиента;
• как выбрать нишу, в которой будет проще получать заказы;
• как грамотно упаковать себя, чтобы клиенты сами видели ценность твоей работы.</blockquote>

Это фундамент, без которого практически невозможно стабильно продавать свои услуги

<b>После просмотра тебя ждет заключительный материал</b>"""

    await callback.message.answer_video(video=LESSON_2_VIDEO_ID, caption=lesson2_text,
                                         parse_mode="HTML", reply_markup=get_next_to_lesson_3_keyboard())


@dp.callback_query(F.data == "lesson_3_opened")
async def process_lesson_3(callback: types.CallbackQuery):
    await callback.answer()
    
    # ✅ Записываем время получения урока 3 (для догрева #5)
    await db.update_lesson_3_received(callback.from_user.id)

    lesson3_text = """<b><a href="https://teletype.in/@tmsgone/ohXkVXuA3Mi">Урок 3. База по поиску клиентов</a></b>

В этом материале я собрал базовые принципы, которыми сам пользуюсь при работе:

<blockquote>• где искать первых клиентов;
• как составить сильный оффер;
• как не остаться без проектов;
• как выстраивать работу с клиентом после первой сделки;
• как увеличивать ценность своих услуг и работать на более высокие чеки.</blockquote>

<b>Изучи <a href="https://teletype.in/@tmsgone/ohXkVXuA3Mi">материал</a> внимательно — именно эти принципы становятся отправной точкой для большинства специалистов, которые начинают зарабатывать в AI.</b>"""

    await callback.message.answer(lesson3_text, reply_markup=get_final_keyboard(), parse_mode="HTML")


@dp.callback_query(F.data == "lesson_complete")
async def process_lesson_complete(callback: types.CallbackQuery):
    await callback.answer()
    await db.mark_lesson_complete(callback.from_user.id)

    final_text = """<b>Поздравляю! Ты прошел вводную часть моей AI Academy</b>

Но это лишь небольшая часть полной системы. Внутри AI Academy мы подробно разбираем генеративный AI, AI-маркетинг, создание AI-ассистентов, AI Web Development, продажи, работу с клиентами, регулярно проводим воркшопы, эфиры, разборы и постоянно дополняем программу новыми материалами.

<b>Сейчас открыт набор на закрытый День открытых дверей AI Academy, на которой мы:</b>
<blockquote>• разберем твою текущую ситуацию и цели;
• поможем выбрать наиболее подходящее направление в AI;
• составим персональную дорожную карту выхода на первые результаты;
• покажем, как устроена академия изнутри и по какой системе проходят обучение наши ученики;
• ответим на все вопросы и расскажем, как попасть в ближайший поток обучения.</blockquote>

<b>Если ты хочешь не просто изучать AI, а начать зарабатывать с его помощью, это следующий логичный шаг — нажимай кнопку ниже и оставляй заявку на участие 💻</b>"""

    await callback.message.answer(final_text, reply_markup=get_consultation_keyboard(), parse_mode="HTML")


# --- Команды админа ---
@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        total = await db.get_stats()
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
• <a href="https://example.com">ссылка</a> - <code>&lt;a href="URL"&gt;текст&lt;/a&gt;</code>"""
        await message.answer(help_text, parse_mode="HTML")
        await state.set_state(MailingStates.waiting_for_text)


@dp.message(MailingStates.waiting_for_text)
async def process_mailing_text(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.update_data(message=message)
    await message.answer("Добавить кнопку?", reply_markup=get_button_choice_keyboard())
    await state.set_state(MailingStates.waiting_for_button)


@dp.callback_query(MailingStates.waiting_for_button)
async def process_button_choice(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer()
        return
    await callback.answer()
    if callback.data == "add_button":
        await callback.message.answer("Текст кнопки:")
        await state.set_state(MailingStates.waiting_for_button_text)
    elif callback.data == "send_now":
        data = await state.get_data()
        await send_mailing_to_all(data.get('message'))
        await state.clear()


@dp.message(MailingStates.waiting_for_button_text)
async def process_button_text(message: types.Message, state: FSMContext):
    await state.update_data(button_text=message.text)
    await message.answer("Ссылка:")
    await state.set_state(MailingStates.waiting_for_button_url)


@dp.message(MailingStates.waiting_for_button_url)
async def process_button_url(message: types.Message, state: FSMContext):
    url = message.text
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    await state.update_data(button_url=url)
    await message.answer("Добавить вторую кнопку?", reply_markup=get_second_button_choice_keyboard())
    await state.set_state(MailingStates.waiting_for_second_button)


@dp.callback_query(MailingStates.waiting_for_second_button)
async def process_second_button_choice(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer()
        return
    await callback.answer()
    if callback.data == "add_second_button":
        await callback.message.answer("Текст второй кнопки:")
        await state.set_state(MailingStates.waiting_for_second_button_text)
    elif callback.data == "send_with_one":
        data = await state.get_data()
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=data.get('button_text'), url=data.get('button_url'))
        ]])
        await send_mailing_to_all(data.get('message'), keyboard)
        await state.clear()


@dp.message(MailingStates.waiting_for_second_button_text)
async def process_second_button_text(message: types.Message, state: FSMContext):
    await state.update_data(second_button_text=message.text)
    await message.answer("Ссылка для второй кнопки:")
    await state.set_state(MailingStates.waiting_for_second_button_url)


@dp.message(MailingStates.waiting_for_second_button_url)
async def process_second_button_url(message: types.Message, state: FSMContext):
    url = message.text
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    data = await state.get_data()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=data.get('button_text'), url=data.get('button_url'))],
        [InlineKeyboardButton(text=data.get('second_button_text'), url=url)]
    ])
    await send_mailing_to_all(data.get('message'), keyboard)
    await state.clear()


@dp.message(Command("getbd"))
async def cmd_getbd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        users = await db.get_all_users_full()
        if not users:
            await message.answer("📭 База данных пуста")
            return

        text = f"📊 <b>Все пользователи ({len(users)})</b>\n\n"
        shown = 0
        for row in users:
            user_id, username, first_name, is_active, started_at, q1, q2 = (
                row['user_id'], row['username'], row['first_name'],
                row['is_active'], row['started_at'], row['survey_q1'], row['survey_q2']
            )
            status = "✅" if is_active else "❌"
            name = first_name or "Без имени"
            username_str = f" (@{username})" if username else ""
            date = started_at[:10] if started_at else "неизвестно"

            entry = f"{status} <b>{name}</b>{username_str}\n└ ID: {user_id} | {date}\n"
            if q1:
                entry += f"└ 📝 {q1[:50]}...\n"
            entry += "\n"

            if len(text) + len(entry) > 3500:
                text += f"... и еще {len(users) - shown} пользователей"
                break

            text += entry
            shown += 1

        await message.answer(text, parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


# --- Заявки на вступление в канал ---
@dp.chat_join_request()
async def handle_join_request(request: ChatJoinRequest):
    user = request.from_user
    try:
        await request.approve()
        await db.add_channel_subscriber(user.id)
        
        # Проверяем, есть ли пользователь в ожидании и не отправляли ли ему пост
        cursor = await db.conn.execute('''
            SELECT user_id, lesson_1_sent_at, practice_sent 
            FROM users 
            WHERE user_id = ?
        ''', (user.id,))
        user_data = await cursor.fetchone()
        
        # Отправляем пост только если пользователь получил вводный урок и пост еще не отправлен
        if user_data and user_data['lesson_1_sent_at'] is not None and user_data['practice_sent'] == 0:
            await send_practice_post(user.id)
            await db.mark_practice_sent(user.id)
            logging.info(f"✅ Пост практики отправлен для {user.id} после подписки")
        else:
            logging.info(f"ℹ️ Пользователь {user.id} еще не прошел вводный урок или пост уже отправлен")
        
        logging.info(f"✅ Заявка от {user.first_name} (@{user.username}) одобрена и записана в БД")
    except Exception as e:
        logging.error(f"❌ Ошибка одобрения заявки {user.id}: {e}")


# --- Запуск ---
async def main():
    try:
        await db.connect()
    except Exception as e:
        logging.error(f"Не удалось подключиться к БД: {e}")
        return

    asyncio.create_task(reminder_checker())
    asyncio.create_task(channel_wait_checker())

    print("\n" + "=" * 60)
    print("🚀 БОТ ЗАПУЩЕН" + ("  [ТЕСТОВЫЙ РЕЖИМ]" if TEST_MODE else "  [ПРОДАКШЕН]"))
    print("=" * 60)
    print(f"👑 Админ ID: {ADMIN_ID}")
    print("=" * 60)
    print("📢 Команды админа: /stats /rasilka /getbd")
    print("=" * 60)
    print("⏰ ДОГРЕВЫ (секунды):")
    print(f"  #1: через {REMINDER_1_DELAY} сек после вводного урока (если не подписался)")
    print(f"  #2: через {REMINDER_2_DELAY} сек после урока 1 (если не открыл урок 2)")
    print(f"  #3: через {REMINDER_3_DELAY} сек после урока 2 (если не завершил курс)")
    print(f"  #4: через {REMINDER_4_DELAY} сек после догрева 1 (если так и не подписался)")
    print(f"  #5: через {REMINDER_5_DELAY} сек после урока 3 (если не нажал 'Я изучил все 3 урока')")
    print("=" * 60 + "\n")

    try:
        await dp.start_polling(bot)
    finally:
        await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n❌ Бот остановлен")