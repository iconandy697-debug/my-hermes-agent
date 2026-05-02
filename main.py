import os
import logging
import asyncio
import sqlite3
import openai
from fastapi import FastAPI, Request, Response
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

# --- 1. 基础配置与环境加载 ---
load_dotenv()
# 提高日志详细度，确保能看到每一个请求
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)

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
# 初始化 OpenAI 客户端（用于 OpenRouter）
client = openai.AsyncOpenAI(api_key=OPENROUTER_API_KEY, base_url="https://openrouter.ai/api/v1")
# 构建 Telegram Application
tg_app = Application.builder().token(TOKEN).build()

# --- 2. 数据库逻辑 ---
def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute('''CREATE TABLE IF NOT EXISTS history 
                       (user_id TEXT, role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        conn.commit()
        conn.close()
        logging.info("📁 数据库初始化成功")
    except Exception as e:
        logging.error(f"❌ 数据库初始化失败: {e}")

# --- 3. 核心业务逻辑 (含深度日志) ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info("📢 [逻辑层] 已进入 handle_message")
    
    if not update.message or not update.message.text:
        logging.warning("⚠️ 收到非文本消息或空 Update，忽略")
        return

    user_id = str(update.effective_user.id)
    user_text = update.message.text
    logging.info(f"👤 用户 ID: {user_id} | 📝 消息内容: {user_text}")

    # 权限校验
    if ADMIN_ID and str(user_id) != str(ADMIN_ID):
        logging.warning(f"🚫 拦截未授权访问: {user_id} (配置的 ADMIN_ID 为 {ADMIN_ID})")
        return

    # 发送等待提示
    placeholder = await update.message.reply_text("🤔 Hermes 正在思考...")

    try:
        logging.info(f"🧠 正在调用模型: google/gemini-2.0-flash-001")
        response = await client.chat.completions.create(
            model="google/gemini-2.0-flash-001",
            messages=[
                {"role": "system", "content": "你是一个专业的麻醉学专家助理 Hermes。"},
                {"role": "user", "content": user_text}
            ],
            timeout=45.0
        )
        answer = response.choices[0].message.content
        logging.info("✅ OpenRouter 响应成功")

        # 更新 Telegram 消息
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id, 
            message_id=placeholder.message_id, 
            text=answer
        )
    except Exception as e:
        err_msg = f"❌ 逻辑异常: {type(e).__name__} - {str(e)}"
        logging.error(err_msg)
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id, 
            message_id=placeholder.message_id, 
            text=f"Hermes 暂时无法回复: {str(e)}"
        )

# --- 4. Webhook 异步设置逻辑 ---
async def set_webhook_with_retry():
    if not DOMAIN:
        logging.error("❌ 未发现 SLIPLANE_DOMAIN 环境变量")
        return

    webhook_url = f"https://{DOMAIN}/webhook"
    retry_delay = 5
    
    while True:
        try:
            # 明确指定 Webhook 地址并清理旧更新
            await tg_app.bot.set_webhook(url=webhook_url, drop_pending_updates=True)
            logging.info(f"✅ [后台] Webhook 成功挂载: {webhook_url}")
            return
        except Exception as e:
            logging.error(f"⚠️ [后台] Webhook 挂载失败: {e}。{retry_delay}秒后重试...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)

# --- 5. FastAPI 路由 ---
@app.get("/")
async def health_check():
    return {"status": "running", "admin_configured": bool(ADMIN_ID)}

@app.post("/webhook")
async def telegram_webhook(request: Request):
    logging.info("📥 [网络层] 接收到 POST 请求")
    
    if not tg_app.running:
        logging.error("❌ tg_app 尚未完全初始化")
        return Response(content="Initializing...", status_code=200)
    
    try:
        payload = await request.json()
        logging.info(f"数据包: {payload}")
        update = Update.de_json(payload, tg_app.bot)
        # 将 update 放入队列处理
        await tg_app.process_update(update)
    except Exception as e:
        logging.error(f"💥 Webhook 处理崩溃: {e}")
    
    return Response(content="OK", status_code=200)

# --- 6. 启动序列 ---
@app.on_event("startup")
async def on_startup():
    init_db()
    
    # 注册消息处理器：允许所有文本消息
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # 关键：先启动内部状态，再设置 Webhook
    await tg_app.initialize()
    await tg_app.start()
    
    # 启动后台任务设置 Webhook
    asyncio.create_task(set_webhook_with_retry())
    logging.info("🚀 Hermes 启动序列完成")

@app.on_event("shutdown")
async def on_shutdown():
    await tg_app.stop()
    await tg_app.shutdown()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
