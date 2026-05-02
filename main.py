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
from langchain_core.tools import tool
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

# 初始化 Telegram，增加超时容忍以适配 Sliplane 部署环境
request_config = HTTPXRequest(connect_timeout=20.0, read_timeout=20.0)
tg_app = Application.builder().token(TOKEN).request(request_config).build()

scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

# --- 2. LangChain Tool 封装 ---

@tool
async def search_medical_news(query: str) -> str:
    """
    Search for the latest medical literature, anesthesiology guidelines (e.g., BJA), 
    and 2026 clinical updates via Tavily. 
    Use this tool when the user asks for recent research or professional guidelines.
    """
    if not TAVILY_API_KEY:
        return "Error: TAVILY_API_KEY not configured."
    
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": "advanced",
        "max_results": 5
    }
    
    try:
        async with httpx.AsyncClient(timeout=25.0) as http_client:
            response = await http_client.post(url, json=payload)
            res_data = response.json()
            results = res_data.get('results', [])
            
            if not results:
                return f"No recent results found for '{query}'."
            
            context = "\n\n".join([f"来源: {r['url']}\n内容: {r['content']}" for r in results])
            logging.info(f"✅ Tavily Tool 成功检索到 {len(results)} 条数据")
            return context
    except Exception as e:
        logging.error(f"🌐 Tavily Tool 异常: {e}")
        return f"Search failed due to an error: {str(e)}"

# --- 3. 定时任务：周一早晨学术扫描 ---
async def scheduled_bja_job():
    if not ADMIN_ID:
        return

    logging.info("⏰ 启动周一早晨学术扫描...")
    # 直接调用工具函数（注意调用 .invoke 或直接运行其内部逻辑）
    search_query = "British Journal of Anaesthesia latest articles and anesthesia guidelines 2026"
    search_data = await search_medical_news.ainvoke({"query": search_query})
    
    try:
        response = await client.chat.completions.create(
            model="google/gemini-2.0-flash-001",
            messages=[
                {"role": "system", "content": "你是一个专业的麻醉学助手。请根据搜索数据，总结 BJA 最新动态。要求：中文，标题粗体。"},
                {"role": "user", "content": f"检索数据：\n{search_data}"}
            ]
        )
        report = f"📅 **Hermes 每周学术快报**\n\n{response.choices[0].message.content}"
        await tg_app.bot.send_message(chat_id=ADMIN_ID, text=report, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"❌ 定时推送失败: {e}")

# --- 4. 数据库初始化 ---
def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute('CREATE TABLE IF NOT EXISTS history (user_id TEXT, role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)')
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"❌ DB Error: {e}")

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
        logging.warning("⚠️ 消息发送超时")

    # 触发逻辑：如果包含关键词或问题较长，则调用搜索工具
    search_keywords = ["查", "最新", "文献", "研究", "进展", "bja", "指南", "什么", "如何"]
    search_data = ""
    if any(k in user_text.lower() for k in search_keywords) or len(user_text) > 15:
        # 使用工具的 ainvoke 方法
        search_data = await search_medical_news.ainvoke({"query": user_text})

    try:
        system_content = (
            "你是一个专业的麻醉学专家助理 Hermes。你拥有实时访问互联网的能力。"
            "结合提供的最新搜索数据（含 2026 版指南）给出准确回答。严禁声称无法联网。"
        )
        if search_data:
            system_content += f"\n\n【最新检索参考】:\n{search_data}"

        response = await client.chat.completions.create(
            model="google/gemini-2.0-flash-001",
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_text}
            ],
            timeout=60.0
        )
        answer = response.choices[0].message.content
        
        # 稳健的消息回复逻辑（含 Markdown 容错）
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
                # 降级为纯文本发送
                if placeholder:
                    await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=placeholder.message_id, text=answer)
                else:
                    await update.message.reply_text(answer)
    except Exception as e:
        logging.error(f"❌ 流程异常: {e}")
        await update.message.reply_text("❌ 抱歉，处理您的请求时出现错误。")

# --- 6. 启动序列 ---
async def start_telegram_backend():
    webhook_url = f"https://{DOMAIN}/webhook"
    while True:
        try:
            await tg_app.initialize() 
            await tg_app.start()
            await tg_app.bot.set_webhook(url=webhook_url, drop_pending_updates=True)
            logging.info(f"✅ Webhook 挂载成功: {webhook_url}")
            break 
        except Exception as e:
            logging.error(f"⚠️ 初始化失败: {e}，5秒后重试...")
            await asyncio.sleep(5)

@app.post("/webhook")
async def telegram_webhook(request: Request):
    if not tg_app.running:
        return Response(content="Initializing", status_code=200)
    try:
        data = await request.json()
        update = Update.de_json(data, tg_app.bot)
        await tg_app.process_update(update)
    except Exception as e:
        logging.error(f"💥 Webhook Error: {e}")
    return Response(content="OK", status_code=200)

@app.get("/")
async def health():
    return {"status": "online", "bot_running": tg_app.running}

@app.on_event("startup")
async def on_startup():
    init_db()
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    asyncio.create_task(start_telegram_backend())
    scheduler.add_job(scheduled_bja_job, 'cron', day_of_week='mon', hour=6, minute=0)
    scheduler.start()
    logging.info("🚀 Hermes 系统已就绪")

@app.on_event("shutdown")
async def on_shutdown():
    scheduler.shutdown()
    if tg_app.running:
        await tg_app.stop()
        await tg_app.shutdown()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
