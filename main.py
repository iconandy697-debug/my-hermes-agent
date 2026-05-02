import os
import logging
import asyncio
import sqlite3
import openai
from fastapi import FastAPI, Request, Response
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
# 预先构建 Application 对象
tg_app = Application.builder().token(TOKEN).build()
client = openai.AsyncOpenAI(api_key=OPENROUTER_API_KEY, base_url="https://openrouter.ai/api/v1")

# --- 2. 数据库逻辑 ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('CREATE TABLE IF NOT EXISTS history (user_id TEXT, role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)')
    conn.commit()
    conn.close()

# --- 3. 后台 Webhook 激活逻辑 ---
async def set_webhook_with_retry():
    """专门负责在后台反复尝试设置 Webhook，直到成功"""
    if not DOMAIN:
        logging.error("未设置 SLIPLANE_DOMAIN")
        return

    webhook_url = f"https://{DOMAIN}/webhook"
    retry_delay = 5
    
    while True:
        try:
            # 仅设置 Webhook，不涉及初始化
            await tg_app.bot.set_webhook(url=webhook_url, drop_pending_updates=True, connect_timeout=30)
            logging.info(f"✅ [后台] Webhook 成功挂载到: {webhook_url}")
            return
        except Exception as e:
            logging.error(f"⚠️ [后台] Webhook 挂载失败: {e}。{retry_delay}秒后重试...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)

# --- 4. 业务逻辑 ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    user_id = str(update.effective_user.id)
    if ADMIN_ID and user_id != str(ADMIN_ID):
        return

    placeholder = await update.message.reply_text("🤔 Hermes 正在思考...")
    try:
        response = await client.chat.completions.create(
            model="google/gemini-2.0-flash-001",
            messages=[{"role": "user", "content": update.message.text}]
        )
        answer = response.choices[0].message.content
    except Exception as e:
        answer = f"❌ 调取大脑失败: {e}"

    await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=placeholder.message_id, text=answer)

# --- 5. 路由配置 ---
@app.get("/")
async def health_check():
    return {"status": "online"}

@app.post("/webhook")
async def telegram_webhook(request: Request):
    # 关键防护：如果应用还没初始化完成，直接返回 200 丢弃这次请求，防止 500 报错
    if not tg_app.running:
        return Response(content="Not initialized", status_code=200)
    
    try:
        data = await request.json()
        update = Update.de_json(data, tg_app.bot)
        await tg_app.process_update(update)
    except Exception as e:
        logging.error(f"Webhook 异常: {e}")
    return Response(content="OK", status_code=200)

@app.on_event("startup")
async def on_startup():
    init_db()
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # 🔥 1. 立即初始化应用（本地初始化，不联网）
    # 这步解决了 "Application was not initialized" 的报错
    try:
        await tg_app.initialize()
        logging.info("🚀 Telegram Application 内部初始化完成")
    except Exception as e:
        logging.error(f"初始化失败: {e}")

    # 🔥 2. 将联网设置 Webhook 的任务丢到后台
    asyncio.create_task(set_webhook_with_retry())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
