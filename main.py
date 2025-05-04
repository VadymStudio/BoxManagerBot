# Встановлення залежностей:
# Виконайте наступну команду в терміналі, щоб встановити необхідні бібліотеки:
# pip install aiogram aiohttp python-dotenv
# Якщо використовуєте віртуальне середовище, переконайтеся, що воно активоване.
# У VS Code виберіть правильний інтерпретатор Python (Ctrl+Shift+P -> Python: Select Interpreter).

import logging
import os
import random
import sqlite3
import time
import asyncio
import re
import string
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest
from dotenv import load_dotenv

# Завантаження змінних із .env
load_dotenv()

# Налаштування логування
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Локальні налаштування
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
try:
    Vadym_ID = int(os.getenv("Vadym_ID", 0))
    Nazar_ID = int(os.getenv("Nazar_ID", 0))
except (ValueError, TypeError) as e:
    logger.error(f"Error reading Vadym_ID or Nazar_ID: {e}")
    Vadym_ID, Nazar_ID = 0, 0
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///bot.db")

if not TELEGRAM_TOKEN:
    logger.error("TELEGRAM_TOKEN is not set")
    raise ValueError("TELEGRAM_TOKEN is required")

# Список адмінів
ADMIN_IDS = [id for id in [Vadym_ID, Nazar_ID] if id != 0]
logger.info(f"ADMIN_IDS: {ADMIN_IDS}")

# Ініціалізація бота
bot = Bot(token=TELEGRAM_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot=bot, storage=storage)

# Список користувачів, які шукають матч, і подія для сповіщення
searching_users = []
matchmaking_event = asyncio.Event()

# Визначення станів
class CharacterCreation(StatesGroup):
    awaiting_character_name = State()
    awaiting_fighter_type = State()

class RoomCreation(StatesGroup):
    awaiting_room_token = State()

