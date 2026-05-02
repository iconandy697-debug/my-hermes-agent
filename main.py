import os
import logging
import asyncio
import sqlite3
import openai
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

# 1. 基础配置
load_dotenv()
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
PORT = int(os.getenv("PORT", 8080))
DOMAIN = os.getenv("SLIPLANE_DOMAIN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# 持久化路径：对应 Sliplane 的 Mount Path
DATA_DIR = "/app/data"
DB_PATH = os.path.join(DATA_DIR, "hermes_memory.db")

# 确保目录在启动前已存在
if not os.path.exists(DATA_DIR):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except Exception as e:
        logging.error(f"无法创建数据目录: {e}")

app = FastAPI()
tg_app = Application.builder().token(TOKEN).build()
client = openai.AsyncOpenAI(api_key=OPENROUTER_API_KEY, base_url="https://openrouter.ai/api/v1")

# 2. 数据库初始化
def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS history 
                     (user_id TEXT, role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"数据库初始化失败: {e}")

# 3. 带记忆的对话逻辑
async def get_hermes_response(user_id: str, user_text: str):
    messages = [{"role": "system", "content": "你是一个专业的麻醉学助手 Hermes。"}]
    
    # 读取历史记录 (最近 6 条)
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT role, content FROM history WHERE user_id=? ORDER BY timestamp DESC LIMIT 6", (user_id,))
        rows = c.fetchall()[::-1]
        for role, content in rows:
            messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_text})
        
        response = await client.chat.completions.create(
            model="google/gemini-2.0-flash-001",
            messages=messages
        )
        answer = response.choices[0].message.content
        
        # 保存新对话
        c.execute("INSERT INTO history (user_id, role, content) VALUES (?, ?, ?)", (user_id, "user", user_text))
        c.execute("INSERT INTO history (user_id, role, content) VALUES (?, ?, ?)", (user_id, "assistant", answer))
        conn.commit()
        conn.close()
        return answer
    except Exception as e:
        logging.error(f"对话处理失败: {e}")
        return f"Hermes 目前有点健忘，但仍在运行。错误: {str(e)}"

# 4. Telegram 与 FastAPI 路由
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if ADMIN_ID and user_id != str(ADMIN_ID):
        return
    
    placeholder = await update.message.reply_text("🤔 正在查阅您的历史记忆...")
    answer = await get_hermes_response(user_id, update.message.text)
    await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=placeholder.message_id, text=answer)

@app.get("/")
async def health_check():
    return {"status": "healthy", "db_connected": os.path.exists(DB_PATH)}

@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return {"ok": True}

@app.on_event("startup")
async def on_startup():
    init_db() # 启动时先初始化数据库
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    await tg_app.initialize()
    if DOMAIN:
        await tg_app.bot.set_webhook(url=f"https://{DOMAIN}/webhook")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
