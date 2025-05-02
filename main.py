import logging
import os
import random
import sqlite3
import time
import asyncio
import re
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
    # Очищення активних матчів і нокдаунів при запуску
    c.execute("DELETE FROM matches WHERE status = 'active'")
    c.execute("DELETE FROM knockdowns")
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

# Скидання стану для всіх команд
async def reset_state(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        logger.debug(f"Resetting state for user {message.from_user.id} from {current_state}")
        await state.clear()

# Налаштування меню команд
async def setup_bot_commands():
    user_commands = [
        BotCommand(command="/start", description="Почати роботу з ботом"),
        BotCommand(command="/create_account", description="Створити акаунт"),
        BotCommand(command="/delete_account", description="Видалити акаунт"),
        BotCommand(command="/start_match", description="Почати матч"),
        BotCommand(command="/refresh_commands", description="Оновити меню команд")
    ]
    
    admin_commands = user_commands + [
        BotCommand(command="/admin_setting", description="Адмін-панель"),
        BotCommand(command="/maintenance_on", description="Увімкнути технічні роботи"),
        BotCommand(command="/maintenance_off", description="Вимкнути технічні роботи")
    ]
    
    try:
        # Очищення старих команд
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
            "🔥 *Swarmer*: Агресивний боєць. Висока сила (1.5), воля (1.5), швидкість удару (1.35). Здоров’я: 120, виносливість: 1.1.\n"
            "🥊 *Out-boxer*: Витривалий і тактичний. Висока виносливість (1.5), здоров’я (200). Сила: 1.15, воля: 1.3.\n"
            "⚡ *Counter-puncher*: Майстер контратаки. Висока реакція (1.5), швидкість удару (1.5). Сила: 1.25, здоров’я: 100, воля: 1.2."
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
            "health": 120,
            "punch_speed": 1.35,
            "will": 1.5
        },
        "out_boxer": {
            "stamina": 1.5,
            "strength": 1.15,
            "reaction": 1.1,
            "health": 200,
            "punch_speed": 1.1,
            "will": 1.3
        },
        "counter_puncher": {
            "stamina": 1.15,
            "strength": 1.25,
            "reaction": 1.5,
            "health": 100,
            "punch_speed": 1.5,
            "will": 1.2
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
            """INSERT INTO fighter_stats (user_id, fighter_type, stamina, strength, reaction, health, punch_speed, will)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, fighter_type, stats["stamina"], stats["strength"], stats["reaction"],
             stats["health"], stats["punch_speed"], stats["will"])
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
    conn.commit()
    conn.close()
    await message.reply("Акаунт видалено! Можеш створити новий за допомогою /create_account.")
    logger.debug(f"Deleted account for user {user_id}")

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
    matchmaking_event.set()  # Сповіщення про нового гравця
    
    await message.reply("Пошук суперника... (макс. 30 секунд)")
    
    try:
        # Очікування другого гравця або таймаут
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
                
                action_deadline = time.time() + 30  # Збільшено до 30 секунд
                c.execute(
                    """INSERT INTO matches (player1_id, player2_id, status, start_time, current_round, player1_health, player1_stamina, player2_health, player2_stamina, action_deadline)
                    VALUES (?, ?, 'active', ?, 1, ?, ?, ?, ?, ?)""",
                    (user_id, opponent_id, time.time(), player_stats[0], player_stats[1], opponent_stats[0], opponent_stats[1], action_deadline)
                )
                conn.commit()
                match_id = c.lastrowid
                conn.close()
                
                keyboard = get_fight_keyboard(match_id)
                await message.reply(
                    f"Матч розпочато! Ти ({user[1]}, {user[2].capitalize()}) проти {opponent[1]} ({opponent[2].capitalize()}). Бій триває 3 хвилини. Обери дію (30 секунд):",
                    reply_markup=keyboard
                )
                await bot.send_message(
                    chat_id=opponent_id,
                    text=f"Матч розпочато! Ти ({opponent[1]}, {opponent[2].capitalize()}) проти {user[1]} ({user[2].capitalize()}). Бій триває 3 хвилини. Обери дію (30 секунд):",
                    reply_markup=keyboard
                )
                logger.debug(f"Started match {match_id} for user {user_id} vs {opponent_id}")
                return
        
        # Якщо суперник не знайдений
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
def get_fight_keyboard(match_id):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Прямий удар", callback_data=f"fight_{match_id}_jab"),
            InlineKeyboardButton(text="Апперкот", callback_data=f"fight_{match_id}_uppercut"),
            InlineKeyboardButton(text="Хук", callback_data=f"fight_{match_id}_hook")
        ],
        [
            InlineKeyboardButton(text="Ухилитися", callback_data=f"fight_{match_id}_dodge"),
            InlineKeyboardButton(text="Блок", callback_data=f"fight_{match_id}_block"),
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
    c.execute("SELECT player1_id, player2_id, player1_action, player2_action, status, action_deadline FROM matches WHERE match_id = ?", (match_id,))
    match = c.fetchone()
    if not match or match[4] != "active":
        await callback.message.reply("Матч завершено або не існує.")
        logger.debug(f"Match {match_id} not active or does not exist")
        conn.close()
        await callback.answer()
        return
    
    player1_id, player2_id, player1_action, player2_action, status, action_deadline = match
    
    if time.time() > action_deadline:
        await callback.message.reply("Час для дії минув! Раунд завершено автоматично.")
        await process_round(match_id, timed_out=True)
        logger.debug(f"Match {match_id} round timed out")
        conn.close()
        await callback.answer()
        return
    
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

# Клавіатура для нокдауну
def get_knockdown_keyboard(match_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Встати", callback_data=f"knockdown_{match_id}_stand")]
    ])

# Обробка дії "Встати"
@dp.callback_query(lambda c: c.data.startswith("knockdown_"))
async def handle_knockdown_stand(callback: types.CallbackQuery):
    logger.debug(f"Received knockdown action from user {callback.from_user.id}: {callback.data}")
    user_id = callback.from_user.id
    match_id = int(callback.data.split("_")[1])
    
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT player_id, deadline FROM knockdowns WHERE match_id = ? AND player_id = ?", (match_id, user_id))
    knockdown = c.fetchone()
    if not knockdown:
        await callback.message.reply("Нокдаун завершено або не існує.")
        logger.debug(f"No knockdown for user {user_id} in match {match_id}")
        conn.close()
        await callback.answer()
        return
    
    if time.time() > knockdown[1]:
        await callback.message.reply("Час для вставання минув!")
        await end_match(match_id, user_id, None, 0, 100)  # Суперник перемагає
        logger.debug(f"Knockdown timeout for user {user_id} in match {match_id}")
        conn.close()
        await callback.answer()
        return
    
    c.execute("SELECT player1_id, player2_id, player1_health, player2_health, player1_stamina, player2_stamina FROM matches WHERE match_id = ?", (match_id,))
    match = c.fetchone()
    player1_id, player2_id, p1_health, p2_health, p1_stamina, p2_stamina = match
    
    c.execute("SELECT health FROM fighter_stats WHERE user_id = ?", (user_id,))
    max_health = c.fetchone()[0]
    
    if user_id == player1_id:
        p1_health = max(0, p1_health) + 0.2 * max_health
        p1_stamina = min(p1_stamina + 40, 100)
        c.execute(
            "UPDATE matches SET player1_health = ?, player1_stamina = ? WHERE match_id = ?",
            (p1_health, p1_stamina, match_id)
        )
    else:
        p2_health = max(0, p2_health) + 0.2 * max_health
        p2_stamina = min(p2_stamina + 40, 100)
        c.execute(
            "UPDATE matches SET player2_health = ?, player2_stamina = ? WHERE match_id = ?",
            (p2_health, p2_stamina, match_id)
        )
    
    c.execute("DELETE FROM knockdowns WHERE match_id = ? AND player_id = ?", (match_id, user_id))
    conn.commit()
    
    opponent_id = player2_id if user_id == player1_id else player1_id
    c.execute("SELECT character_name FROM users WHERE user_id = ?", (user_id,))
    player_name = c.fetchone()[0]
    c.execute("SELECT character_name FROM users WHERE user_id = ?", (opponent_id,))
    opponent_name = c.fetchone()[0]
    
    await callback.message.reply(
        f"{player_name} встав після нокдауну! Здоров’я: {p1_health if user_id == player1_id else p2_health:.1f}, Енергія: {p1_stamina if user_id == player1_id else p2_stamina:.1f}"
    )
    await bot.send_message(
        opponent_id,
        f"{player_name} встав після нокдауну! Продовжуємо бій!"
    )
    await send_fight_message(match_id)
    
    conn.close()
    await callback.answer()
    logger.debug(f"User {user_id} stood up in match {match_id}")

# Надсилання повідомлення про бій після нокдауну
async def send_fight_message(match_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute(
        """SELECT player1_id, player2_id, player1_health, player1_stamina, player2_health, player2_stamina, current_round
        FROM matches WHERE match_id = ?""",
        (match_id,)
    )
    match = c.fetchone()
    if not match:
        conn.close()
        return
    
    player1_id, player2_id, p1_health, p1_stamina, p2_health, p2_stamina, round_num = match
    c.execute("SELECT character_name, fighter_type FROM users WHERE user_id = ?", (player1_id,))
    p1_name, p1_type = c.fetchone()
    c.execute("SELECT character_name, fighter_type FROM users WHERE user_id = ?", (player2_id,))
    p2_name, p2_type = c.fetchone()
    c.execute("SELECT strength, reaction, punch_speed, stamina, health FROM fighter_stats WHERE user_id = ?", (player1_id,))
    p1_stats = c.fetchone()
    c.execute("SELECT strength, reaction, punch_speed, stamina, health FROM fighter_stats WHERE user_id = ?", (player2_id,))
    p2_stats = c.fetchone()
    
    p1_status_text = get_status_text(p1_name, p1_type, p1_health, p1_stamina, p1_stats)
    p2_status_text = get_status_text(p2_name, p2_type, p2_health, p2_stamina, p2_stats)
    
    keyboard = get_fight_keyboard(match_id)
    action_deadline = time.time() + 30
    c.execute("UPDATE matches SET action_deadline = ?, player1_action = NULL, player2_action = NULL WHERE match_id = ?", (action_deadline, match_id))
    conn.commit()
    
    await bot.send_message(
        player1_id,
        f"Раунд {round_num}\n{p1_status_text}\n{p2_status_text}\nОбери дію (30 секунд):",
        reply_markup=keyboard
    )
    await bot.send_message(
        player2_id,
        f"Раунд {round_num}\n{p2_status_text}\n{p1_status_text}\nОбери дію (30 секунд):",
        reply_markup=keyboard
    )
    
    conn.close()

# Формування тексту стану гравця
def get_status_text(name, fighter_type, health, stamina, stats):
    strength, reaction, punch_speed, stamina_stat, max_health = stats
    hit_modifier = reaction * punch_speed / 2
    return (
        f"{name} ({fighter_type.capitalize()}):\n"
        f"Здоров’я: {health:.1f}/{max_health:.1f}, Енергія: {stamina:.1f}/100\n"
        f"📊 Прямий удар: -6 енергії, урон {7 * strength:.1f}, шанс {min(100, 90 * hit_modifier):.1f}%\n"
        f"📊 Апперкот: -19 енергії, урон {25 * strength:.1f}, шанс {min(100, 60 * hit_modifier):.1f}%\n"
        f"📊 Хук: -15 енергії, урон {19 * strength:.1f}, шанс {min(100, 75 * hit_modifier):.1f}%\n"
        f"📊 Ухилення: -6 енергії, шанс {min(100, 40 * reaction * punch_speed):.1f}%\n"
        f"📊 Блок: -5 енергії, зменшення урону на {0.5 * stamina_stat * strength:.1f}\n"
        f"📊 Відпочинок: +30 енергії"
    )

# Обробка раунду
async def process_round(match_id, timed_out=False):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute(
        """SELECT player1_id, player2_id, player1_action, player2_action, player1_health, player1_stamina,
        player2_health, player2_stamina, current_round, start_time FROM matches WHERE match_id = ?""",
        (match_id,)
    )
    match = c.fetchone()
    player1_id, player2_id, p1_action, p2_action, p1_health, p1_stamina, p2_health, p2_stamina, round_num, start_time = match
    
    c.execute("SELECT character_name FROM users WHERE user_id = ?", (player1_id,))
    p1_name = c.fetchone()[0]
    c.execute("SELECT character_name FROM users WHERE user_id = ?", (player2_id,))
    p2_name = c.fetchone()[0]
    
    if time.time() > start_time + 180:
        await end_match(match_id, player1_id, player2_id, p1_health, p2_health)
        logger.debug(f"Match {match_id} ended due to time limit")
        conn.close()
        return
    
    c.execute("SELECT strength, reaction, punch_speed, stamina, health FROM fighter_stats WHERE user_id = ?", (player1_id,))
    p1_stats = c.fetchone()
    p1_strength, p1_reaction, p1_punch_speed, p1_stamina_stat, p1_max_health = p1_stats
    c.execute("SELECT strength, reaction, punch_speed, stamina, health FROM fighter_stats WHERE user_id = ?", (player2_id,))
    p2_stats = c.fetchone()
    p2_strength, p2_reaction, p2_punch_speed, p2_stamina_stat, p2_max_health = p2_stats
    
    result_text = f"Раунд {round_num}\n"
    
    if timed_out:
        p1_action = "rest"
        p2_action = "rest"
        result_text += "Час минув! Обидва гравці відпочивають.\n"
    
    attack_params = {
        "jab": {"base_damage": 7, "stamina_cost": 6, "base_hit_chance": 0.9},
        "uppercut": {"base_damage": 25, "stamina_cost": 19, "base_hit_chance": 0.6},
        "hook": {"base_damage": 19, "stamina_cost": 15, "base_hit_chance": 0.75}
    }
    
    # Обробка дії Гравця 1
    if p1_action in attack_params:
        params = attack_params[p1_action]
        hit_chance = params["base_hit_chance"] * (p1_reaction * p1_punch_speed / 2)
        p1_stamina -= params["stamina_cost"]
        if p2_action not in ["dodge", "block"] and random.random() < hit_chance:
            damage = params["base_damage"] * p1_strength
            p2_health -= damage
            result_text += f"{p1_name} завдає {p1_action} по {p2_name}! Урон: {damage:.1f}\n"
        elif p2_action == "block":
            block_strength = 0.5 * p2_stamina_stat * p2_strength
            damage = max(0, params["base_damage"] * p1_strength - block_strength)
            p2_health -= damage
            p2_stamina -= 5
            result_text += f"{p1_name} завдає {p1_action}, але {p2_name} блокує! Урон: {damage:.1f}\n"
        elif p2_action == "dodge":
            dodge_chance = 0.4 * p2_reaction * p2_punch_speed
            if random.random() < dodge_chance:
                p2_stamina -= 5
                result_text += f"{p1_name} завдає {p1_action}, але {p2_name} ухилився!\n"
            else:
                damage = params["base_damage"] * p1_strength
                p2_health -= damage
                result_text += f"{p1_name} завдає {p1_action} по {p2_name}! Ухилення не вдалося. Урон: {damage:.1f}\n"
    elif p1_action == "dodge":
        p1_stamina -= 6
        result_text += f"{p1_name} намагається ухилитися.\n"
    elif p1_action == "block":
        p1_stamina -= 5
        result_text += f"{p1_name} блокує.\n"
    elif p1_action == "rest":
        p1_stamina = min(p1_stamina + 30 * p1_stamina_stat, 100)
        result_text += f"{p1_name} відпочиває.\n"
    
    # Обробка дії Гравця 2
    if p2_action in attack_params:
        params = attack_params[p2_action]
        hit_chance = params["base_hit_chance"] * (p2_reaction * p2_punch_speed / 2)
        p2_stamina -= params["stamina_cost"]
        if p1_action not in ["dodge", "block"] and random.random() < hit_chance:
            damage = params["base_damage"] * p2_strength
            p1_health -= damage
            result_text += f"{p2_name} завдає {p2_action} по {p1_name}! Урон: {damage:.1f}\n"
        elif p1_action == "block":
            block_strength = 0.5 * p1_stamina_stat * p1_strength
            damage = max(0, params["base_damage"] * p2_strength - block_strength)
            p1_health -= damage
            p1_stamina -= 5
            result_text += f"{p2_name} завдає {p2_action}, але {p1_name} блокує! Урон: {damage:.1f}\n"
        elif p1_action == "dodge":
            dodge_chance = 0.4 * p1_reaction * p1_punch_speed
            if random.random() < dodge_chance:
                p1_stamina -= 5
                result_text += f"{p2_name} завдає {p2_action}, але {p1_name} ухилився!\n"
            else:
                damage = params["base_damage"] * p2_strength
                p1_health -= damage
                result_text += f"{p2_name} завдає {p2_action} по {p1_name}! Ухилення не вдалося. Урон: {damage:.1f}\n"
    elif p2_action == "dodge":
        p2_stamina -= 5
        result_text += f"{p2_name} намагається ухилитися.\n"
    elif p2_action == "block":
        p2_stamina -= 5
        result_text += f"{p2_name} блокує.\n"
    elif p2_action == "rest":
        p2_stamina = min(p2_stamina + 15 * p2_stamina_stat, 100)
        result_text += f"{p2_name} відпочиває.\n"
    
    # Перевірка нокдауну
    knockdown = False
    if p1_health <= 0 or p1_stamina <= 0:
        deadline = time.time() + 10
        c.execute(
            "INSERT INTO knockdowns (match_id, player_id, deadline) VALUES (?, ?, ?)",
            (match_id, player1_id, deadline)
        )
        conn.commit()
        result_text += f"{p1_name} у нокдауні! В нього 10 секунд, щоб встати!\n"
        await bot.send_message(
            player1_id,
            f"Ти в нокдауні! Натисни 'Встати' протягом 10 секунд!",
            reply_markup=get_knockdown_keyboard(match_id)
        )
        await bot.send_message(
            player2_id,
            f"{p1_name} у нокдауні! Чи встане він? Залишилось 10 секунд."
        )
        knockdown = True
        logger.debug(f"Player {p1_name} in knockdown for match {match_id}")
    
    if p2_health <= 0 or p2_stamina <= 0:
        deadline = time.time() + 10
        c.execute(
            "INSERT INTO knockdowns (match_id, player_id, deadline) VALUES (?, ?, ?)",
            (match_id, player2_id, deadline)
        )
        conn.commit()
        result_text += f"{p2_name} у нокдауні! В нього 10 секунд, щоб встати!\n"
        await bot.send_message(
            player2_id,
            f"Ти в нокдауні! Натисни 'Встати' протягом 10 секунд!",
            reply_markup=get_knockdown_keyboard(match_id)
        )
        await bot.send_message(
            player1_id,
            f"{p2_name} у нокдауні! Чи встане він? Залишилось 10 секунд."
        )
        knockdown = True
        logger.debug(f"Player {p2_name} in knockdown for match {match_id}")
    
    if knockdown:
        conn.close()
        return
    
    # Оновлення стану
    new_deadline = time.time() + 30
    c.execute(
        """UPDATE matches SET player1_health = ?, player1_stamina = ?, player2_health = ?, player2_stamina = ?,
        player1_action = NULL, player2_action = NULL, current_round = ?, action_deadline = ? WHERE match_id = ?""",
        (p1_health, p1_stamina, p2_health, p2_stamina, round_num + 1, new_deadline, match_id)
    )
    conn.commit()
    
    p1_status_text = get_status_text(p1_name, p1_type, p1_health, p1_stamina, p1_stats)
    p2_status_text = get_status_text(p2_name, p2_type, p2_health, p2_stamina, p2_stats)
    
    await bot.send_message(
        player1_id,
        f"{result_text}\n{p1_status_text}\n{p2_status_text}\nОбери дію (30 секунд):",
        reply_markup=get_fight_keyboard(match_id)
    )
    await bot.send_message(
        player2_id,
        f"{result_text}\n{p2_status_text}\n{p1_status_text}\nОбери дію (30 секунд):",
        reply_markup=get_fight_keyboard(match_id)
    )
    logger.debug(f"Processed round {round_num} for match {match_id}")
    
    conn.close()

# Завершення матчу
async def end_match(match_id, loser_id, winner_id, p1_health, p2_health):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT player1_id, player2_id FROM matches WHERE match_id = ?", (match_id,))
    match = c.fetchone()
    player1_id, player2_id = match
    
    c.execute("SELECT character_name FROM users WHERE user_id = ?", (player1_id,))
    p1_name = c.fetchone()[0]
    c.execute("SELECT character_name FROM users WHERE user_id = ?", (player2_id,))
    p2_name = c.fetchone()[0]
    
    c.execute("UPDATE matches SET status = 'finished' WHERE match_id = ?", (match_id,))
    c.execute("DELETE FROM knockdowns WHERE match_id = ?", (match_id,))
    conn.commit()
    conn.close()
    
    if p1_health <= 0 and p2_health <= 0:
        result = "Нічия! Обидва бійці втратили все здоров’я."
    elif loser_id == player1_id:
        result = f"{p2_name} переміг!"
    elif loser_id == player2_id:
        result = f"{p1_name} переміг!"
    else:
        result = "Матч завершено за часом. Переможець визначається за здоров’ям:\n"
        result += f"{p1_name}: HP {p1_health:.1f}\n{p2_name}: HP {p2_health:.1f}\n"
        result += f"{p1_name} переміг!" if p1_health > p2_health else f"{p2_name} переміг!" if p2_health > p1_health else "Нічия!"
    
    await bot.send_message(player1_id, result)
    await bot.send_message(player2_id, result)
    logger.debug(f"Ended match {match_id}: {result}")

# Адмінська команда /admin_setting
@dp.message(Command("admin_setting"))
async def admin_setting(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    logger.debug(f"Received /admin_setting from user {user_id}")
    await reset_state(message, state)
    if user_id not in ADMIN_IDS:
        await message.reply("Доступ заборонено!")
        logger.debug(f"Access denied for /admin_setting for user {user_id}")
        return
    await message.reply(
        "Адмін-панель:\n"
        "/maintenance_on - Увімкнути технічні роботи\n"
        "/maintenance_off - Вимкнути технічні роботи"
    )
    logger.debug(f"Sent admin panel to user {user_id}")

# Увімкнення технічних робіт
@dp.message(Command("maintenance_on"))
async def maintenance_on(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    logger.debug(f"Received /maintenance_on from user {user_id}")
    await reset_state(message, state)
    if user_id not in ADMIN_IDS:
        await message.reply("Доступ заборонено!")
        logger.debug(f"Access denied for /maintenance_on for user {user_id}")
        return
    global maintenance_mode
    maintenance_mode = True
    await message.reply("Технічні роботи увімкнено. Бот призупинено.")
    logger.debug("Maintenance mode enabled")

# Вимкнення технічних робіт
@dp.message(Command("maintenance_off"))
async def maintenance_off(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    logger.debug(f"Received /maintenance_off from user {user_id}")
    await reset_state(message, state)
    if user_id not in ADMIN_IDS:
        await message.reply("Доступ заборонено!")
        logger.debug(f"Access denied for /maintenance_off for user {user_id}")
        return
    global maintenance_mode
    maintenance_mode = False
    await message.reply("Технічні роботи вимкнено. Бот активний.")
    logger.debug("Maintenance mode disabled")

# Health check для UptimeRobot
async def health(request):
    logger.debug("Received /health request")
    return web.json_response({"status": "ok"})

# Webhook endpoint
async def webhook(request):
    logger.debug("Received webhook request")
    try:
        data = await request.json()
        logger.debug(f"Webhook data: {data}")
        update = types.Update(**data)
        await dp.feed_update(bot, update)
        logger.debug("Update processed successfully")
        return web.json_response({"ok": True})
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return web.json_response({"ok": False}, status=500)

# Налаштування aiohttp
app = web.Application()
app.router.add_get("/health", health)
app.router.add_post("/webhook", webhook)

# Асинхронна функція для запуску
async def main():
    logger.info("Starting bot...")
    try:
        async with bot.session:
            await bot.delete_webhook(drop_pending_updates=True)
            logger.info("Webhook disabled successfully")
            bot_info = await bot.get_me()
            logger.info(f"Bot info: {bot_info}")
            webhook_url = "https://boxmanagerbot.onrender.com/webhook"
            await bot.set_webhook(url=webhook_url)
            logger.info(f"Webhook set to {webhook_url}")
            await setup_bot_commands()
            logger.info("Bot commands set up successfully")
    except Exception as e:
        logger.error(f"Failed to set webhook or commands: {e}")
        raise

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
    port = int(os.environ.get("PORT", 10000))
    web.run_app(app, host="0.0.0.0", port=port)