# Ініціалізація бази даних SQLite
def init_db():
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        character_name TEXT UNIQUE,
        fighter_type TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS fighter_stats (
        user_id INTEGER PRIMARY KEY,
        fighter_type TEXT,
        stamina REAL,
        strength REAL,
        reaction REAL,
        health REAL,
        punch_speed REAL,
        will REAL,
        footwork REAL,
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS matches (
        match_id INTEGER PRIMARY KEY AUTOINCREMENT,
        player1_id INTEGER,
        player2_id INTEGER,
        status TEXT,
        start_time REAL,
        current_round INTEGER,
        player1_action TEXT,
        player2_action TEXT,
        player1_health REAL,
        player1_stamina REAL,
        player2_health REAL,
        player2_stamina REAL,
        action_deadline REAL,
        distance TEXT,
        FOREIGN KEY (player1_id) REFERENCES users (user_id),
        FOREIGN KEY (player2_id) REFERENCES users (user_id)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS knockdowns (
        match_id INTEGER,
        player_id INTEGER,
        deadline REAL,
        FOREIGN KEY (match_id) REFERENCES matches (match_id),
        FOREIGN KEY (player_id) REFERENCES users (user_id)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS rooms (
        token TEXT PRIMARY KEY,
        creator_id INTEGER,
        opponent_id INTEGER,
        created_at REAL,
        status TEXT,
        FOREIGN KEY (creator_id) REFERENCES users (user_id),
        FOREIGN KEY (opponent_id) REFERENCES users (user_id)
    )""")
    # Очищення активних матчів, нокдаунів і старих кімнат
    c.execute("DELETE FROM matches WHERE status = 'active'")
    c.execute("DELETE FROM knockdowns")
    c.execute("DELETE FROM rooms WHERE created_at < ?", (time.time() - 300,))
    conn.commit()
    conn.close()

init_db()

# Перевірка maintenance mode
maintenance_mode = False

async def check_maintenance(message: types.Message):
    if maintenance_mode and message.from_user.id not in ADMIN_IDS:
        await message.reply("Бот на технічних роботах. Спробуй пізніше.")
        logger.debug(f"Maintenance mode blocked user {message.from_user.id}")
        return False
    return True

# Скидання стану
async def reset_state(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        logger.debug(f"Resetting state for user {message.from_user.id} from {current_state}")
        await state.clear()

# Генерація токена для кімнати
def generate_room_token():
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choice(characters) for _ in range(6))

# Налаштування меню команд
async def setup_bot_commands():
    user_commands = [
        BotCommand(command="/start", description="Почати роботу з ботом"),
        BotCommand(command="/create_account", description="Створити акаунт"),
        BotCommand(command="/delete_account", description="Видалити акаунт"),
        BotCommand(command="/start_match", description="Почати матч"),
        BotCommand(command="/create_room", description="Створити кімнату"),
        BotCommand(command="/join_room", description="Приєднатися до кімнати"),
        BotCommand(command="/start_fight", description="Почати бій (тільки для творця кімнати)"),
        BotCommand(command="/refresh_commands", description="Оновити меню команд")
    ]
    
    admin_commands = user_commands + [
        BotCommand(command="/admin_setting", description="Адмін-панель"),
        BotCommand(command="/maintenance_on", description="Увімкнути технічні роботи"),
        BotCommand(command="/maintenance_off", description="Вимкнути технічні роботи")
    ]
    
    try:
        await bot.delete_my_commands(scope=BotCommandScopeDefault())
        logger.info("Cleared default commands")
        await bot.set_my_commands(commands=user_commands, scope=BotCommandScopeDefault())
        logger.info("Set default commands for all users")
    except TelegramBadRequest as e:
        logger.error(f"Failed to set default commands: {e}")
    
    for admin_id in ADMIN_IDS:
        try:
            await bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=admin_id))
            await bot.set_my_commands(commands=admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))
            logger.info(f"Set admin commands for user {admin_id}")
        except TelegramBadRequest as e:
            logger.error(f"Failed to set admin commands for user {admin_id}: {e}")

# Команда /refresh_commands
@dp.message(Command("refresh_commands"))
async def refresh_commands(message: types.Message, state: FSMContext):
    logger.debug(f"Received /refresh_commands from user {message.from_user.id}")
    await reset_state(message, state)
    if not await check_maintenance(message):
        return
    try:
        await setup_bot_commands()
        await message.reply("Меню команд оновлено! Відкрий меню команд (📋).")
        logger.debug(f"Refreshed commands for user {message.from_user.id}")
    except Exception as e:
        await message.reply("Помилка при оновленні команд. Спробуй ще раз.")
        logger.error(f"Error refreshing commands for user {message.from_user.id}: {e}")

# Команда /start
@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    logger.debug(f"Received /start from user {message.from_user.id}")
    await reset_state(message, state)
    user_id = message.from_user.id
    if not await check_maintenance(message):
        return
    await message.reply(
        "Вітаємо у Box Manager Online! Відкрий меню команд (📋), щоб почати."
    )
    logger.debug(f"Sent /start response to user {user_id}")

# Команда /create_account
@dp.message(Command("create_account"))
async def create_account(message: types.Message, state: FSMContext):
    logger.debug(f"Received /create_account from user {message.from_user.id}")
    await reset_state(message, state)
    if not await check_maintenance(message):
        return
    user_id = message.from_user.id
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if c.fetchone():
        await message.reply("Ти вже маєш акаунт! Використай /delete_account, щоб видалити його.")
        logger.debug(f"User {user_id} already has an account")
        conn.close()
        return
    conn.close()
    await message.reply("Введи ім'я персонажа (нік у грі, тільки літери, цифри, до 20 символів):")
    await state.set_state(CharacterCreation.awaiting_character_name)
    logger.debug(f"Prompted user {user_id} for character name")

# Обробка імені персонажа
@dp.message(CharacterCreation.awaiting_character_name)
async def handle_character_name(message: types.Message, state: FSMContext):
    logger.debug(f"Received text input from user {message.from_user.id}: {message.text}")
    if not await check_maintenance(message):
        return
    user_id = message.from_user.id
    character_name = message.text.strip()

    if character_name.startswith('/'):
        await message.reply("Будь ласка, введи нік, а не команду. Спробуй ще раз.")
        logger.debug(f"User {user_id} entered command {character_name} instead of nickname")
        return

    if not re.match(r'^[a-zA-Z0-9_]{1,20}$', character_name):
        await message.reply("Нік може містити тільки літери, цифри, символ '_', до 20 символів. Спробуй ще раз.")
        logger.debug(f"Invalid character name {character_name} from user {user_id}")
        return

    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT character_name FROM users WHERE character_name = ?", (character_name,))
    if c.fetchone():
        await message.reply("Цей нік уже зайнятий. Вибери інший.")
        logger.debug(f"Character name {character_name} already taken")
        conn.close()
        return
    try:
        c.execute(
            "INSERT INTO users (user_id, username, character_name) VALUES (?, ?, ?)",
            (user_id, message.from_user.username, character_name),
        )
        conn.commit()
        await state.update_data(character_name=character_name)
        fighter_descriptions = (
            "Вибери тип бійця (змінити вибір потім неможливо):\n\n"
            "🔥 *Swarmer*: Агресивний боєць. Висока сила (1.5), воля (1.5), швидкість удару (1.35), робота ніг (1.2). Здоров’я: 195, виносливість: 1.1.\n"
            "🥊 *Out-boxer*: Витривалий і тактичний. Висока виносливість (1.5), здоров’я: 300, робота ніг (1.4). Сила: 1.15, воля: 1.3.\n"
            "⚡ *Counter-puncher*: Майстер контратаки. Висока реакція (1.5), швидкість удару (1.5), робота ніг (1.5). Сила: 1.25, здоров’я: 150, воля: 1.2."
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Swarmer", callback_data="swarmer")],
            [InlineKeyboardButton(text="Out-boxer", callback_data="out_boxer")],
            [InlineKeyboardButton(text="Counter-puncher", callback_data="counter_puncher")],
        ])
        await message.reply(fighter_descriptions, reply_markup=keyboard, parse_mode="Markdown")
        await state.set_state(CharacterCreation.awaiting_fighter_type)
        logger.debug(f"Sent fighter type selection to user {user_id}")
    except sqlite3.IntegrityError as e:
        await message.reply("Помилка: цей нік уже зайнятий або сталася помилка. Спробуй інший.")
        logger.error(f"Integrity error for character name {character_name}: {e}")
    except Exception as e:
        await message.reply("Сталася помилка при створенні акаунта. Спробуй ще раз.")
        logger.error(f"Error creating account for user {user_id}: {e}")
    finally:
        conn.close()

# Обробка вибору типу бійця
@dp.callback_query(CharacterCreation.awaiting_fighter_type, lambda c: c.data in ["swarmer", "out_boxer", "counter_puncher"])
async def handle_fighter_type(callback: types.CallbackQuery, state: FSMContext):
    logger.debug(f"Received fighter type selection from user {callback.from_user.id}: {callback.data}")
    user_id = callback.from_user.id
    fighter_type = callback.data
    user_data = await state.get_data()
    character_name = user_data.get("character_name")
    
    fighter_stats = {
        "swarmer": {
            "stamina": 1.1,
            "strength": 1.5,
            "reaction": 1.1,
            "health": 195,
            "punch_speed": 1.35,
            "will": 1.5,
            "footwork": 1.2
        },
        "out_boxer": {
            "stamina": 1.5,
            "strength": 1.15,
            "reaction": 1.1,
            "health": 300,
            "punch_speed": 1.1,
            "will": 1.3,
            "footwork": 1.4
        },
        "counter_puncher": {
            "stamina": 1.15,
            "strength": 1.25,
            "reaction": 1.5,
            "health": 150,
            "punch_speed": 1.5,
            "will": 1.2,
            "footwork": 1.5
        }
    }
    
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    try:
        c.execute(
            "UPDATE users SET fighter_type = ? WHERE user_id = ?",
            (fighter_type, user_id)
        )
        stats = fighter_stats[fighter_type]
        c.execute(
            """INSERT INTO fighter_stats (user_id, fighter_type, stamina, strength, reaction, health, punch_speed, will, footwork)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, fighter_type, stats["stamina"], stats["strength"], stats["reaction"],
             stats["health"], stats["punch_speed"], stats["will"], stats["footwork"])
        )
        conn.commit()
        await callback.message.reply(f"Акаунт створено! Персонаж: {character_name}, Тип: {fighter_type.capitalize()}")
        await callback.answer()
        logger.debug(f"Created account for user {user_id}: {character_name}, {fighter_type}")
    except sqlite3.IntegrityError as e:
        await callback.message.reply("Помилка при збереженні бійця. Спробуй ще раз.")
        logger.error(f"Integrity error for fighter type {fighter_type} for user {user_id}: {e}")
        await callback.answer()
    except Exception as e:
        await callback.message.reply("Сталася помилка при створенні бійця. Спробуй ще раз.")
        logger.error(f"Error saving fighter type for user {user_id}: {e}")
        await callback.answer()
    finally:
        conn.close()
        await state.clear()

# Команда /delete_account
@dp.message(Command("delete_account"))
async def delete_account(message: types.Message, state: FSMContext):
    logger.debug(f"Received /delete_account from user {message.from_user.id}")
    await reset_state(message, state)
    if not await check_maintenance(message):
        return
    user_id = message.from_user.id
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if not c.fetchone():
        await message.reply("У тебе немає акаунта!")
        logger.debug(f"No account found for user {user_id}")
        conn.close()
        return
    c.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
    c.execute("DELETE FROM fighter_stats WHERE user_id = ?", (user_id,))
    c.execute("DELETE FROM matches WHERE player1_id = ? OR player2_id = ?", (user_id, user_id))
    c.execute("DELETE FROM knockdowns WHERE player_id = ?", (user_id,))
    c.execute("DELETE FROM rooms WHERE creator_id = ? OR opponent_id = ?", (user_id, user_id))
    conn.commit()
    conn.close()
    await message.reply("Акаунт видалено! Можеш створити новий за допомогою /create_account.")
    logger.debug(f"Deleted account for user {user_id}")

# Команда /create_room
@dp.message(Command("create_room"))
async def create_room(message: types.Message, state: FSMContext):
    logger.debug(f"Received /create_room from user {message.from_user.id}")
    await reset_state(message, state)
    if not await check_maintenance(message):
        return
    user_id = message.from_user.id
    
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT user_id, character_name FROM users WHERE user_id = ?", (user_id,))
    user = c.fetchone()
    if not user:
        await message.reply("Спочатку створи акаунт за допомогою /create_account!")
        logger.debug(f"No account for user {user_id} for /create_room")
        conn.close()
        return
    
    c.execute("SELECT match_id FROM matches WHERE (player1_id = ? OR player2_id = ?) AND status = 'active'", (user_id, user_id))
    if c.fetchone():
        await message.reply("Ти вже в матчі! Закінчи поточний бій.")
        logger.debug(f"User {user_id} already in active match")
        conn.close()
        return
    
    c.execute("SELECT token FROM rooms WHERE creator_id = ? AND status = 'waiting'", (user_id,))
    if c.fetchone():
        await message.reply("Ти вже створив кімнату! Зачекай, поки хтось приєднається, або видали акаунт.")
        logger.debug(f"User {user_id} already has a waiting room")
        conn.close()
        return
    
    token = generate_room_token()
    try:
        c.execute(
            "INSERT INTO rooms (token, creator_id, created_at, status) VALUES (?, ?, ?, ?)",
            (token, user_id, time.time(), 'waiting')
        )
        conn.commit()
        await message.reply(
            f"Кімната створена! Токен: <code>{token}</code>\nПоділись ним із суперником. "
            f"Коли суперник приєднається, використовуй /start_fight, щоб почати бій.",
            parse_mode="HTML"
        )
        logger.debug(f"Created room with token {token} for user {user_id}")
    except sqlite3.IntegrityError:
        await message.reply("Помилка: токен уже існує. Спробуй ще раз.")
        logger.error(f"Token {token} already exists")
    finally:
        conn.close()

# Команда /join_room
@dp.message(Command("join_room"))
async def join_room(message: types.Message, state: FSMContext):
    logger.debug(f"Received /join_room from user {message.from_user.id}")
    await reset_state(message, state)
    if not await check_maintenance(message):
        return
    user_id = message.from_user.id
    
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT user_id, character_name FROM users WHERE user_id = ?", (user_id,))
    user = c.fetchone()
    if not user:
        await message.reply("Спочатку створи акаунт за допомогою /create_account!")
        logger.debug(f"No account for user {user_id} for /join_room")
        conn.close()
        return
    
    c.execute("SELECT match_id FROM matches WHERE (player1_id = ? OR player2_id = ?) AND status = 'active'", (user_id, user_id))
    if c.fetchone():
        await message.reply("Ти вже в матчі! Закінчи поточний бій.")
        logger.debug(f"User {user_id} already in active match")
        conn.close()
        return
    
    args = message.text.split()
    if len(args) != 2:
        await message.reply("Вкажи токен кімнати: /join_room <token>")
        logger.debug(f"Invalid /join_room command from user {user_id}")
        conn.close()
        return
    
    token = args[1].strip()
    c.execute("SELECT creator_id, created_at, opponent_id, status FROM rooms WHERE token = ?", (token,))
    room = c.fetchone()
    if not room:
        await message.reply("Кімната не знайдена або прострочена!")
        logger.debug(f"Room with token {token} not found")
        conn.close()
        return
    
    creator_id, created_at, opponent_id, status = room
    if creator_id == user_id:
        await message.reply("Ти не можеш приєднатися до власної кімнати!")
        logger.debug(f"User {user_id} tried to join own room {token}")
        conn.close()
        return
    
    if status != 'waiting':
        await message.reply("Кімната вже закрита або бій розпочато!")
        logger.debug(f"Room {token} is not in waiting status")
        conn.close()
        return
    
    if opponent_id is not None:
        await message.reply("Кімната вже заповнена! Максимум 2 гравці.")
        logger.debug(f"Room {token} already has 2 players")
        conn.close()
        return
    
    if time.time() - created_at > 300:
        c.execute("DELETE FROM rooms WHERE token = ?", (token,))
        conn.commit()
        await message.reply("Кімната прострочена!")
        logger.debug(f"Room {token} expired")
        conn.close()
        return
    
    try:
        c.execute(
            "UPDATE rooms SET opponent_id = ?, status = 'ready' WHERE token = ?",
            (user_id, token)
        )
        conn.commit()
        await message.reply(
            f"Ти приєднався до кімнати {token}! Чекай, поки творець розпочне бій (/start_fight)."
        )
        await bot.send_message(
            creator_id,
            f"Гравець {user[1]} приєднався до твоєї кімнати {token}! "
            f"Використай /start_fight, щоб почати бій."
        )
        logger.debug(f"User {user_id} joined room {token}")
    except sqlite3.Error as e:
        await message.reply("Помилка при приєднанні до кімнати. Спробуй ще раз.")
        logger.error(f"Database error joining room {token}: {e}")
    finally:
        conn.close()

# Команда /start_fight
@dp.message(Command("start_fight"))
async def start_fight(message: types.Message, state: FSMContext):
    logger.debug(f"Received /start_fight from user {message.from_user.id}")
    await reset_state(message, state)
    if not await check_maintenance(message):
        return
    user_id = message.from_user.id
    
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT token, opponent_id, status FROM rooms WHERE creator_id = ? AND status = 'ready'", (user_id,))
    room = c.fetchone()
    if not room:
        await message.reply("Ти не створив кімнату, або ще немає суперника!")
        logger.debug(f"No ready room found for creator {user_id}")
        conn.close()
        return
    
    token, opponent_id, status = room
    if opponent_id is None:
        await message.reply("Суперник ще не приєднався! Зачекай.")
        logger.debug(f"No opponent in room {token}")
        conn.close()
        return
    
    c.execute("SELECT user_id, character_name, fighter_type FROM users WHERE user_id = ?", (user_id,))
    creator = c.fetchone()
    c.execute("SELECT user_id, character_name, fighter_type FROM users WHERE user_id = ?", (opponent_id,))
    opponent = c.fetchone()
    
    if not creator or not opponent:
        await message.reply("Помилка: дані гравців не знайдено.")
        logger.error(f"User data missing for creator {user_id} or opponent {opponent_id}")
        conn.close()
        return
    
    c.execute("SELECT health, stamina FROM fighter_stats WHERE user_id = ?", (user_id,))
    creator_stats = c.fetchone()
    c.execute("SELECT health, stamina FROM fighter_stats WHERE user_id = ?", (opponent_id,))
    opponent_stats = c.fetchone()
    
    if not creator_stats or not opponent_stats:
        await message.reply("Помилка: не вдалося знайти статистику бійця. Спробуй видалити акаунт і створити новий.")
        logger.error(f"Missing stats for user {user_id} or opponent {opponent_id}")
        conn.close()
        return
    
    try:
        action_deadline = time.time() + 30
        c.execute(
            """INSERT INTO matches (player1_id, player2_id, status, start_time, current_round, player1_health, player1_stamina, player2_health, player2_stamina, action_deadline, distance)
            VALUES (?, ?, 'active', ?, 1, ?, ?, ?, ?, ?, 'far')""",
            (user_id, opponent_id, time.time(), creator_stats[0], creator_stats[1], opponent_stats[0], opponent_stats[1], action_deadline)
        )
        conn.commit()
        match_id = c.lastrowid
        c.execute("UPDATE rooms SET status = 'active' WHERE token = ?", (token,))
        conn.commit()
        
        keyboard = get_fight_keyboard(match_id, "far")
        await message.reply(
            f"Матч розпочато! Ти ({creator[1]}, {creator[2].capitalize()}) проти {opponent[1]} ({opponent[2].capitalize()}). "
            f"Бій триває 3 раунди по 3 хвилини. Дистанція: Далеко. Обери дію (30 секунд):",
            reply_markup=keyboard
        )
        await bot.send_message(
            opponent_id, f"Матч розпочато! Ти ({opponent[1]}, {opponent[2].capitalize()}) проти {creator[1]} ({creator[2].capitalize()}). "
            f"Бій триває 3 раунди по 3 хвилини. Дистанція: Далеко. Обери дію (30 секунд):",
            reply_markup=keyboard
        )
        logger.debug(f"Started match {match_id} for user {user_id} vs {opponent_id}")
    except sqlite3.Error as e:
        await message.reply("Помилка при створенні матчу. Спробуй ще раз.")
        logger.error(f"Database error starting match for room {token}: {e}")
    finally:
        conn.close()

# Команда /start_match
@dp.message(Command("start_match"))
async def start_match(message: types.Message, state: FSMContext):
    logger.debug(f"Received /start_match from user {message.from_user.id}")
    await reset_state(message, state)
    if not await check_maintenance(message):
        return
    user_id = message.from_user.id
    
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT user_id, character_name, fighter_type FROM users WHERE user_id = ?", (user_id,))
    user = c.fetchone()
    if not user:
        await message.reply("Спочатку створи акаунт за допомогою /create_account!")
        logger.debug(f"No account for user {user_id} for /start_match")
        conn.close()
        return
    
    c.execute("SELECT match_id FROM matches WHERE (player1_id = ? OR player2_id = ?) AND status = 'active'", (user_id, user_id))
    if c.fetchone():
        await message.reply("Ти вже в матчі! Закінчи поточний бій.")
        logger.debug(f"User {user_id} already in active match")
        conn.close()
        return
    
    if user_id in searching_users:
        await message.reply("Ти вже шукаєш суперника! Зачекай.")
        logger.debug(f"User {user_id} already in searching_users")
        conn.close()
        return
    
    searching_users.append(user_id)
    logger.debug(f"User {user_id} added to searching_users: {searching_users}")
    matchmaking_event.set()
    
    await message.reply("Пошук суперника... (макс. 30 секунд)")
    
    try:
        await asyncio.wait_for(matchmaking_event.wait(), timeout=30)
        for opponent_id in searching_users:
            if opponent_id != user_id:
                if user_id in searching_users:
                    searching_users.remove(user_id)
                if opponent_id in searching_users:
                    searching_users.remove(opponent_id)
                logger.debug(f"Match found: {user_id} vs {opponent_id}")
                
                c.execute("SELECT user_id, character_name, fighter_type FROM users WHERE user_id = ?", (opponent_id,))
                opponent = c.fetchone()
                if not opponent:
                    await message.reply("Помилка: суперник не знайдений. Спробуй ще раз.")
                    logger.error(f"Opponent {opponent_id} not found in users")
                    conn.close()
                    return
                
                c.execute("SELECT health, stamina FROM fighter_stats WHERE user_id = ?", (user_id,))
                player_stats = c.fetchone()
                c.execute("SELECT health, stamina FROM fighter_stats WHERE user_id = ?", (opponent_id,))
                opponent_stats = c.fetchone()
                
                if not player_stats or not opponent_stats:
                    await message.reply("Помилка: не вдалося знайти статистику бійця. Спробуй видалити акаунт і створити новий.")
                    logger.error(f"Missing stats for user {user_id} or opponent {opponent_id}")
                    conn.close()
                    return
                
                action_deadline = time.time() + 30
                c.execute(
                    """INSERT INTO matches (player1_id, player2_id, status, start_time, current_round, player1_health, player1_stamina, player2_health, player2_stamina, action_deadline, distance)
                    VALUES (?, ?, 'active', ?, 1, ?, ?, ?, ?, ?, 'far')""",
                    (user_id, opponent_id, time.time(), player_stats[0], player_stats[1], opponent_stats[0], opponent_stats[1], action_deadline)
                )
                conn.commit()
                match_id = c.lastrowid
                conn.close()
                
                keyboard = get_fight_keyboard(match_id, "far")
                await message.reply(
                    f"Матч розпочато! Ти ({user[1]}, {user[2].capitalize()}) проти {opponent[1]} ({opponent[2].capitalize()}). Бій триває 3 раунди по 3 хвилини. Дистанція: Далеко. Обери дію (30 секунд):",
                    reply_markup=keyboard
                )
                await bot.send_message(
                    chat_id=opponent_id,
                    text=f"Матч розпочато! Ти ({opponent[1]}, {opponent[2].capitalize()}) проти {user[1]} ({user[2].capitalize()}). Бій триває 3 раунди по 3 хвилини. Дистанція: Далеко. Обери дію (30 секунд):",
                    reply_markup=keyboard
                )
                logger.debug(f"Started match {match_id} for user {user_id} vs {opponent_id}")
                return
        
        if user_id in searching_users:
            searching_users.remove(user_id)
        await message.reply("Суперник не знайдений. Спробуй ще раз.")
        logger.debug(f"Search timeout for user {user_id}")
        conn.close()
    except asyncio.TimeoutError:
        if user_id in searching_users:
            searching_users.remove(user_id)
        await message.reply("Суперник не знайдений. Спробуй ще раз.")
        logger.debug(f"Search timeout for user {user_id}")
        conn.close()

# Клавіатура для бою
def get_fight_keyboard(match_id, distance):
    if distance == "close":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="Джеб", callback_data=f"fight_{match_id}_jab"),
                InlineKeyboardButton(text="Аперкот", callback_data=f"fight_{match_id}_uppercut"),
                InlineKeyboardButton(text="Хук", callback_data=f"fight_{match_id}_hook")
            ],
            [
                InlineKeyboardButton(text="Ухилитися", callback_data=f"fight_{match_id}_dodge"),
                InlineKeyboardButton(text="Блок", callback_data=f"fight_{match_id}_block"),
                InlineKeyboardButton(text="Відійти", callback_data=f"fight_{match_id}_move_away")
            ],
            [
                InlineKeyboardButton(text="Відпочинок", callback_data=f"fight_{match_id}_rest")
            ]
        ])
    else:  # far, cornered_p1, cornered_p2
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="Джеб", callback_data=f"fight_{match_id}_jab"),
                InlineKeyboardButton(text="Ухилитися", callback_data=f"fight_{match_id}_dodge"),
                InlineKeyboardButton(text="Блок", callback_data=f"fight_{match_id}_block")
            ],
            [
                InlineKeyboardButton(text="Підійти", callback_data=f"fight_{match_id}_move_closer"),
                InlineKeyboardButton(text="Відпочинок", callback_data=f"fight_{match_id}_rest")
            ]
        ])
    return keyboard

