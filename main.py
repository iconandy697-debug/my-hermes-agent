import os
import logging
import asyncio
import sqlite3
import openai
import httpx
from fastapi import FastAPI, Request, Response
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from telegram.request import HTTPXRequest
from dotenv import load_dotenv
from tavily import TavilyClient
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- 1. 基础配置 ---
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
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

DATA_DIR = "/app/data"
DB_PATH = os.path.join(DATA_DIR, "hermes_memory.db")

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, exist_ok=True)

app = FastAPI()
client = openai.AsyncOpenAI(api_key=OPENROUTER_API_KEY, base_url="https://openrouter.ai/api/v1")

# 初始化 Telegram App，增加超时容忍度以解决 Sliplane 部署启动失败问题
request_config = HTTPXRequest(connect_timeout=20.0, read_timeout=20.0)
tg_app = Application.builder().token(TOKEN).request(request_config).build()

# 初始化定时任务调度器（设置为上海时区）
scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

# --- 2. 辅助功能：网页检索 ---
async def get_search_context(query: str):
    """使用 httpx 直接调用 Tavily API，确保搜索 2026 最新数据"""
    if not TAVILY_API_KEY:
        logging.error("❌ TAVILY_API_KEY 为空，请检查环境变量配置")
        return ""
    
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": "advanced",
        "max_results": 5,
        "include_answer": True
    }
    
    try:
        async with httpx.AsyncClient(timeout=25.0) as http_client:
            response = await http_client.post(url, json=payload)
            res_data = response.json()
            results = res_data.get('results', [])
            if not results:
                return ""
            
            context_list = [f"来源: {r['url']}\n内容: {r['content']}" for r in results]
            logging.info(f"✅ Tavily 搜索成功，获取到 {len(results)} 条实时信息")
            return "\n\n".join(context_list)
    except Exception as e:
        logging.error(f"🌐 Tavily 搜索异常: {e}")
        return ""

# --- 3. 定时任务：周一早晨学术扫描 ---
async def scheduled_bja_job():
    """每周一 06:00 自动触发的任务"""
    if not ADMIN_ID:
        logging.warning("⏰ 定时任务触发，但未配置 ADMIN_ID")
        return

    logging.info("⏰ 启动周一早晨定时学术扫描...")
    search_query = "Latest research articles from British Journal of Anaesthesia and top anesthesiology news 2026"
    search_data = await get_search_context(search_query)
    
    try:
        response = await client.chat.completions.create(
            model="google/gemini-2.0-flash-001",
            messages=[
                {
                    "role": "system", 
                    "content": "你是一个专业的麻醉学专家助理。请总结过去一周麻醉学领域的最新研究动态。要求：中文回答，标题粗体，包含原文简要链接。"
                },
                {"role": "user", "content": f"实时检索数据：\n{search_data}"}
            ],
            timeout=60.0
        )
        report = f"📅 **Hermes 每周学术快报**\n\n{response.choices[0].message.content}"
        await tg_app.bot.send_message(chat_id=ADMIN_ID, text=report, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"❌ 定时推送模型调用失败: {e}")

# --- 4. 数据库初始化 ---
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

# --- 5. 消息处理逻辑 ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user_id = str(update.effective_user.id)
    user_text = update.message.text

    if ADMIN_ID and str(user_id) != str(ADMIN_ID):
        return

    placeholder = None
    try:
        placeholder = await update.message.reply_text("🤔 Hermes 正在检索与思考...")
    except Exception:
        logging.warning("⚠️ 初始消息发送超时")

    search_keywords = ["查", "最新", "文献", "研究", "进展", "bja", "指南", "什么", "如何"]
    search_data = ""
    if any(k in user_text.lower() for k in search_keywords) or len(user_text) > 15:
        search_data = await get_search_context(user_text)

    try:
        system_content = (
            "你是一个专业的麻醉学专家助理 Hermes。你拥有实时访问互联网的能力。"
            "结合我提供的最新搜索数据（含 2026 版指南）给出专业回答。"
            "严禁声称无法访问互联网。"
        )
        
        if search_data:
            system_content += f"\n\n【最新实时检索数据】:\n{search_data}"

        response = await client.chat.completions.create(
            model="google/gemini-2.0-flash-001",
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_text}
            ],
            timeout=60.0
        )
        answer = response.choices[0].message.content
        
        # 稳健的消息编辑/发送逻辑
        try:
            if placeholder:
                await context.bot.edit_message_text(
                    chat_id=update.effective_chat.id, 
                    message_id=placeholder.message_id, 
                    text=answer,
                    parse_mode="Markdown"
                )
            else:
                await update.message.reply_text(answer, parse_mode="Markdown")
        except Exception as e:
            if "Can't parse entities" in str(e):
                logging.warning("⚠️ Markdown 报错，转纯文本重发")
                if placeholder:
                    await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=placeholder.message_id, text=answer)
                else:
                    await update.message.reply_text(answer)
            else:
                logging.error(f"❌ 最终发送失败: {e}")

    except Exception as e:
        logging.error(f"❌ 流程异常: {e}")
        await update.message.reply_text(f"❌ 出现异常，请稍后再试")

# --- 6. 后台启动序列 ---
async def start_telegram_backend():
    webhook_url = f"https://{DOMAIN}/webhook"
    while True:
        try:
            logging.info("🔄 尝试初始化 Telegram Webhook...")
            await tg_app.initialize() 
            await tg_app.start()
            await tg_app.bot.set_webhook(url=webhook_url, drop_pending_updates=True)
            logging.info(f"✅ Webhook 成功挂载: {webhook_url}")
            break 
        except Exception as e:
            logging.error(f"⚠️ 联网初始化失败: {e}，5秒后重试...")
            await asyncio.sleep(5)

# --- 7. FastAPI 路由 ---
@app.post("/webhook")
async def telegram_webhook(request: Request):
    if not tg_app.running:
        return Response(content="Initializing", status_code=200)
    try:
        data = await request.json()
        update = Update.de_json(data, tg_app.bot)
        await tg_app.process_update(update)
    except Exception as e:
        logging.error(f"💥 Webhook 路由异常: {e}")
    return Response(content="OK", status_code=200)

@app.get("/")
async def health():
    return {"status": "online", "bot_running": tg_app.running}

# --- 8. 启动管理 ---
@app.on_event("startup")
async def on_startup():
    init_db()
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    asyncio.create_task(start_telegram_backend())
    scheduler.add_job(scheduled_bja_job, 'cron', day_of_week='mon', hour=6, minute=0)
    scheduler.start()
    logging.info("🚀 Hermes 系统就绪")

@app.on_event("shutdown")
async def on_shutdown():
    scheduler.shutdown()
    if tg_app.running:
        await tg_app.stop()
        await tg_app.shutdown()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
