from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
import sqlite3
import os
import ast
import asyncio

# Налаштування змінних середовища
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_IDS = ast.literal_eval(os.environ.get("ADMIN_IDS", "[]"))
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///bot.db")

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
        character_name TEXT
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

# Створення акаунта
async def create_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_maintenance(update, context):
        return
    user_id = update.effective_user.id
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if c.fetchone():
        await update.message.reply_text("Ти вже маєш акаунт!")
        conn.close()
        return
    args = context.args
    if len(args) < 1:
        await update.message.reply_text("Вкажи ім'я персонажа: /create_account <ім'я>")
        conn.close()
        return
    character_name = " ".join(args)
    c.execute(
        "INSERT INTO users (user_id, username, character_name) VALUES (?, ?, ?)",
        (user_id, update.effective_user.username, character_name),
    )
    conn.commit()
    conn.close()
    await update.message.reply_text(f"Акаунт створено! Персонаж: {character_name}")

# Адмінська команда /admin_setting
async def admin_setting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Доступ заборонено!")
        return
    await update.message.reply_text(
        "Адмін-панель:\n"
        "/maintenance_on - Увімкнути технічні роботи\n"
        "/maintenance_off - Вимкнути технічні роботи"
    )

# Увімкнення технічних робіт
async def maintenance_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global maintenance_mode
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Доступ заборонено!")
        return
    maintenance_mode = True
    await update.message.reply_text("Технічні роботи увімкнено. Бот призупинено.")

# Вимкнення технічних робіт
async def maintenance_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global maintenance_mode
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Доступ заборонено!")
        return
    maintenance_mode = False
    await update.message.reply_text("Технічні роботи вимкнено. Бот активний.")

# Додаємо обробники команд
app_telegram.add_handler(CommandHandler("start", start))
app_telegram.add_handler(CommandHandler("create_account", create_account))
app_telegram.add_handler(CommandHandler("admin_setting", admin_setting))
app_telegram.add_handler(CommandHandler("maintenance_on", maintenance_on))
app_telegram.add_handler(CommandHandler("maintenance_off", maintenance_off))

# Вебхук
@app.route("/webhook", methods=["POST"])
async def webhook():
    update = Update.de_json(request.get_json(), bot)
    if update:
        await app_telegram.process_update(update)
    return {"ok": True}

# Health check для UptimeRobot
@app.route("/health")
def health():
    return {"status": "ok"}

# Ініціалізація Application перед запуском
@app.before_first_request
def initialize_application():
    loop = asyncio.get_event_loop()
    loop.run_until_complete(app_telegram.initialize())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)