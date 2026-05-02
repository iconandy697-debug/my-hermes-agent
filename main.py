import os
import logging
import asyncio
from fastapi import FastAPI, Request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

# 1. 初始化与配置
load_dotenv()
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# 从 Sliplane 环境变量获取配置
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")  # 建议在 Sliplane 设置您的数字 ID
PORT = int(os.getenv("PORT", 8080))
DOMAIN = os.getenv("SLIPLANE_DOMAIN") # 您的 Sliplane 二级域名，例如: xxx.sliplane.app

# 初始化 FastAPI 和 Telegram Application
app = FastAPI()
# 注意：这里不直接 run_polling，而是手动管理生命周期
tg_app = Application.builder().token(TOKEN).build()

# 2. Telegram 核心逻辑
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /start 命令"""
    await update.message.reply_text("Hermes Agent 已就绪。请输入您的专业指令。")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理普通消息并集成权限校验"""
    user_id = str(update.effective_user.id)
    
    # 权限校验：仅允许管理员使用
    if ADMIN_ID and user_id != str(ADMIN_ID):
        await update.message.reply_text("未授权用户。")
        return

    user_text = update.message.text
    
    # --- 这里接入您的 Hermes Agent 核心逻辑 ---
    # 示例逻辑：直接返回收到的内容
    # 如果您有现成的 agent 类，在此调用：response = agent.run(user_text)
    response = f"已收到指令：{user_text}\n正在通过 Hermes 核心处理..."
    
    await update.message.reply_text(response)

# 3. FastAPI 路由配置
@app.get("/")
async def index():
    return {"status": "Hermes Agent is running", "gateway": "Telegram Webhook"}

@app.post("/webhook")
async def telegram_webhook(request: Request):
    """接收来自 Telegram 的 Webhook 请求"""
    data = await request.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return {"ok": True}

# 4. 生命周期管理：设置与取消 Webhook
@app.on_event("startup")
async def on_startup():
    # 注册处理程序
    tg_app.add_handler(CommandHandler("start", start))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # 初始化机器人并设置 Webhook
    await tg_app.initialize()
    if DOMAIN:
        webhook_url = f"https://{DOMAIN}/webhook"
        await tg_app.bot.set_webhook(url=webhook_url)
        logging.info(f"Webhook 已设置为: {webhook_url}")
    else:
        logging.warning("未检测到域名，请确保 SLIPLANE_DOMAIN 已设置")

@app.on_event("shutdown")
async def on_shutdown():
    await tg_app.bot.delete_webhook()
    await tg_app.shutdown()

if __name__ == "__main__":
    import uvicorn
    # 监听 0.0.0.0 是 Sliplane 访问的前提
    uvicorn.run(app, host="0.0.0.0", port=PORT)
