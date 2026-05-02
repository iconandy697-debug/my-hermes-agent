import os
import logging
import asyncio
import sqlite3
import openai
from fastapi import FastAPI, Request, Response
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

# --- 1. 基础配置 ---import os
import logging
import asyncio
import sqlite3
import openai
from fastapi import FastAPI, Request, Response
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv
from tavily import TavilyClient  # 导入 Tavily

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
tavily = TavilyClient(api_key=TAVILY_API_KEY) if TAVILY_API_KEY else None
tg_app = Application.builder().token(TOKEN).build()

# --- 2. 辅助功能：网页检索 ---
async def get_search_context(query: str):
    """使用 Tavily 检索相关背景资料"""
    if not tavily:
        return ""
    try:
        # 使用高级检索模式，针对学术/医疗场景更精准
        response = tavily.search(query=query, search_depth="advanced", max_results=5)
        context_list = [f"来源: {r['url']}\n内容: {r['content']}" for r in response.get('results', [])]
        return "\n\n".join(context_list)
    except Exception as e:
        logging.error(f"🌐 Tavily 搜索异常: {e}")
        return ""

# --- 3. 数据库初始化 ---
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

# --- 4. 消息处理逻辑 ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info("📢 [逻辑层] 接收到消息请求")
    
    if not update.message or not update.message.text:
        return

    user_id = str(update.effective_user.id)
    user_text = update.message.text

    # 权限校验
    if ADMIN_ID and str(user_id) != str(ADMIN_ID):
        logging.warning(f"🚫 拦截未授权访问: {user_id}")
        return

    placeholder = await update.message.reply_text("🤔 Hermes 正在检索与思考...")

    # 自动判断是否需要搜索 (包含关键词或文本较长时)
    search_data = ""
    search_keywords = ["查", "最新", "文献", "研究", "进展", "bja", "guideline", "指南", "什么"]
    if any(k in user_text.lower() for k in search_keywords) or len(user_text) > 15:
        logging.info(f"🔍 触发实时检索: {user_text}")
        search_data = await get_search_context(user_text)

    try:
        # 构建增强 Prompt
        system_content = "你是一个专业的麻醉学专家助理 Hermes。"
        if search_data:
            system_content += f"\n\n以下是为你检索到的实时网络参考信息，请结合这些信息给出专业、准确的回答：\n{search_data}"

        response = await client.chat.completions.create(
            model="google/gemini-2.0-flash-001",
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_text}
            ],
            timeout=60.0 # 增加超时时间以应对搜索+推理
        )
        answer = response.choices[0].message.content
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id, 
            message_id=placeholder.message_id, 
            text=answer
        )
        logging.info("✅ 回复发送成功")
    except Exception as e:
        logging.error(f"❌ 模型调用异常: {e}")
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id, 
            message_id=placeholder.message_id, 
            text=f" Hermes 暂时无法连接大脑: {str(e)}"
        )

# --- 5. 关键：后台异步启动序列 ---
async def start_telegram_backend():
    """在后台不断重试直到 Telegram 连接成功"""
    retry_delay = 2
    webhook_url = f"https://{DOMAIN}/webhook"
    
    while True:
        try:
            logging.info("🔄 尝试初始化 Telegram 联网服务...")
            await tg_app.initialize() 
            await tg_app.start()
            
            await tg_app.bot.set_webhook(url=webhook_url, drop_pending_updates=True)
            logging.info(f"✅ [核心] Webhook 成功挂载: {webhook_url}")
            break 
        except Exception as e:
            logging.error(f"⚠️ 联网初始化失败: {e}，{retry_delay}秒后重试...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30)

# --- 6. FastAPI 路由 ---
@app.get("/")
async def health():
    return {"status": "online", "bot_running": tg_app.running}

@app.post("/webhook")
async def telegram_webhook(request: Request):
    if not tg_app.running:
        logging.warning("📥 收到 Webhook 但 Bot 尚未就绪")
        return Response(content="Wait for init", status_code=200)
    
    try:
        data = await request.json()
        logging.info(f"📥 收到信号: {data}")
        update = Update.de_json(data, tg_app.bot)
        await tg_app.process_update(update)
    except Exception as e:
        logging.error(f"💥 Webhook 路由异常: {e}")
    
    return Response(content="OK", status_code=200)

# --- 7. 启动与停机 ---
@app.on_event("startup")
async def on_startup():
    init_db()
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    asyncio.create_task(start_telegram_backend())
    logging.info("🚀 FastAPI 启动成功，Telegram 任务已转入后台重试流")

@app.on_event("shutdown")
async def on_shutdown():
    if tg_app.running:
        await tg_app.stop()
        await tg_app.shutdown()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
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

DATA_DIR = "/app/data"
DB_PATH = os.path.join(DATA_DIR, "hermes_memory.db")

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, exist_ok=True)

