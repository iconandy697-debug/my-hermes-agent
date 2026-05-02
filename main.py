import os
from fastapi import FastAPI
from pydantic import BaseModel
from dotenv import load_dotenv

# 1. 加载 .env 中的环境变量
load_dotenv()

app = FastAPI()

# 定义请求体格式
class Query(BaseModel):
    message: str

@app.get("/")
def read_root():
    return {"status": "Hermes Agent is running"}

@app.post("/chat")
async def chat(query: Query):
    # 这里是调用你 Hermes Agent 逻辑的地方
    # 例如：response = agent.run(query.message)
    user_input = query.message
    
    # 模拟回复逻辑
    return {
        "reply": f"Hermes 收到消息: {user_input}",
        "agent_status": "active"
    }

if __name__ == "__main__":
    import uvicorn
    # 关键：必须监听 0.0.0.0 和 Sliplane 指定的端口
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
