from flask import Flask, request
from telegram import Update, Bot, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
import sqlite3
import os
import asyncio
import logging

# Налаштування логування
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Локальні налаштування (для локального запуску)
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

# Ініціалізація Flask і Telegram Bot
app = Flask(__name__)
bot = Bot(token=TELEGRAM_TOKEN)

# Ініціалізація Application
app_telegram = Application.builder().token(TELEGRAM_TOKEN).build()

# Ініціалізація бази даних SQLite
def init_db():
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        character_name TEXT UNIQUE
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

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_maintenance(update, context):
        return
    await update.message.reply_text(
        "Вітаємо у Box Manager Online! Використовуй /create_account, щоб створити акаунт."
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
        await update.message.reply_text(f"Акаунт створено! Персонаж: {character_name}")
    except sqlite3.IntegrityError:
        await update.message.reply_text("Цей нік уже зайнятий. Вибери інший.")
    finally:
        conn.close()
        context.user_data["awaiting_character_name"] = False

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
    conn.commit()
    conn.close()
    await update.message.reply_text("Акаунт видалено! Можеш створити новий за допомогою /create_account.")

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
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Доступ заборонено!")
        return
    global maintenance_mode
    maintenance_mode = True
    await update.message.reply_text("Технічні роботи увімкнено. Бот призупинено.")

# Вимкнення технічних робіт
async def maintenance_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Доступ заборонено!")
        return
    global maintenance_mode
    maintenance_mode = False
    await update.message.reply_text("Технічні роботи вимкнено. Бот активний.")

# Налаштування кастомного меню для адмінів
async def set_admin_commands():
    admin_commands = [
        BotCommand("admin_setting", "Адмін-панель"),
        BotCommand("maintenance_on", "Увімкнути тех. роботи"),
        BotCommand("maintenance_off", "Вимкнути тех. роботи"),
    ]
    for admin_id in ADMIN_IDS:
        try:
            await bot.set_my_commands(commands=admin_commands, scope={"type": "chat", "chat_id": admin_id})
            logger.info(f"Set admin commands for user {admin_id}")
        except Exception as e:
            logger.error(f"Failed to set commands for {admin_id}: {e}")

# Додаємо обробники команд
app_telegram.add_handler(CommandHandler("start", start))
app_telegram.add_handler(CommandHandler("create_account", create_account))
app_telegram.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_character_name))
app_telegram.add_handler(CommandHandler("delete_account", delete_account))
app_telegram.add_handler(CommandHandler("admin_setting", admin_setting))
app_telegram.add_handler(CommandHandler("maintenance_on", maintenance_on))
app_telegram.add_handler(CommandHandler("maintenance_off", maintenance_off))

# Ініціалізація Bot і Application
async def initialize_app():
    await bot.initialize()
    await app_telegram.initialize()
    await set_admin_commands()

# Викликаємо ініціалізацію при запуску
loop = asyncio.get_event_loop()
loop.run_until_complete(initialize_app())

# Вебхук (асинхронний)
@app.route("/webhook", methods=["POST"])
async def webhook():
    try:
        update = Update.de_json(request.get_json(), bot)
        if update:
            await app_telegram.process_update(update)
        return {"ok": True}
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return {"ok": False}, 500

# Health check для UptimeRobot
@app.route("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)