# Обробка дій у бою
@dp.callback_query(lambda c: c.data.startswith("fight_"))
async def handle_fight_action(callback: types.CallbackQuery):
    logger.debug(f"Received fight action from user {callback.from_user.id}: {callback.data}")
    user_id = callback.from_user.id
    callback_data = callback.data.split("_")
    match_id, action = int(callback_data[1]), callback_data[2]
    
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT player1_id, player2_id, player1_action, player2_action, status, action_deadline, distance FROM matches WHERE match_id = ?", (match_id,))
    match = c.fetchone()
    if not match or match[4] != "active":
        await callback.message.reply("Матч завершено або не існує.")
        logger.debug(f"Match {match_id} not active or does not exist")
        conn.close()
        await callback.answer()
        return
    
    player1_id, player2_id, player1_action, player2_action, status, action_deadline, distance = match
    
    if time.time() > action_deadline:
        await callback.message.reply("Час для дії минув! Раунд завершено автоматично.")
        await process_round(match_id, timed_out=True)
        logger.debug(f"Match {match_id} round timed out")
        conn.close()
        await callback.answer()
        return
    
    # Перевірка доступності дії залежно від дистанції
    if distance != "close" and action in ["uppercut", "hook"]:
        await callback.message.reply("На далекій дистанції доступний лише Джеб!")
        logger.debug(f"Invalid action {action} for far/cornered distance in match {match_id}")
        conn.close()
        await callback.answer()
        return
    if distance == "close" and action == "move_closer":
        await callback.message.reply("Ви вже на близькій дистанції!")
        logger.debug(f"Invalid action move_closer for close distance in match {match_id}")
        conn.close()
        await callback.answer()
        return
    if distance in ["far", "cornered_p1", "cornered_p2"] and action == "move_away":
        await callback.message.reply("Ви вже на далекій дистанції!")
        logger.debug(f"Invalid action move_away for far distance in match {match_id}")
        conn.close()
        await callback.answer()
        return
    
    # Збереження дії
    if user_id == player1_id:
        c.execute("UPDATE matches SET player1_action = ? WHERE match_id = ?", (action, match_id))
    elif user_id == player2_id:
        c.execute("UPDATE matches SET player2_action = ? WHERE match_id = ?", (action, match_id))
    else:
        await callback.message.reply("Ти не учасник цього матчу!")
        logger.debug(f"User {user_id} not in match {match_id}")
        conn.close()
        await callback.answer()
        return
    
    conn.commit()
    
    c.execute("SELECT player1_action, player2_action FROM matches WHERE match_id = ?", (match_id,))
    actions = c.fetchone()
    if actions[0] and actions[1]:
        await process_round(match_id)
    
    conn.close()
    await callback.answer()
    logger.debug(f"Processed fight action {action} for match {match_id} by user {user_id}")

