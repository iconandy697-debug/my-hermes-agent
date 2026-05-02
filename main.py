import os
import logging
import asyncio
import sqlite3
import openai
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

# --- 1. 基础配置 ---
load_dotenv()
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
PORT = int(os.getenv("PORT", 8080))
DOMAIN = os.getenv("SLIPLANE_DOMAIN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

DATA_DIR = "/app/data"
DB_PATH = os.path.join(DATA_DIR, "hermes_memory.db")

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, exist_ok=True)

app = FastAPI()
tg_app = Application.builder().token(TOKEN).build()
client = openai.AsyncOpenAI(api_key=OPENROUTER_API_KEY, base_url="https://openrouter.ai/api/v1")

# --- 2. 数据库逻辑 ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('CREATE TABLE IF NOT EXISTS history (user_id TEXT, role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)')
    conn.commit()
    conn.close()

# --- 3. 后台激活任务 (关键修复) ---
async def setup_webhook_task():
    """在后台静默重试，不阻塞 FastAPI 启动"""
    await asyncio.sleep(5)  # 给容器 5 秒钟来打通网络
    await tg_app.initialize()
    
    if DOMAIN:
        webhook_url = f"https://{DOMAIN}/webhook"
        for i in range(10):  # 增加到 10 次重试
            try:
                # 增加超长时间限制
                await tg_app.bot.set_webhook(url=webhook_url, connect_timeout=40, read_timeout=40)
                logging.info(f"✅ [后台] Webhook 激活成功: {webhook_url}")
                return
            except Exception as e:
                logging.error(f"⚠️ [后台] 第 {i+1} 次激活失败 (网络未就绪): {e}")
                await asyncio.sleep(10)
    logging.error("❌ 后台 Webhook 设置最终失败，请检查 TOKEN 或网络。")

# --- 4. 业务逻辑 ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if ADMIN_ID and user_id != str(ADMIN_ID):
        return

    placeholder = await update.message.reply_text("🤔 Hermes 正在思考...")
    
    # 简单的对话逻辑 (为了稳定暂时简化，您可以自行加回历史记录逻辑)
    try:
        response = await client.chat.completions.create(
            model="google/gemini-2.0-flash-001",
            messages=[{"role": "user", "content": update.message.text}]
        )
        answer = response.choices[0].message.content
    except Exception as e:
        answer = f"❌ 错误: {e}"

    await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=placeholder.message_id, text=answer)

# --- 5. 路由与启动控制 ---
@app.get("/")
async def health_check():
    return {"status": "online"}

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
    # 🔥 核心改动：使用 create_task 让它在后台跑，不干扰 startup 完成
    asyncio.create_task(setup_webhook_task())
    logging.info("🚀 FastAPI 已就绪，后台 Webhook 任务已启动。")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
