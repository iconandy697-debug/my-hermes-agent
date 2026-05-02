import os
import logging
import asyncio
import sqlite3
import openai
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

# --- 1. 基础配置与环境加载 ---
load_dotenv()
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
PORT = int(os.getenv("PORT", 8080))
DOMAIN = os.getenv("SLIPLANE_DOMAIN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# 持久化存储路径 (需在 Sliplane Volumes 中挂载到 /app/data)
DATA_DIR = "/app/data"
DB_PATH = os.path.join(DATA_DIR, "hermes_memory.db")

# 确保数据目录存在
if not os.path.exists(DATA_DIR):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except Exception as e:
        logging.error(f"无法创建数据目录: {e}")

# 初始化核心组件
app = FastAPI()
tg_app = Application.builder().token(TOKEN).build()
client = openai.AsyncOpenAI(
    api_key=OPENROUTER_API_KEY, 
    base_url="https://openrouter.ai/api/v1"
)

# --- 2. 数据库持久化逻辑 ---
def init_db():
    """初始化 SQLite 数据库"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS history 
                     (user_id TEXT, role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"数据库初始化失败: {e}")

async def get_chat_history(user_id: str, limit=6):
    """读取最近对话记录"""
    history = []
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT role, content FROM history WHERE user_id=? ORDER BY timestamp DESC LIMIT ?", (user_id, limit))
        rows = c.fetchall()[::-1]
        for role, content in rows:
            history.append({"role": role, "content": content})
        conn.close()
    except Exception as e:
        logging.error(f"读取历史失败: {e}")
    return history

async def save_chat_message(user_id: str, role: str, content: str):
    """保存对话消息"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO history (user_id, role, content) VALUES (?, ?, ?)", (user_id, role, content))
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"保存消息失败: {e}")

# --- 3. 业务逻辑 ---
async def get_hermes_response(user_id: str, user_text: str):
    """调用 OpenRouter"""
    messages = [{"role": "system", "content": "你是一个专业的麻醉学专家助理 Hermes。"}]
    history = await get_chat_history(user_id)
    messages.extend(history)
    messages.append({"role": "user", "content": user_text})
    
    try:
        response = await client.chat.completions.create(
            model="google/gemini-2.0-flash-001",
            messages=messages,
            extra_headers={
                "HTTP-Referer": f"https://{DOMAIN}" if DOMAIN else "http://localhost",
                "X-Title": "Hermes Med Agent",
            }
        )
        answer = response.choices[0].message.content
        await save_chat_message(user_id, "user", user_text)
        await save_chat_message(user_id, "assistant", answer)
        return answer
    except Exception as e:
        return f"❌ Hermes 脑回路连接失败: {str(e)}"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if ADMIN_ID and user_id != str(ADMIN_ID):
        await update.message.reply_text("🚫 未授权。")
        return

    placeholder = await update.message.reply_text("🤔 Hermes 正在检索历史并思考...")
    answer = await get_hermes_response(user_id, update.message.text)
    
    await context.bot.edit_message_text(
        chat_id=update.effective_chat.id,
        message_id=placeholder.message_id,
        text=answer
    )

# --- 4. 路由与生命周期管理 (核心修复) ---
@app.get("/")
async def health_check():
    return {"status": "healthy", "database": os.path.exists(DB_PATH)}

@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return {"ok": True}

@app.on_event("startup")
async def on_startup():
    init_db()
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    await tg_app.initialize()
    
    if DOMAIN:
        webhook_url = f"https://{DOMAIN}/webhook"
        # 针对 TimedOut 报错的重试逻辑
        for i in range(3):
            try:
                await tg_app.bot.set_webhook(url=webhook_url, connect_timeout=20, read_timeout=20)
                logging.info(f"✅ Webhook 设置成功: {webhook_url}")
                break
            except Exception as e:
                logging.error(f"⚠️ 第 {i+1} 次 Webhook 设置失败: {e}")
                if i < 2: await asyncio.sleep(5)
    else:
        logging.warning("未检测到 SLIPLANE_DOMAIN")

@app.on_event("shutdown")
async def on_shutdown():
    await tg_app.bot.delete_webhook()
    await tg_app.shutdown()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
