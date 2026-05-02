import os
import logging
import asyncio
import uvicorn
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

app = FastAPI()

# 2. Telegram 机器人逻辑
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hermes Agent 已连接 Telegram 关口。")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 简单的权限校验
    if ADMIN_ID and str(update.effective_user.id) != str(ADMIN_ID):
        return
    
    # 模拟 Hermes 响应逻辑
    user_text = update.message.text
    await update.message.reply_text(f"Hermes 正在思考您的指令: {user_text}")

# 全局初始化 Telegram Application
tg_application = Application.builder().token(TOKEN).build()

# 3. FastAPI 路由 (关键：确保 '/' 路径立即可用)
@app.get("/")
async def health_check():
    """专门给 Sliplane 的健康检查使用"""
    return {"status": "healthy", "service": "hermes-agent"}

# 4. 生命周期管理 (解决启动阻塞)
@app.on_event("startup")
async def startup_event():
    # 注册 Telegram 处理程序
    tg_application.add_handler(CommandHandler("start", start))
    tg_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # 初始化并启动 Telegram Bot (Polling 模式在后台运行)
    await tg_application.initialize()
    await tg_application.start()
    # 开启轮询，但不阻塞主线程
    asyncio.create_task(tg_application.updater.start_polling())
    logging.info("Telegram Bot 后台轮询已启动")

@app.on_event("shutdown")
async def shutdown_event():
    await tg_application.updater.stop()
    await tg_application.stop()
    await tg_application.shutdown()

if __name__ == "__main__":
    # 使用 uvicorn 启动
    uvicorn.run(app, host="0.0.0.0", port=PORT)