# Надсилання повідомлення про бій
async def send_fight_message(match_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute(
        """SELECT player1_id, player2_id, player1_health, player1_stamina, player2_health, player2_stamina, current_round, distance
        FROM matches WHERE match_id = ?""",
        (match_id,)
    )
    match = c.fetchone()
    if not match:
        conn.close()
        return
    
    player1_id, player2_id, p1_health, p1_stamina, p2_health, p2_stamina, round_num, distance = match
    c.execute("SELECT character_name, fighter_type FROM users WHERE user_id = ?", (player1_id,))
    p1_name, p1_type = c.fetchone()
    c.execute("SELECT character_name, fighter_type FROM users WHERE user_id = ?", (player2_id,))
    p2_name, p2_type = c.fetchone()
    c.execute("SELECT health FROM fighter_stats WHERE user_id = ?", (player1_id,))
    p1_max_health = c.fetchone()[0]
    c.execute("SELECT health FROM fighter_stats WHERE user_id = ?", (player2_id,))
    p2_max_health = c.fetchone()[0]
    
    p1_status_text = get_status_text(p1_name, p1_type, p1_health, p1_stamina, p1_max_health)
    p2_status_text = get_status_text(p2_name, p2_type, p2_health, p2_stamina, p2_max_health)
    
    distance_text = {
        "far": "Далеко",
        "close": "Близько",
        "cornered_p1": f"{p1_name} у куті!",
        "cornered_p2": f"{p2_name} у куті!"
    }[distance]
    
    keyboard = get_fight_keyboard(match_id, distance)
    action_deadline = time.time() + 30
    c.execute("UPDATE matches SET action_deadline = ?, player1_action = NULL, player2_action = NULL WHERE match_id = ?", (action_deadline, match_id))
    conn.commit()
    
    await bot.send_message(
        player1_id,
        f"Раунд {round_num}\nДистанція: {distance_text}\n{p1_status_text}\n{p2_status_text}\nОбери дію (30 секунд):",
        reply_markup=keyboard
    )
    await bot.send_message(
        player2_id,
        f"Раунд {round_num}\nДистанція: {distance_text}\n{p2_status_text}\n{p1_status_text}\nОбери дію (30 секунд):",
        reply_markup=keyboard
    )
    
    conn.close()

# Формування тексту стану гравця
def get_status_text(name, fighter_type, health, stamina, max_health):
    return f"{name} ({fighter_type.capitalize()}):\nЗдоров’я: {health:.1f}/{max_health:.1f}, Енергія: {stamina:.1f}/100"

# Обробка нокдауну
async def handle_knockdown(match_id, player_id, opponent_id, player_name, opponent_name):
    logger.debug(f"Player {player_name} in knockdown for match {match_id}")
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    
    c.execute("SELECT will FROM fighter_stats WHERE user_id = ?", (player_id,))
    will = c.fetchone()[0]
    c.execute("SELECT health FROM fighter_stats WHERE user_id = ?", (player_id,))
    max_health = c.fetchone()[0]
    c.execute("SELECT player1_id, player1_health, player1_stamina, player2_id, player2_health, player2_stamina FROM matches WHERE match_id = ?", (match_id,))
    match = c.fetchone()
    p1_id, p1_health, p1_stamina, p2_id, p2_health, p2_stamina = match
    
    await bot.send_message(player_id, f"Ти впав! Чи зможеш встати?")
    await bot.send_message(opponent_id, f"{player_name} впав! Чи встане він?")
    
    # Формула шансу вставання: 0.4 * will
    stand_chance = 0.4 * will
    if random.random() < stand_chance:
        if player_id == p1_id:
            p1_health = max(0.2 * max_health, p1_health)  # Відновлення до 20% здоров’я, якщо здоров’я було 0
            p1_stamina = min(p1_stamina + 40, 100)
            c.execute(
                "UPDATE matches SET player1_health = ?, player1_stamina = ? WHERE match_id = ?",
                (p1_health, p1_stamina, match_id)
            )
        else:
            p2_health = max(0.2 * max_health, p2_health)
            p2_stamina = min(p2_stamina + 40, 100)
            c.execute(
                "UPDATE matches SET player2_health = ?, player2_stamina = ? WHERE match_id = ?",
                (p2_health, p2_stamina, match_id)
            )
        c.execute("DELETE FROM knockdowns WHERE match_id = ? AND player_id = ?", (match_id, player_id))
        conn.commit()
        await bot.send_message(
            player_id,
            f"Ти встав після нокдауну! Здоров’я: {p1_health if player_id == p1_id else p2_health:.1f}, Енергія: {p1_stamina if player_id == p1_id else p2_stamina:.1f}"
        )
        await bot.send_message(
            opponent_id,
            f"{player_name} встав після нокдауну! Продовжуємо бій!"
        )
        conn.close()
        await send_fight_message(match_id)
        return
    
    c.execute("DELETE FROM knockdowns WHERE match_id = ? AND player_id = ?", (match_id, player_id))
    conn.commit()
    conn.close()
    await end_match(match_id, player_id, opponent_id, p1_health, p2_health)

# Обробка раунду
async def process_round(match_id, timed_out=False):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute(
        """SELECT player1_id, player2_id, player1_action, player2_action, player1_health, player1_stamina,
        player2_health, player2_stamina, current_round, start_time, distance FROM matches WHERE match_id = ?""",
        (match_id,)
    )
    match = c.fetchone()
    player1_id, player2_id, p1_action, p2_action, p1_health, p1_stamina, p2_health, p2_stamina, round_num, start_time, distance = match
    
    c.execute("SELECT character_name, fighter_type FROM users WHERE user_id = ?", (player1_id,))
    p1_name, p1_type = c.fetchone()
    c.execute("SELECT character_name, fighter_type FROM users WHERE user_id = ?", (player2_id,))
    p2_name, p2_type = c.fetchone()
    
    c.execute("SELECT strength, reaction, punch_speed, stamina, health, will, footwork FROM fighter_stats WHERE user_id = ?", (player1_id,))
    p1_stats = c.fetchone()
    p1_strength, p1_reaction, p1_punch_speed, p1_stamina_stat, p1_max_health, p1_will, p1_footwork = p1_stats
    c.execute("SELECT strength, reaction, punch_speed, stamina, health, will, footwork FROM fighter_stats WHERE user_id = ?", (player2_id,))
    p2_stats = c.fetchone()
    p2_strength, p2_reaction, p2_punch_speed, p2_stamina_stat, p2_max_health, p2_will, p2_footwork = p2_stats
    
    if time.time() > start_time + 180:
        await end_match(match_id, None, None, p1_health, p2_health)
        logger.debug(f"Match {match_id} ended due to time limit")
        conn.close()
        return
    
    result_text = f"Раунд {round_num}\n"
    
    if timed_out:
        p1_action = "rest"
        p2_action = "rest"
        result_text += "Час минув! Обидва гравці відпочивають.\n"
    
    attack_params = {
        "jab": {"base_damage": 10, "stamina_cost": 6, "base_hit_chance": 0.9},
        "uppercut": {"base_damage": 25, "stamina_cost": 19, "base_hit_chance": 0.6},
        "hook": {"base_damage": 19, "stamina_cost": 15, "base_hit_chance": 0.75}
    }
    
    p1_action_result = ""
    p2_action_result = ""
    new_distance = distance
    
    # Логування стану перед обробкою
    logger.debug(f"Before round {round_num} for match {match_id}:")
    logger.debug(f"Player 1 ({p1_name}) health: {p1_health:.1f}/{p1_max_health:.1f}, stamina: {p1_stamina:.1f}, action: {p1_action}")
    logger.debug(f"Player 2 ({p2_name}) health: {p2_health:.1f}/{p2_max_health:.1f}, stamina: {p2_stamina:.1f}, action: {p2_action}")
    
    # Обробка дій руху
    if p1_action == "move_closer" and random.random() < 0.4 * p1_footwork:
        new_distance = "close"
        result_text += f"{p1_name} наближається до {p2_name}!\n"
        p1_action_result = "Ти наблизився!"
        p1_stamina -= 5
    elif p1_action == "move_away":
        if random.random() < 0.1:
            new_distance = "cornered_p1"
            result_text += f"{p1_name} відступає, але потрапляє в кут!\n"
            p1_action_result = "Ти потрапив у кут!"
        elif random.random() < 0.4 * p1_footwork:
            new_distance = "far"
            result_text += f"{p1_name} відступає від {p2_name}!\n"
            p1_action_result = "Ти відступив!"
        else:
            result_text += f"{p1_name} не вдалося відступити!\n"
            p1_action_result = "Відступ не вдався!"
        p1_stamina -= 5
    
    if p2_action == "move_closer" and random.random() < 0.4 * p2_footwork:
        new_distance = "close"
        result_text += f"{p2_name} наближається до {p1_name}!\n"
        p2_action_result = "Ти наблизився!"
        p2_stamina -= 5
    elif p2_action == "move_away":
        if random.random() < 0.1:
            new_distance = "cornered_p2"
            result_text += f"{p2_name} відступає, але потрапляє в кут!\n"
            p2_action_result = "Ти потрапив у кут!"
        elif random.random() < 0.4 * p2_footwork:
            new_distance = "far"
            result_text += f"{p2_name} відступає від {p1_name}!\n"
            p2_action_result = "Ти відступив!"
        else:
            result_text += f"{p2_name} не вдалося відступити!\n"
            p2_action_result = "Відступ не вдався!"
        p2_stamina -= 5
    
    # Обробка дії Гравця 1
    if p1_action in attack_params:
        params = attack_params[p1_action]
        hit_chance = params["base_hit_chance"] * (p1_reaction * p1_punch_speed / 2)
        if new_distance == "cornered_p2":
            hit_chance *= 1.1
        p1_stamina -= params["stamina_cost"]
        if p2_action not in ["dodge", "block"] and random.random() < hit_chance:
            if p1_action == "jab" and new_distance in ["far", "cornered_p1", "cornered_p2"]:
                damage = params["base_damage"] * p1_strength * p1_punch_speed
            else:
                damage = params["base_damage"] * p1_strength
            if p2_action == "move_away":
                damage /= 4
                result_text += f"{p1_name} завдає {p1_action} по {p2_name}, але той відступає! Урон: {damage:.1f}\n"
            else:
                result_text += f"{p1_name} завдає {p1_action} по {p2_name}! Урон: {damage:.1f}\n"
            p2_health -= damage
            p2_stamina -= damage / 10
            p1_action_result = "Ти влучив!"
            logger.debug(f"Player 1 dealt {damage:.1f} damage to Player 2 with {p1_action}, reduced stamina by {damage/10:.1f}")
        elif p2_action == "block":
            block_success_chance = (0.4 * p2_strength) * (p2_health / p2_max_health)
            p2_stamina -= 5
            if random.random() < block_success_chance:
                damage = 0.1 * params["base_damage"] * p1_strength
                result_text += f"{p1_name} завдає {p1_action}, але {p2_name} успішно блокує! Урон: {damage:.1f}\n"
                p1_action_result = "Ти влучив, але суперник успішно заблокував!"
                p2_action_result = "Ти успішно заблокував!"
                p2_health -= damage
                p2_stamina -= damage / 10
                logger.debug(f"Player 2 blocked, reduced damage: {damage:.1f}, stamina: {damage/10:.1f}")
            else:
                damage = 0.8 * params["base_damage"] * p1_strength
                result_text += f"{p1_name} завдає {p1_action}, але {p2_name} невдало блокує! Урон: {damage:.1f}\n"
                p1_action_result = "Ти влучив, суперник невдало заблокував!"
                p2_action_result = "Твій блок провалився!"
                p2_health -= damage
                p2_stamina -= damage / 10
                logger.debug(f"Player 2 failed block, damage: {damage:.1f}, stamina: {damage/10:.1f}")
        elif p2_action == "dodge":
            dodge_chance = min(0.8, 0.4 * p2_reaction * p2_punch_speed)
            p2_stamina -= 10
            if random.random() < dodge_chance:
                result_text += f"{p1_name} завдає {p1_action}, але {p2_name} ухилився!\n"
                p1_action_result = "Ти промахнувся!"
                p2_action_result = "Ти ухилився!"
                logger.debug(f"Player 2 dodged Player 1's {p1_action}")
                if new_distance == "close" and p2_type == "counter_puncher" and random.random() < 0.5:
                    counter_damage = params["base_damage"] * p2_strength * 1.5
                    p1_health -= counter_damage
                    p1_stamina -= counter_damage / 10
                    result_text += f"{p2_name} контратакує після ухилення! Урон: {counter_damage:.1f}\n"
                    logger.debug(f"Player 2 counter-attacked, critical damage: {counter_damage:.1f}, stamina: {counter_damage/10:.1f}")
            else:
                if p1_action == "jab" and new_distance in ["far", "cornered_p1", "cornered_p2"]:
                    damage = params["base_damage"] * p1_strength * p1_punch_speed
                else:
                    damage = params["base_damage"] * p1_strength
                p2_health -= damage
                p2_stamina -= damage / 10
                result_text += f"{p1_name} завдає {p1_action} по {p2_name}! Ухилення не вдалося. Урон: {damage:.1f}\n"
                p1_action_result = "Ти влучив!"
                p2_action_result = "Ухилення не вдалося!"
                logger.debug(f"Player 2 failed dodge, damage: {damage:.1f}, stamina: {damage/10:.1f}")
    elif p1_action == "dodge":
        p1_stamina -= 10
        result_text += f"{p1_name} намагається ухилитися.\n"
        p1_action_result = "Ти намагався ухилитися."
        logger.debug(f"Player 1 attempted dodge")
    elif p1_action == "block":
        p1_stamina -= 5
        result_text += f"{p1_name} блокує.\n"
        p1_action_result = "Ти блокуєш."
        logger.debug(f"Player 1 blocked")
    elif p1_action == "rest":
        p1_stamina = min(p1_stamina + 30 * p1_stamina_stat, 100)
        result_text += f"{p1_name} відпочиває.\n"
        p1_action_result = "Ти відпочиваєш."
        logger.debug(f"Player 1 rested, stamina: {p1_stamina:.1f}")
    
    # Обробка дії Гравця 2
    if p2_action in attack_params:
        params = attack_params[p2_action]
        hit_chance = params["base_hit_chance"] * (p2_reaction * p2_punch_speed / 2)
        if new_distance == "cornered_p1":
            hit_chance *= 1.1
        p2_stamina -= params["stamina_cost"]
        if p1_action not in ["dodge", "block"] and random.random() < hit_chance:
            if p2_action == "jab" and new_distance in ["far", "cornered_p1", "cornered_p2"]:
                damage = params["base_damage"] * p2_strength * p2_punch_speed
            else:
                damage = params["base_damage"] * p2_strength
            if p1_action == "move_away":
                damage /= 4
                result_text += f"{p2_name} завдає {p2_action} по {p1_name}, але той відступає! Урон: {damage:.1f}\n"
            else:
                result_text += f"{p2_name} завдає {p2_action} по {p1_name}! Урон: {damage:.1f}\n"
            p1_health -= damage
            p1_stamina -= damage / 10
            p2_action_result = "Ти влучив!"
            logger.debug(f"Player 2 dealt {damage:.1f} damage to Player 1 with {p2_action}, reduced stamina by {damage/10:.1f}")
        elif p1_action == "block":
            block_success_chance = (0.4 * p1_strength) * (p1_health / p1_max_health)
            p1_stamina -= 5
            if random.random() < block_success_chance:
                damage = 0.1 * params["base_damage"] * p2_strength
                result_text += f"{p2_name} завдає {p2_action}, але {p1_name} успішно блокує! Урон: {damage:.1f}\n"
                p2_action_result = "Ти влучив, але суперник успішно заблокував!"
                p1_action_result = "Ти успішно заблокував!"
                p1_health -= damage
                p1_stamina -= damage / 10
                logger.debug(f"Player 1 blocked, reduced damage: {damage:.1f}, stamina: {damage/10:.1f}")
            else:
                damage = 0.8 * params["base_damage"] * p2_strength
                result_text += f"{p2_name} завдає {p2_action}, але {p1_name} невдало блокує! Урон: {damage:.1f}\n"
                p2_action_result = "Ти влучив, суперник невдало заблокував!"
                p1_action_result = "Твій блок провалився!"
                p1_health -= damage
                p1_stamina -= damage / 10
                logger.debug(f"Player 1 failed block, damage: {damage:.1f}, stamina: {damage/10:.1f}")
        elif p1_action == "dodge":
            dodge_chance = min(0.8, 0.4 * p1_reaction * p1_punch_speed)
            p1_stamina -= 10
            if random.random() < dodge_chance:
                result_text += f"{p2_name} завдає {p2_action}, але {p1_name} ухилився!\n"
                p2_action_result = "Ти промахнувся!"
                p1_action_result = "Ти ухилився!"
                logger.debug(f"Player 1 dodged Player 2's {p2_action}")
                if new_distance == "close" and p1_type == "counter_puncher" and random.random() < 0.5:
                    counter_damage = params["base_damage"] * p1_strength * 1.5
                    p2_health -= counter_damage
                    p2_stamina -= counter_damage / 10
                    result_text += f"{p1_name} контратакує після ухилення! Урон: {counter_damage:.1f}\n"
                    logger.debug(f"Player 1 counter-attacked, critical damage: {counter_damage:.1f}, stamina: {counter_damage/10:.1f}")
            else:
                if p2_action == "jab" and new_distance in ["far", "cornered_p1", "cornered_p2"]:
                    damage = params["base_damage"] * p2_strength * p2_punch_speed
                else:
                    damage = params["base_damage"] * p2_strength
                p1_health -= damage
                p1_stamina -= damage / 10
                result_text += f"{p2_name} завдає {p2_action} по {p1_name}! Ухилення не вдалося. Урон: {damage:.1f}\n"
                p2_action_result = "Ти влучив!"
                p1_action_result = "Ухилення не вдалося!"
                logger.debug(f"Player 1 failed dodge, damage: {damage:.1f}, stamina: {damage/10:.1f}")
    elif p2_action == "dodge":
        p2_stamina -= 10
        result_text += f"{p2_name} намагається ухилитися.\n"
        p2_action_result = "Ти намагався ухилитися."
        logger.debug(f"Player 2 attempted dodge")
    elif p2_action == "block":
        p2_stamina -= 5
        result_text += f"{p2_name} блокує.\n"
        p2_action_result = "Ти блокуєш."
        logger.debug(f"Player 2 blocked")
    elif p2_action == "rest":
        p2_stamina = min(p2_stamina + 30 * p2_stamina_stat, 100)
        result_text += f"{p2_name} відпочиває.\n"
        p2_action_result = "Ти відпочиваєш."
        logger.debug(f"Player 2 rested, stamina: {p2_stamina:.1f}")
    
    # Обмеження мінімального здоров’я та енергії
    p1_health = max(0, p1_health)
    p2_health = max(0, p2_health)
    p1_stamina = max(0, p1_stamina)
    p2_stamina = max(0, p2_stamina)
    
    # Відправка результатів дій
    if p1_action_result:
        await bot.send_message(player1_id, p1_action_result)
    if p2_action_result:
        await bot.send_message(player2_id, p2_action_result)
    
    # Логування стану після обробки
    logger.debug(f"After round {round_num} for match {match_id}:")
    logger.debug(f"Player 1 ({p1_name}) health: {p1_health:.1f}/{p1_max_health:.1f}, stamina: {p1_stamina:.1f}")
    logger.debug(f"Player 2 ({p2_name}) health: {p2_health:.1f}/{p2_max_health:.1f}, stamina: {p2_stamina:.1f}")
    
    # Перевірка нокдауну при нульовій енергії або здоров’ї
    if p1_health <= 0 and p2_health <= 0:
        await end_match(match_id, None, None, p1_health, p2_health)
        conn.close()
        return
    elif p1_health <= 0 or p1_stamina <= 0:
        await handle_knockdown(match_id, player1_id, player2_id, p1_name, p2_name)
        conn.close()
        return
    elif p2_health <= 0 or p2_stamina <= 0:
        await handle_knockdown(match_id, player2_id, player1_id, p2_name, p1_name)
        conn.close()
        return
    
    # Оновлення стану
    new_deadline = time.time() + 30
    c.execute(
        """UPDATE matches SET player1_health = ?, player1_stamina = ?, player2_health = ?, player2_stamina = ?,
        player1_action = NULL, player2_action = NULL, current_round = ?, action_deadline = ?, distance = ? WHERE match_id = ?""",
        (p1_health, p1_stamina, p2_health, p2_stamina, round_num + 1, new_deadline, new_distance, match_id)
    )
    conn.commit()
    
    p1_status_text = get_status_text(p1_name, p1_type, p1_health, p1_stamina, p1_max_health)
    p2_status_text = get_status_text(p2_name, p2_type, p2_health, p2_stamina, p2_max_health)
    
    distance_text = {
        "far": "Далеко",
        "close": "Близько",
        "cornered_p1": f"{p1_name} у куті!",
        "cornered_p2": f"{p2_name} у куті!"
    }[new_distance]
    
    await bot.send_message(
        player1_id,
        f"{result_text}\nДистанція: {distance_text}\n{p1_status_text}\n{p2_status_text}\nОбери дію (30 секунд):",
        reply_markup=get_fight_keyboard(match_id, new_distance)
    )
    await bot.send_message(
        player2_id,
        f"{result_text}\nДистанція: {distance_text}\n{p2_status_text}\n{p1_status_text}\nОбери дію (30 секунд):",
        reply_markup=get_fight_keyboard(match_id, new_distance)
    )
    logger.debug(f"Processed round {round_num} for match {match_id}")
    
    conn.close()

# Завершення матчу
async def end_match(match_id, loser_id, winner_id, p1_health, p2_health):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute(
        """SELECT player1_id, player2_id, current_round FROM matches WHERE match_id = ?""",
        (match_id,)
    )
    match = c.fetchone()
    if not match:
        logger.error(f"Match {match_id} not found for end_match")
        conn.close()
        return
    
    player1_id, player2_id, round_num = match
    c.execute("SELECT character_name FROM users WHERE user_id = ?", (player1_id,))
    p1_name = c.fetchone()[0]
    c.execute("SELECT character_name FROM users WHERE user_id = ?", (player2_id,))
    p2_name = c.fetchone()[0]
    
    # Визначення результату
    if loser_id is None and winner_id is None:
        if p1_health > p2_health:
            winner_id, loser_id = player1_id, player2_id
            result_text = f"Матч завершено після {round_num} раундів! Переможець: {p1_name} (залишилось здоров’я: {p1_health:.1f})!"
        elif p2_health > p1_health:
            winner_id, loser_id = player2_id, player1_id
            result_text = f"Матч завершено після {round_num} раундів! Переможець: {p2_name} (залишилось здоров’я: {p2_health:.1f})!"
        else:
            result_text = f"Матч завершено після {round_num} раундів! Нічия!"
    elif loser_id == player1_id:
        result_text = f"Матч завершено! {p1_name} програв через нокаут! Переможець: {p2_name}!"
    elif loser_id == player2_id:
        result_text = f"Матч завершено! {p2_name} програв через нокаут! Переможець: {p1_name}!"
    
    # Оновлення статусу матчу
    c.execute("UPDATE matches SET status = 'finished' WHERE match_id = ?", (match_id,))
    c.execute("DELETE FROM knockdowns WHERE match_id = ?", (match_id,))
    c.execute("UPDATE rooms SET status = 'finished' WHERE creator_id = ? OR opponent_id = ?", (player1_id, player2_id))
    conn.commit()
    conn.close()
    
    # Відправка повідомлень
    await bot.send_message(player1_id, result_text)
    await bot.send_message(player2_id, result_text)
    logger.debug(f"Match {match_id} ended: {result_text}")

# Команди адмінів
@dp.message(Command("admin_setting"))
async def admin_setting(message: types.Message, state: FSMContext):
    logger.debug(f"Received /admin_setting from user {message.from_user.id}")
    await reset_state(message, state)
    if message.from_user.id not in ADMIN_IDS:
        await message.reply("У тебе немає доступу до адмін-панелі!")
        logger.debug(f"Unauthorized admin access attempt by user {message.from_user.id}")
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Увімкнути технічні роботи", callback_data="maintenance_on")],
        [InlineKeyboardButton(text="Вимкнути технічні роботи", callback_data="maintenance_off")],
    ])
    await message.reply("Адмін-панель:", reply_markup=keyboard)
    logger.debug(f"Admin panel opened for user {message.from_user.id}")

