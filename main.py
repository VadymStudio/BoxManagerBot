import logging
import os
import random
import sqlite3
import time
import asyncio
from flask import Flask, request, jsonify
from telegram import Update, Bot, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.request import HTTPXRequest
import httpx
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

# Налаштування HTTPX із більшим пулом з’єднань
telegram_request = HTTPXRequest(
    connection_pool_size=100
)

# Ініціалізація Flask
app = Flask(__name__)

# Ініціалізація Telegram Bot і Application
bot = Bot(token=TELEGRAM_TOKEN, request=telegram_request)
app_telegram = Application.builder().token(TELEGRAM_TOKEN).request(telegram_request).build()

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
    conn.commit()
    conn.close()

init_db()

# Перевірка maintenance mode
maintenance_mode = False

async def check_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if maintenance_mode and update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Бот на технічних роботах. Спробуй пізніше.")
        return False
    return True

# Налаштування кастомного меню для адміна
async def set_admin_commands(user_id):
    admin_commands = [
        BotCommand("admin_setting", "Адмін-панель"),
        BotCommand("maintenance_on", "Увімкнути тех. роботи"),
        BotCommand("maintenance_off", "Вимкнути тех. роботи"),
    ]
    try:
        await bot.set_my_commands(commands=admin_commands, scope={"type": "chat", "chat_id": user_id})
        logger.info(f"Set admin commands for user {user_id}")
    except Exception as e:
        logger.error(f"Failed to set commands for {user_id}: {e}")

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.debug(f"Received /start from user {update.effective_user.id}")
    user_id = update.effective_user.id
    if user_id in ADMIN_IDS and not context.user_data.get(f"admin_commands_set_{user_id}"):
        await set_admin_commands(user_id)
        context.user_data[f"admin_commands_set_{user_id}"] = True
    if not await check_maintenance(update, context):
        return
    await update.message.reply_text(
        "Вітаємо у Box Manager Online! Використовуй /create_account, щоб створити акаунт, або /start_match, щоб почати бій."
    )

# Команда /create_account
async def create_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.debug(f"Received /create_account from user {update.effective_user.id}")
    if not await check_maintenance(update, context):
        return
    user_id = update.effective_user.id
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if c.fetchone():
        await update.message.reply_text("Ти вже маєш акаунт! Використай /delete_account, щоб видалити його.")
        conn.close()
        return
    context.user_data["awaiting_character_name"] = True
    await update.message.reply_text("Введи ім'я персонажа (нік у грі):")
    conn.close()

