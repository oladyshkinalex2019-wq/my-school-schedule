import os
import sqlite3
import json
import logging
import asyncio
import aiohttp_cors
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.client.session.aiohttp import AiohttpSession
from aiohttp import web

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
WEB_APP_URL = os.getenv("WEB_APP_URL")
PROXY_URL = os.getenv("PROXY_URL")

# --- Инициализация Базы Данных ---
def init_db():
    conn = sqlite3.connect("planner.db")
    cursor = conn.cursor()
    # Таблица для хранения расписания и настроек пользователя
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            schedule TEXT,
            reset_time TEXT DEFAULT '08:30'
        )
    """)
    # Таблица для хранения отмеченных галочек
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS task_states (
            task_key TEXT PRIMARY KEY,
            is_checked INTEGER
        )
    """)
    conn.commit()
    conn.close()

init_db()

# --- Настройка бота ---
session = None
if PROXY_URL and PROXY_URL.strip():
    clean_proxy = PROXY_URL.strip()
    if clean_proxy.startswith("socks5h://"):
        clean_proxy = clean_proxy.replace("socks5h://", "socks5://")
    session = AiohttpSession(proxy=clean_proxy)

bot = Bot(token=TOKEN, session=session) if session else Bot(token=TOKEN)
dp = Dispatcher()

# --- Веб-сервер (API для Mini App) ---
async def get_user_data(request):
    user_id = request.query.get("user_id")
    if not user_id:
        return web.json_response({"error": "No user_id"}, status=400)
    
    conn = sqlite3.connect("planner.db")
    cursor = conn.cursor()
    
    cursor.execute("SELECT schedule, reset_time FROM users WHERE user_id = ?", (user_id,))
    user_row = cursor.fetchone()
    
    # Получаем состояние всех галочек этого пользователя
    cursor.execute("SELECT task_key, is_checked FROM task_states WHERE task_key LIKE ?", (f"{user_id}_%",))
    tasks_rows = cursor.fetchall()
    conn.close()

    schedule = json.loads(user_row[0]) if user_row and user_row[0] else None
    reset_time = user_row[1] if user_row else "08:30"
    tasks = {row[0]: bool(row[1]) for row in tasks_rows}

    return web.json_response({
        "schedule": schedule,
        "reset_time": reset_time,
        "tasks": tasks
    })

async def save_user_schedule(request):
    data = await request.json()
    user_id = data.get("user_id")
    schedule = data.get("schedule")
    
    conn = sqlite3.connect("planner.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users (user_id, schedule) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET schedule = excluded.schedule
    """, (user_id, json.dumps(schedule, ensure_ascii=False)))
    conn.commit()
    conn.close()
    return web.json_response({"status": "ok"})

async def toggle_task_state(request):
    data = await request.json()
    task_key = data.get("key")
    is_checked = 1 if data.get("checked") else 0
    
    conn = sqlite3.connect("planner.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO task_states (task_key, is_checked) VALUES (?, ?)
        ON CONFLICT(task_key) DO UPDATE SET is_checked = excluded.is_checked
    """, (task_key, is_checked))
    conn.commit()
    conn.close()
    return web.json_response({"status": "ok"})

async def update_reset_time_api(request):
    data = await request.json()
    user_id = data.get("user_id")
    reset_time = data.get("reset_time")
    
    conn = sqlite3.connect("planner.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users (user_id, reset_time) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET reset_time = excluded.reset_time
    """, (user_id, reset_time))
    conn.commit()
    conn.close()
    return web.json_response({"status": "ok"})

# --- Команды бота ---
@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="📅 Открыть расписание", web_app=WebAppInfo(url=WEB_APP_URL))
        ]]
    )
    await message.answer(
        "Привет! Нажми на кнопку ниже, чтобы открыть расписание.\n\n"
        "⚙️ Чтобы изменить расписание, отправь команду /edit_schedule",
        reply_markup=keyboard
    )

@dp.message(Command("edit_schedule"))
async def edit_schedule_cmd(message: types.Message):
    instruction = (
        "📝 Инструкция по загрузке расписания:\n\n"
        "Скопируй текст ниже, измени предметы на свои и отправь мне одним сообщением:\n\n"
        "Пн: Математика, Физика, Русский\n"
        "Вт: Обществознание, История\n"
        "Ср: Алгебра, Геометрия\n"
        "Чт: Информатика, Физика\n"
        "Пт: Литература, Русский\n"
        "Сб: География, Английский"
    )
    await message.answer(instruction)

@dp.message(F.text.contains(":"))
async def parse_schedule(message: types.Message):
    lines = message.text.strip().split("\n")
    new_schedule = {}
    valid_days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб"]

    for line in lines:
        if ":" in line:
            day_part, lessons_part = line.split(":", 1)
            day = day_part.strip().capitalize()
            if day in valid_days:
                lessons = [l.strip() for l in lessons_part.split(",") if l.strip()]
                new_schedule[day] = lessons

    if new_schedule:
        conn = sqlite3.connect("planner.db")
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO users (user_id, schedule) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET schedule = excluded.schedule
        """, (message.from_user.id, json.dumps(new_schedule, ensure_ascii=False)))
        conn.commit()
        conn.close()

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="📅 Открыть расписание", web_app=WebAppInfo(url=WEB_APP_URL))
            ]]
        )
        await message.answer("✅ Расписание сохранено в базе данных!", reply_markup=keyboard)
    else:
        await message.answer("❌ Не удалось распознать расписание.")

# --- Запуск приложения ---
async def main():
    logging.basicConfig(level=logging.INFO)
    
    app = web.Application()

    # Настраиваем CORS
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
            allow_methods=["GET", "POST", "OPTIONS"]
        )
    })

    # Добавляем маршруты
    r_get = app.router.add_get("/api/get_data", get_user_data)
    r_save = app.router.add_post("/api/save_schedule", save_user_schedule)
    r_toggle = app.router.add_post("/api/toggle_task", toggle_task_state)
    r_reset = app.router.add_post("/api/save_reset_time", update_reset_time_api)

    # Вешаем CORS на каждый маршрут
    cors.add(r_get)
    cors.add(r_save)
    cors.add(r_toggle)
    cors.add(r_reset)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())