@dp.callback_query(lambda c: c.data in ["maintenance_on", "maintenance_off"])
async def handle_admin_action(callback: types.CallbackQuery):
    logger.debug(f"Received admin action from user {callback.from_user.id}: {callback.data}")
    if callback.from_user.id not in ADMIN_IDS:
        await callback.message.reply("У тебе немає доступу до адмін-панелі!")
        logger.debug(f"Unauthorized admin action attempt by user {callback.from_user.id}")
        await callback.answer()
        return
    
    global maintenance_mode
    if callback.data == "maintenance_on":
        maintenance_mode = True
        await callback.message.reply("Технічні роботи увімкнено. Доступ обмежено для всіх, крім адмінів.")
        logger.info("Maintenance mode enabled")
    else:
        maintenance_mode = False
        await callback.message.reply("Технічні роботи вимкнено. Бот доступний для всіх.")
        logger.info("Maintenance mode disabled")
    
    await callback.answer()

@dp.message(Command("maintenance_on"))
async def maintenance_on(message: types.Message, state: FSMContext):
    logger.debug(f"Received /maintenance_on from user {message.from_user.id}")
    await reset_state(message, state)
    if message.from_user.id not in ADMIN_IDS:
        await message.reply("У тебе немає доступу до цієї команди!")
        logger.debug(f"Unauthorized /maintenance_on attempt by user {message.from_user.id}")
        return
    global maintenance_mode
    maintenance_mode = True
    await message.reply("Технічні роботи увімкнено. Доступ обмежено для всіх, крім адмінів.")
    logger.info("Maintenance mode enabled")

