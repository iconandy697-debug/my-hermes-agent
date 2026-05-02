import os
import logging
import asyncio
import sqlite3
import openai
from fastapi import FastAPI, Request, Response
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
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
tavily = TavilyClient(api_key=TAVILY_API_KEY) if TAVILY_API_KEY else None
tg_app = Application.builder().token(TOKEN).build()

# 初始化定时任务调度器（设置为上海时区）
scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

# --- 2. 辅助功能：网页检索 ---
async def get_search_context(query: str):
    """调用 Tavily 获取实时网络背景"""
    # 检查 Key 是否存在（防止因变量名错误导致为 None）
    if not TAVILY_API_KEY:
        logging.error("❌ TAVILY_API_KEY 为空，请检查环境变量配置")
        return ""
    
    try:
        # 使用 run_in_executor 运行同步的 Tavily SDK 防止阻塞异步主线程
        loop = asyncio.get_event_loop()
        # search_depth="advanced" 消耗 2 Credits，提供更深度的学术内容
        # search_depth="basic" 消耗 1 Credit
        response = await loop.run_in_executor(
            None, 
            lambda: tavily.search(query=query, search_depth="advanced", max_results=5)
        )
        
        results = response.get('results', [])
        if not results:
            logging.warning(f"⚠️ 搜索完成但未找到相关结果: {query}")
            return ""
            
        context_list = [f"来源: {r['url']}\n内容: {r['content']}" for r in results]
        logging.info(f"✅ Tavily 搜索成功，获取到 {len(results)} 条信息")
        return "\n\n".join(context_list)
        
    except Exception as e:
        logging.error(f"🌐 Tavily 搜索异常: {e}")
        return ""

# --- 3. 定时任务：周一早晨学术扫描 ---
async def scheduled_bja_job():
    """每周一 06:00 自动触发的任务"""
    if not ADMIN_ID:
        logging.warning("⏰ 定时任务触发，但未配置 ADMIN_ID，无法推送")
        return

    logging.info("⏰ 启动周一早晨定时学术扫描...")
    # 模拟检索 BJA 最新文献及麻醉学动态
    search_query = "Latest research articles from British Journal of Anaesthesia and top anesthesiology news this week"
    search_data = await get_search_context(search_query)
    
    try:
        # 使用 Gemini 进行学术总结
        response = await client.chat.completions.create(
            model="google/gemini-2.0-flash-001",
            messages=[
                {
                    "role": "system", 
                    "content": "你是一个专业的麻醉学专家助理。请根据提供的搜索信息，总结过去一周麻醉学领域的最新研究动态和 BJA (British Journal of Anaesthesia) 的重点文章。要求：使用中文，标题粗体，条理清晰，包含原文简要链接。"
                },
                {"role": "user", "content": f"实时检索数据如下：\n{search_data}"}
            ],
            timeout=60.0
        )
        report = f"📅 **Hermes 每周学术快报**\n\n{response.choices[0].message.content}"
        
        # 主动推送给管理员
        await tg_app.bot.send_message(chat_id=ADMIN_ID, text=report, parse_mode="Markdown")
        logging.info("✅ 周一学术快报推送成功")
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
    logging.info("📢 [逻辑层] 接收到消息请求")
    
    if not update.message or not update.message.text:
        return

    user_id = str(update.effective_user.id)
    user_text = update.message.text

    # 权限校验
    if ADMIN_ID and str(user_id) != str(ADMIN_ID):
        logging.warning(f"🚫 拦截未授权访问: {user_id}")
        return

    # 发送思考中状态
    placeholder = await update.message.reply_text("🤔 Hermes 正在检索与思考...")

    # 自动判断是否触发实时检索 (包含特定关键词或问题较长)
    search_keywords = ["查", "最新", "文献", "研究", "进展", "bja", "指南", "什么", "如何"]
    search_data = ""
    if any(k in user_text.lower() for k in search_keywords) or len(user_text) > 15:
        logging.info(f"🔍 触发实时搜索: {user_text}")
        search_data = await get_search_context(user_text)

    try:
        # 修改这里的 System Content，赋予它“联网意识”
        system_content = (
            "你是一个专业的麻醉学专家助理 Hermes。你现在拥有通过 Tavily 访问实时互联网的能力。"
            "我会为你提供最新的搜索数据，请你结合这些信息给出最前沿、最准确的专业回答。"
            "严禁在回答中说‘我无法访问互联网’或‘我的信息不是最新的’。"
            "如果引用了检索到的数据，请直接呈现结果。"
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
        # ... 后续逻辑 ...
        answer = response.choices[0].message.content
        
        # 编辑占位消息显示最终结果
        try:
        # 第一尝试：使用 Markdown 渲染
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id, 
            message_id=placeholder.message_id, 
            text=answer,
            parse_mode="Markdown"
        )
    except Exception as e:
        if "Can't parse entities" in str(e):
            # 第二尝试：Markdown 解析失败，降级为纯文本，防止答案丢失
            logging.warning(f"⚠️ 格式解析失败 (offset {e.byte_offset})，已转为纯文本发送")
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id, 
                message_id=placeholder.message_id, 
                text=answer  # 删掉 parse_mode 参数
            )
        else:
            logging.error(f"❌ 发送消息时发生其他错误: {e}")
        )

# --- 6. 后台启动序列 ---
async def start_telegram_backend():
    """初始化 Telegram Webhook 联网服务"""
    webhook_url = f"https://{DOMAIN}/webhook"
    while True:
        try:
            logging.info("🔄 尝试初始化 Telegram 服务...")
            await tg_app.initialize() 
            await tg_app.start()
            await tg_app.bot.set_webhook(url=webhook_url, drop_pending_updates=True)
            logging.info(f"✅ [核心] Webhook 成功挂载: {webhook_url}")
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

# --- 8. 启动与停机管理 ---
@app.on_event("startup")
async def on_startup():
    init_db()
    # 注册消息处理器
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # 启动异步后台联网任务
    asyncio.create_task(start_telegram_backend())
    
    # 配置定时扫描任务：每周一 06:00 (Asia/Shanghai)
    scheduler.add_job(scheduled_bja_job, 'cron', day_of_week='mon', hour=6, minute=0)
    scheduler.start()
    logging.info("🚀 Hermes 系统已就绪，定时学术扫描已设定为每周一 06:00")

@app.on_event("shutdown")
async def on_shutdown():
    scheduler.shutdown()
    if tg_app.running:
        await tg_app.stop()
        await tg_app.shutdown()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
