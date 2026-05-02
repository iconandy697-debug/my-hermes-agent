import os
import logging
import asyncio
import openai
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

# 1. 初始化与配置
load_dotenv()
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# 环境变量获取
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
PORT = int(os.getenv("PORT", 8080))
DOMAIN = os.getenv("SLIPLANE_DOMAIN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# 初始化 FastAPI 和 Telegram
app = FastAPI()
tg_app = Application.builder().token(TOKEN).build()

# 初始化 OpenRouter 客户端 (兼容 OpenAI SDK)
client = openai.AsyncOpenAI(
    api_key=OPENROUTER_API_KEY, 
    base_url="https://openrouter.ai/api/v1"
)

# 2. 核心大脑逻辑：调用 OpenRouter
async def get_hermes_response(user_text: str):
    """调用 OpenRouter API 获取回复"""
    try:
        response = await client.chat.completions.create(
            # 这里您可以更改为任何 OpenRouter 支持的模型
            model="google/gemini-2.0-flash-001", 
            messages=[
                {"role": "system", "content": "你是一个专业的麻醉学专家助理 Hermes。"},
                {"role": "user", "content": user_text}
            ],
            extra_headers={
                "HTTP-Referer": f"https://{DOMAIN}" if DOMAIN else "http://localhost", # OpenRouter 排名需要
                "X-Title": "Hermes Med Agent",
            }
        )
        return response.choices[0].message.content
    except Exception as e:
        logging.error(f"OpenRouter Error: {e}")
        return f"❌ OpenRouter 连接异常: {str(e)}"

# 3. Telegram 业务逻辑
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔬 Hermes Agent (via OpenRouter) 已连接。")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    
    # 权限校验
    if ADMIN_ID and user_id != str(ADMIN_ID):
        await update.message.reply_text("🚫 未经授权。")
        return

    user_text = update.message.text
    placeholder_msg = await update.message.reply_text("🤔 Hermes 正在通过 OpenRouter 思考...")
    
    # 异步获取回复
    hermes_answer = await get_hermes_response(user_text)
    
    await context.bot.edit_message_text(
        chat_id=update.effective_chat.id,
        message_id=placeholder_msg.message_id,
        text=hermes_answer
    )

# 4. FastAPI 路由与 Webhook
@app.get("/")
async def index():
    return {"status": "Hermes Running", "engine": "OpenRouter"}

@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return {"ok": True}

# 5. 生命周期管理
@app.on_event("startup")
async def on_startup():
    tg_app.add_handler(CommandHandler("start", start))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    await tg_app.initialize()
    if DOMAIN:
        webhook_url = f"https://{DOMAIN}/webhook"
        await tg_app.bot.set_webhook(url=webhook_url)
        logging.info(f"Webhook set to: {webhook_url}")

@app.on_event("shutdown")
async def on_shutdown():
    await tg_app.bot.delete_webhook()
    await tg_app.shutdown()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