@dp.message(Command("maintenance_off"))
async def maintenance_off(message: types.Message, state: FSMContext):
    logger.debug(f"Received /maintenance_off from user {message.from_user.id}")
    await reset_state(message, state)
    if message.from_user.id not in ADMIN_IDS:
        await message.reply("У тебе немає доступу до цієї команди!")
        logger.debug(f"Unauthorized /maintenance_off attempt by user {message.from_user.id}")
        return
    global maintenance_mode
    maintenance_mode = False
    await message.reply("Технічні роботи вимкнено. Бот доступний для всіх.")
    logger.info("Maintenance mode disabled")

# Налаштування вебхука
async def on_startup():
    await setup_bot_commands()
    webhook_url = f"https://box-manager-bot.onrender.com/webhook/{TELEGRAM_TOKEN}"
    await bot.set_webhook(webhook_url)
    logger.info(f"Webhook set to {webhook_url}")

async def on_shutdown():
    await bot.delete_webhook()
    await storage.close()
    await bot.session.close()
    logger.info("Bot shutdown")

# Обробка вебхука
async def handle_webhook(request):
    update = types.Update(**(await request.json()))
    await dp.feed_update(bot, update)
    return web.Response()

# Запуск бота
async def main():
    try:
        # Ініціалізація веб-сервера
        app = web.Application()
        app.router.add_post(f"/webhook/{TELEGRAM_TOKEN}", handle_webhook)
        
        # Налаштування старту та завершення
        app.on_startup.append(lambda _: on_startup())
        app.on_shutdown.append(lambda _: on_shutdown())
        
        # Запуск веб-сервера
        runner = web.AppRunner(app)
        await runner.setup()
        port = int(os.getenv("PORT", 10000))  # Render передає порт через змінну PORT
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        logger.info(f"Server started on port {port}")
        
        # Утримання програми в робочому стані
        await asyncio.Event().wait()
    except Exception as e:
        logger.error(f"Error in main: {e}")
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")
    except Exception as e:
        logger.error(f"Fatal error: {e}")