# Обробка текстового вводу для імені персонажа
async def handle_character_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.debug(f"Received text input from user {update.effective_user.id}: {update.message.text}")
    if not context.user_data.get("awaiting_character_name"):
        return
    if not await check_maintenance(update, context):
        return
    user_id = update.effective_user.id
    character_name = update.message.text.strip()
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT character_name FROM users WHERE character_name = ?", (character_name,))
    if c.fetchone():
        await update.message.reply_text("Цей нік уже зайнятий. Вибери інший.")
        conn.close()
        return
    try:
        c.execute(
            "INSERT INTO users (user_id, username, character_name) VALUES (?, ?, ?)",
            (user_id, update.effective_user.username, character_name),
        )
        conn.commit()
        context.user_data["awaiting_character_name"] = False
        context.user_data["awaiting_fighter_type"] = True
        context.user_data["character_name"] = character_name
        fighter_descriptions = (
            "Вибери тип бійця (змінити вибір потім неможливо):\n\n"
            "🔥 *Swarmer*: Агресивний боєць. Висока сила (1.5), воля (1.5), швидкість удару (1.35). Здоров’я: 120, виносливість: 1.1.\n"
            "🥊 *Out-boxer*: Витривалий і тактичний. Висока виносливість (1.5), здоров’я (200). Сила: 1.15, воля: 1.3.\n"
            "⚡ *Counter-puncher*: Майстер контратаки. Висока реакція (1.5), швидкість удару (1.5). Сила: 1.25, здоров’я: 100, воля: 1.2."
        )
        keyboard = [
            [InlineKeyboardButton("Swarmer", callback_data="swarmer")],
            [InlineKeyboardButton("Out-boxer", callback_data="out_boxer")],
            [InlineKeyboardButton("Counter-puncher", callback_data="counter_puncher")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(fighter_descriptions, reply_markup=reply_markup, parse_mode="Markdown")
    except sqlite3.IntegrityError:
        await update.message.reply_text("Цей нік уже зайнятий. Вибери інший.")
    finally:
        conn.close()

# Обробка вибору типу бійця
async def handle_fighter_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    logger.debug(f"Received fighter type selection from user {query.from_user.id}: {query.data}")
    if not context.user_data.get("awaiting_fighter_type"):
        await query.message.reply_text("Помилка: вибір бійця вже завершено.")
        return
    user_id = query.from_user.id
    fighter_type = query.data
    character_name = context.user_data.get("character_name")
    
    # Характеристики бійців
    fighter_stats = {
        "swarmer": {
            "stamina": 1.1,
            "total_stamina": 100,
            "strength": 1.5,
            "reaction": 1.1,
            "health": 120,
            "punch_speed": 1.35,
            "will": 1.5
        },
        "out_boxer": {
            "stamina": 1.5,
            "total_stamina": 100,
            "strength": 1.15,
            "reaction": 1.1,
            "health": 200,
            "punch_speed": 1.1,
            "will": 1.3
        },
        "counter_puncher": {
            "stamina": 1.15,
            "total_stamina": 100,
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
        # Оновлюємо тип бійця в таблиці users
        c.execute(
            "UPDATE users SET fighter_type = ? WHERE user_id = ?",
            (fighter_type, user_id)
        )
        # Додаємо характеристики в таблицю fighter_stats
        stats = fighter_stats[fighter_type]
        c.execute(
            """INSERT INTO fighter_stats (user_id, fighter_type, stamina, strength, reaction, health, punch_speed, will)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, fighter_type, stats["stamina"], stats["strength"], stats["reaction"],
             stats["health"], stats["punch_speed"], stats["will"])
        )
        conn.commit()
        await query.message.reply_text(f"Акаунт створено! Персонаж: {character_name}, Тип: {fighter_type.capitalize()}")
    except sqlite3.IntegrityError:
        await query.message.reply_text("Помилка при збереженні бійця. Спробуй ще раз.")
    finally:
        conn.close()
        context.user_data["awaiting_fighter_type"] = False
        context.user_data["character_name"] = None

# Команда /delete_account
async def delete_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.debug(f"Received /delete_account from user {update.effective_user.id}")
    if not await check_maintenance(update, context):
        return
    user_id = update.effective_user.id
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if not c.fetchone():
        await update.message.reply_text("У тебе немає акаунта!")
        conn.close()
        return
    c.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
    c.execute("DELETE FROM fighter_stats WHERE user_id = ?", (user_id,))
    c.execute("DELETE FROM matches WHERE player1_id = ? OR player2_id = ?", (user_id, user_id))
    conn.commit()
    conn.close()
    await update.message.reply_text("Акаунт видалено! Можеш створити новий за допомогою /create_account.")

# Команда /start_match
async def start_match(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.debug(f"Received /start_match from user {update.effective_user.id}")
    if not await check_maintenance(update, context):
        return
    user_id = update.effective_user.id
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT user_id, character_name, fighter_type FROM users WHERE user_id = ?", (user_id,))
    user = c.fetchone()
    if not user:
        await update.message.reply_text("Спочатку створи акаунт за допомогою /create_account!")
        conn.close()
        return
    
    # Перевіряємо, чи гравець уже в матчі
    c.execute("SELECT match_id FROM matches WHERE (player1_id = ? OR player2_id = ?) AND status = 'active'", (user_id, user_id))
    if c.fetchone():
        await update.message.reply_text("Ти вже в матчі! Закінчи поточний бій.")
        conn.close()
        return
    
    # Шукаємо іншого гравця
    c.execute("SELECT user_id, character_name, fighter_type FROM users WHERE user_id != ? AND user_id NOT IN (SELECT player1_id FROM matches WHERE status = 'active') AND user_id NOT IN (SELECT player2_id FROM matches WHERE status = 'active')", (user_id,))
    opponents = c.fetchall()
    if not opponents:
        await update.message.reply_text("Немає доступних суперників. Спробуй пізніше.")
        conn.close()
        return
    
    opponent = random.choice(opponents)
    opponent_id, opponent_name, opponent_type = opponent
    
    # Отримуємо характеристики гравців
    c.execute("SELECT health, total_stamina FROM fighter_stats WHERE user_id = ?", (user_id,))
    player_stats = c.fetchone()
    c.execute("SELECT health, total_stamina FROM fighter_stats WHERE user_id = ?", (opponent_id,))
    opponent_stats = c.fetchone()
    
    # Створюємо матч із дедлайном для дії
    action_deadline = time.time() + 15  # 15 секунд на дію
    c.execute(
        """INSERT INTO matches (player1_id, player2_id, status, start_time, current_round, player1_health, player1_stamina, player2_health, player2_stamina, action_deadline)
        VALUES (?, ?, 'active', ?, 1, ?, ?, ?, ?, ?)""",
        (user_id, opponent_id, time.time(), player_stats[0], player_stats[1], opponent_stats[0], opponent_stats[1], action_deadline)
    )
    conn.commit()
    match_id = c.lastrowid
    conn.close()
    
    # Відправляємо повідомлення обом гравцям
    await update.message.reply_text(
        f"Матч розпочато! Ти ({user[1]}, {user[2].capitalize()}) проти {opponent_name} ({opponent_type.capitalize()}). Бій триває 3 хвилини. Обери дію (15 секунд):",
        reply_markup=get_fight_keyboard(match_id)
    )
    await bot.send_message(
        chat_id=opponent_id,
        text=f"Матч розпочато! Ти ({opponent_name}, {opponent_type.capitalize()}) проти {user[1]} ({user[2].capitalize()}). Бій триває 3 хвилини. Обери дію (15 секунд):",
        reply_markup=get_fight_keyboard(match_id)
    )

# Клавіатура для бою
def get_fight_keyboard(match_id):
    keyboard = [
        [InlineKeyboardButton("Вдарити", callback_data=f"fight_{match_id}_attack")],
        [InlineKeyboardButton("Ухилитися", callback_data=f"fight_{match_id}_dodge")],
        [InlineKeyboardButton("Блок", callback_data=f"fight_{match_id}_block")],
        [InlineKeyboardButton("Відпочинок", callback_data=f"fight_{match_id}_rest")]
    ]
    return InlineKeyboardMarkup(keyboard)

# Обробка дій у бою
async def handle_fight_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    logger.debug(f"Received fight action from user {query.from_user.id}: {query.data}")
    user_id = query.from_user.id
    callback_data = query.data.split("_")
    match_id, action = int(callback_data[1]), callback_data[2]
    
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT player1_id, player2_id, player1_action, player2_action, status, action_deadline FROM matches WHERE match_id = ?", (match_id,))
    match = c.fetchone()
    if not match or match[4] != "active":
        await query.message.reply_text("Матч завершено або не існує.")
        conn.close()
        return
    
    player1_id, player2_id, player1_action, player2_action, status, action_deadline = match
    
    # Перевіряємо дедлайн
    if time.time() > action_deadline:
        await query.message.reply_text("Час для дії минув! Раунд завершено автоматично.")
        await process_round(match_id, context, timed_out=True)
        conn.close()
        return
    
    # Зберігаємо дію гравця
    if user_id == player1_id:
        c.execute("UPDATE matches SET player1_action = ? WHERE match_id = ?", (action, match_id))
    elif user_id == player2_id:
        c.execute("UPDATE matches SET player2_action = ? WHERE match_id = ?", (action, match_id))
    else:
        await query.message.reply_text("Ти не учасник цього матчу!")
        conn.close()
        return
    
    conn.commit()
    
    # Перевіряємо, чи обидва гравці вибрали дії
    c.execute("SELECT player1_action, player2_action FROM matches WHERE match_id = ?", (match_id,))
    actions = c.fetchone()
    if actions[0] and actions[1]:
        # Обидва вибрали, обробляємо раунд
        await process_round(match_id, context)
    
    conn.close()

# Обробка раунду з формулами
async def process_round(match_id, context, timed_out=False):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute(
        """SELECT player1_id, player2_id, player1_action, player2_action, player1_health, player1_stamina,
        player2_health, player2_stamina, current_round, start_time FROM matches WHERE match_id = ?""",
        (match_id,)
    )
    match = c.fetchone()
    player1_id, player2_id, p1_action, p2_action, p1_health, p1_stamina, p2_health, p2_stamina, round_num, start_time = match
    
    # Перевіряємо тривалість бою (3 хвилини = 180 секунд)
    if time.time() > start_time + 180:
        await end_match(match_id, context, player1_id, player2_id, p1_health, p2_health)
        conn.close()
        return
    
    # Отримуємо характеристики гравців
    c.execute("SELECT strength, reaction, punch_speed, stamina FROM fighter_stats WHERE user_id = ?", (player1_id,))
    p1_stats = c.fetchone()
    p1_strength, p1_reaction, p1_punch_speed, p1_stamina_stat = p1_stats
    c.execute("SELECT strength, reaction, punch_speed, stamina FROM fighter_stats WHERE user_id = ?", (player2_id,))
    p2_stats = c.fetchone()
    p2_strength, p2_reaction, p2_punch_speed, p2_stamina_stat = p2_stats
    
    result_text = f"Раунд {round_num}\ Wn"
    
    # Якщо тайм-аут, дії = "rest"
    if timed_out:
        p1_action = "rest"
        p2_action = "rest"
        result_text += "Час минув! Обидва гравці відпочивають.\n"
    
    # Формули
    base_damage = 10  # Базовий урон удару
    if p1_action == "attack" and p2_action != "dodge" and p2_action != "block":
        damage = base_damage * p1_strength  # Урон = урон прийому * Сила
        p2_health -= damage
        p1_stamina -= 10 * p1_stamina_stat
        result_text += f"Гравець 1 вдарив Гравця 2! Урон: {damage:.1f}\n"
    elif p1_action == "attack" and p2_action == "block":
        block_strength = 0.5 * p2_stamina_stat * p2_strength  # Блок = 0.5 * Виносливість * Сила
        damage = max(0, base_damage * p1_strength - block_strength)
        p2_health -= damage
        p1_stamina -= 10 * p1_stamina_stat
        result_text += f"Гравець 1 вдарив, але Гравець 2 блокував! Урон: {damage:.1f}\n"
    elif p1_action == "attack" and p2_action == "dodge":
        dodge_chance = 0.4 * p2_reaction * p2_punch_speed  # Ухилення = 0.4 * Реакція * Швидкість удару
        if random.random() < dodge_chance:
            result_text += "Гравець 1 вдарив, але Гравець 2 ухилився!\n"
        else:
            damage = base_damage * p1_strength
            p2_health -= damage
            p1_stamina -= 10 * p1_stamina_stat
            result_text += f"Гравець 1 вдарив Гравця 2! Ухилення не вдалося. Урон: {damage:.1f}\n"
    elif p1_action == "rest":
        p1_stamina = min(p1_stamina + 15 * p1_stamina_stat, 100)
        result_text += "Гравець 1 відпочиває.\n"
    
    if p2_action == "attack" and p1_action != "dodge" and p1_action != "block":
        damage = base_damage * p2_strength
        p1_health -= damage
        p2_stamina -= 10 * p2_stamina_stat
        result_text += f"Гравець 2 вдарив Гравця 1! Урон: {damage:.1f}\n"
    elif p2_action == "attack" and p1_action == "block":
        block_strength = 0.5 * p1_stamina_stat * p1_strength
        damage = max(0, base_damage * p2_strength - block_strength)
        p1_health -= damage
        p2_stamina -= 10 * p2_stamina_stat
        result_text += f"Гравець 2 вдарив, але Гравець 1 блокував! Урон: {damage:.1f}\n"
    elif p2_action == "attack" and p1_action == "dodge":
        dodge_chance = 0.4 * p1_reaction * p1_punch_speed
        if random.random() < dodge_chance:
            result_text += "Гравець 2 вдарив, але Гравець 1 ухилився!\n"
        else:
            damage = base_damage * p2_strength
            p1_health -= damage
            p2_stamina -= 10 * p2_stamina_stat
            result_text += f"Гравець 2 вдарив Гравця 1! Ухилення не вдалося. Урон: {damage:.1f}\n"
    elif p2_action == "rest":
        p2_stamina = min(p2_stamina + 15 * p2_stamina_stat, 100)
        result_text += "Гравець 2 відпочиває.\n"
    
    # Перевіряємо здоров’я
    if p1_health <= 0 or p2_health <= 0:
        await end_match(match_id, context, player1_id, player2_id, p1_health, p2_health)
        conn.close()
        return
    
    # Оновлюємо матч
    new_deadline = time.time() + 15  # Новий дедлайн
    c.execute(
        """UPDATE matches SET player1_health = ?, player1_stamina = ?, player2_health = ?, player2_stamina = ?,
        player1_action = NULL, player2_action = NULL, current_round = ?, action_deadline = ? WHERE match_id = ?""",
        (p1_health, p1_stamina, p2_health, p2_stamina, round_num + 1, new_deadline, match_id)
    )
    conn.commit()
    
    # Відправляємо результати
    status_text = (
        f"Стан:\nГравець 1: HP {p1_health:.1f}, Виносливість {p1_stamina:.1f}\n"
        f"Гравець 2: HP {p2_health:.1f}, Виносливість {p2_stamina:.1f}\n"
        "Обери наступну дію (15 секунд):"
    )
    await bot.send_message(
        chat_id=player1_id,
        text=result_text + status_text,
        reply_markup=get_fight_keyboard(match_id)
    )
    await bot.send_message(
        chat_id=player2_id,
        text=result_text + status_text,
        reply_markup=get_fight_keyboard(match_id)
    )
    
    conn.close()

# Завершення матчу
async def end_match(match_id, context, player1_id, player2_id, p1_health, p2_health):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("UPDATE matches SET status = 'finished' WHERE match_id = ?", (match_id,))
    conn.commit()
    conn.close()
    
    if p1_health <= 0 and p2_health <= 0:
        result = "Нічия! Обидва бійці втратили все здоров’я."
    elif p1_health <= 0:
        result = "Гравець 2 переміг!"
    elif p2_health <= 0:
        result = "Гравець 1 переміг!"
    else:
        result = "Матч завершено за часом. Переможець визначається за здоров’ям:\n"
        result += f"Гравець 1: HP {p1_health:.1f}\nГравець 2: HP {p2_health:.1f}\n"
        result += "Гравець 1 переміг!" if p1_health > p2_health else "Гравець 2 переміг!" if p2_health > p1_health else "Нічия!"
    
    await bot.send_message(chat_id=player1_id, text=result)
    await bot.send_message(chat_id=player2_id, text=result)

# Адмінська команда /admin_setting
async def admin_setting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"Received /admin_setting from user {user_id}. ADMIN_IDS: {ADMIN_IDS}")
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("Доступ заборонено!")
        return
    await update.message.reply_text(
        "Адмін-панель:\n"
        "/maintenance_on - Увімкнути технічні роботи\n"
        "/maintenance_off - Вимкнути технічні роботи"
    )

# Увімкнення технічних робіт
async def maintenance_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"Received /maintenance_on from user {user_id}")
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("Доступ заборонено!")
        return
    global maintenance_mode
    maintenance_mode = True
    await update.message.reply_text("Технічні роботи увімкнено. Бот призупинено.")

# Вимкнення технічних робіт
async def maintenance_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.debug(f"Received /maintenance_off from user {user_id}")
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("Доступ заборонено!")
        return
    global maintenance_mode
    maintenance_mode = False
    await update.message.reply_text("Технічні роботи вимкнено. Бот активний.")

# Додаємо обробники команд
app_telegram.add_handler(CommandHandler("start", start))
app_telegram.add_handler(CommandHandler("create_account", create_account))
app_telegram.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_character_name))
app_telegram.add_handler(CallbackQueryHandler(handle_fighter_type, pattern="^(swarmer|out_boxer|counter_puncher)$"))
app_telegram.add_handler(CallbackQueryHandler(handle_fight_action, pattern="^fight_"))
app_telegram.add_handler(CommandHandler("start_match", start_match))
app_telegram.add_handler(CommandHandler("delete_account", delete_account))
app_telegram.add_handler(CommandHandler("admin_setting", admin_setting))
app_telegram.add_handler(CommandHandler("maintenance_on", maintenance_on))
app_telegram.add_handler(CommandHandler("maintenance_off", maintenance_off))

# Вебхук для Flask (для дебагу або майбутнього використання)
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json()
        logger.debug(f"Webhook received data: {data}")
        update = Update.de_json(data, bot)
        if update:
            app_telegram.process_update(update)
            logger.debug("Webhook processed update successfully")
        else:
            logger.warning("Webhook received no valid update")
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"ok": False}), 500

# Health check для UptimeRobot
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

# Синхронне відключення вебхука
def disable_webhook_sync():
    try:
        response = httpx.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook")
        result = response.json()
        if result.get("ok"):
            logger.info("Webhook disabled successfully")
        else:
            logger.error(f"Failed to disable webhook: {result}")
    except Exception as e:
        logger.error(f"Failed to disable webhook: {e}")

# Ініціалізація та запуск polling
if __name__ == "__main__":
    logger.info("Starting bot...")
    disable_webhook_sync()  # Відключаємо вебхук
    try:
        logger.info("Initializing bot and starting polling...")
        app_telegram.run_polling(poll_interval=0.5, timeout=10, drop_pending_updates=True)
        logger.info("Polling started successfully")
    except Exception as e:
        logger.error(f"Polling failed: {e}")
        raise