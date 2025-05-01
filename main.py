import logging
import os
import random
import sqlite3
import time
from flask import Flask, request, jsonify
from telegram import Update, Bot, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.request import HTTPXRequest
import httpx

# Налаштування логування
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Локальні налаштування
try:
    from config import TELEGRAM_TOKEN, Vadym_ID, Nazar_ID, DATABASE_URL
except ImportError:
    TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
    try:
        Vadym_ID = int(os.environ.get("Vadym_ID", 0))
        Nazar_ID = int(os.environ.get("Nazar_ID", 0))
    except (ValueError, TypeError) as e:
        logger.error(f"Error reading Vadym_ID or Nazar_ID: {e}")
        Vadym_ID, Nazar_ID = 0, 0
    DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///bot.db")

# Список адмінів
ADMIN_IDS = [id for id in [Vadym_ID, Nazar_ID] if id != 0]
logger.info(f"ADMIN_IDS: {ADMIN_IDS}")

# Налаштування HTTPX із більшим пулом з’єднань
request = HTTPXRequest(
    connection_pool_size=100
)

# Ініціалізація Flask
app = Flask(__name__)

# Ініціалізація Telegram Bot і Application
bot = Bot(token=TELEGRAM_TOKEN, request=request)
app_telegram = Application.builder().token(TELEGRAM_TOKEN).request(request).build()

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
    
    # Створюємо матч
    c.execute(
        """INSERT INTO matches (player1_id, player2_id, status, start_time, current_round, player1_health, player1_stamina, player2_health, player2_stamina)
        VALUES (?, ?, 'active', ?, 1, ?, ?, ?, ?)""",
        (user_id, opponent_id, time.time(), player_stats[0], player_stats[1], opponent_stats[0], opponent_stats[1])
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
    user_id = query.from_user.id
    callback_data = query.data.split("_")
    match_id, action = int(callback_data[1]), callback_data[2]
    
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT player1_id, player2_id, player1_action, player2_action, status FROM matches WHERE match_id = ?", (match_id,))
    match = c.fetchone()
    if not match or match[4] != "active":
        await query.message.reply_text("Матч завершено або не існує.")
        conn.close()
        return
    
    player1_id, player2_id, player1_action, player2_action = match
    
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

# Обробка раунду (спрощена, без повних формул)
async def process_round(match_id, context):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute(
        """SELECT player1_id, player2_id, player1_action, player2_action, player1_health, player1_stamina,
        player2_health, player2_stamina, current_round FROM matches WHERE match_id = ?""",
        (match_id,)
    )
    match = c.fetchone()
    player1_id, player2_id, p1_action, p2_action, p1_health, p1_stamina, p2_health, p2_stamina, round_num = match
    
    # Спрощена логіка (для тесту)
    result_text = f"Раунд {round_num}\n"
    result_text += f"Гравець 1: {p1_action}\n"
    result_text += f"Гравець 2: {p2_action}\n"
    
    # Оновлюємо стан (приклад: атака зменшує здоров’я)
    if p1_action == "attack" and p2_action != "dodge":
        p2_health -= 10  # Тестове зменшення
        result_text += "Гравець 1 вдарив Гравця 2!\n"
    if p2_action == "attack" and p1_action != "dodge":
        p1_health -= 10
        result_text += "Гравець 2 вдарив Гравця 1!\n"
    
    # Оновлюємо матч
    c.execute(
        """UPDATE matches SET player1_health = ?, player1_stamina = ?, player2_health = ?, player2_stamina = ?,
        player1_action = NULL, player2_action = NULL, current_round = ? WHERE match_id = ?""",
        (p1_health, p1_stamina, p2_health, p2_stamina, round_num + 1, match_id)
    )
    conn.commit()
    
    # Відправляємо результати
    status_text = (
        f"Стан:\nГравець 1: HP {p1_health}, Виносливість {p1_stamina}\n"
        f"Гравець 2: HP {p2_health}, Виносливість {p2_stamina}\n"
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

# Адмінська команда /admin_setting
async def admin_setting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"User {user_id} tried /admin_setting. ADMIN_IDS: {ADMIN_IDS}")
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
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("Доступ заборонено!")
        return
    global maintenance_mode
    maintenance_mode = True
    await update.message.reply_text("Технічні роботи увімкнено. Бот призупинено.")

# Вимкнення технічних робіт
async def maintenance_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
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

# Вебхук для Flask
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json()
        logger.info(f"Webhook received data: {data}")
        update = Update.de_json(data, bot)
        if update:
            app_telegram.process_update(update)
            logger.info("Webhook processed update successfully")
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

# Ініціалізація та запуск polling
if __name__ == "__main__":
    import asyncio
    async def disable_webhook():
        try:
            await bot.delete_webhook()
            logger.info("Webhook disabled successfully")
        except Exception as e:
            logger.error(f"Failed to disable webhook: {e}")

    loop = asyncio.get_event_loop()
    loop.run_until_complete(disable_webhook())
    loop.run_until_complete(bot.initialize())
    loop.run_until_complete(app_telegram.initialize())
    app_telegram.run_polling()