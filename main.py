import os
import uvicorn
from fastapi import FastAPI

app = FastAPI()

@app.get("/")
async def root():
    # 只要这个接口返回 200，Sliplane 就会显示绿色 Healthy
    return {"status": "ok", "message": "Sliplane connection successful"}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