app = FastAPI()
client = openai.AsyncOpenAI(api_key=OPENROUTER_API_KEY, base_url="https://openrouter.ai/api/v1")
tg_app = Application.builder().token(TOKEN).build()

# --- 2. 数据库初始化 ---
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

# --- 3. 消息处理逻辑 ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info("📢 [逻辑层] 接收到消息请求")
    
    if not update.message or not update.message.text:
        return

    user_id = str(update.effective_user.id)
    user_text = update.message.text

    # 权限校验
    if ADMIN_ID and str(user_id) != str(ADMIN_ID):
        logging.warning(f"🚫 拦截未授权访问: {user_id}")
        return

    placeholder = await update.message.reply_text("🤔 Hermes 正在思考...")

    try:
        response = await client.chat.completions.create(
            model="google/gemini-2.0-flash-001",
            messages=[
                {"role": "system", "content": "你是一个专业的麻醉学专家助理 Hermes。"},
                {"role": "user", "content": user_text}
            ],
            timeout=40.0
        )
        answer = response.choices[0].message.content
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id, 
            message_id=placeholder.message_id, 
            text=answer
        )
        logging.info("✅ 回复发送成功")
    except Exception as e:
        logging.error(f"❌ 模型调用异常: {e}")
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id, 
            message_id=placeholder.message_id, 
            text=f" Hermes 暂时无法连接大脑: {str(e)}"
        )

# --- 4. 关键：后台异步启动序列 ---
async def start_telegram_backend():
    """在后台不断重试直到 Telegram 连接成功"""
    retry_delay = 2
    webhook_url = f"https://{DOMAIN}/webhook"
    
    while True:
        try:
            logging.info("🔄 尝试初始化 Telegram 联网服务...")
            # initialize 会尝试调用 getMe，如果网络未就绪会报错
            await tg_app.initialize() 
            await tg_app.start()
            
            # 挂载 Webhook
            await tg_app.bot.set_webhook(url=webhook_url, drop_pending_updates=True)
            logging.info(f"✅ [核心] Webhook 成功挂载: {webhook_url}")
            break 
        except Exception as e:
            logging.error(f"⚠️ 联网初始化失败: {e}，{retry_delay}秒后重试...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30) # 指数退避策略

# --- 5. FastAPI 路由 ---
@app.get("/")
async def health():
    return {"status": "online", "bot_running": tg_app.running}

@app.post("/webhook")
async def telegram_webhook(request: Request):
    if not tg_app.running:
        logging.warning("📥 收到 Webhook 但 Bot 尚未就绪")
        return Response(content="Wait for init", status_code=200)
    
    try:
        data = await request.json()
        logging.info(f"📥 收到信号: {data}")
        update = Update.de_json(data, tg_app.bot)
        await tg_app.process_update(update)
    except Exception as e:
        logging.error(f"💥 Webhook 路由异常: {e}")
    
    return Response(content="OK", status_code=200)

# --- 6. 启动与停机 ---
@app.on_event("startup")
async def on_startup():
    init_db()
    # 注册处理器
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # 核心修改：将联网操作转入后台任务，不阻塞 FastAPI 启动
    asyncio.create_task(start_telegram_backend())
    logging.info("🚀 FastAPI 启动成功，Telegram 任务已转入后台重试流")

@app.on_event("shutdown")
async def on_shutdown():
    if tg_app.running:
        await tg_app.stop()
        await tg_app.shutdown()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
