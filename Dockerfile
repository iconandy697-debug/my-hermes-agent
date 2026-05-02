FROM python:3.10-slim

# 安装构建工具，这是解决 pip install 报错的关键
RUN apt-get update && apt-get install -y \
    curl bash git gcc g++ make python3-dev libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 注意：如果你的仓库里已经有代码，不要再执行 git clone，
# 否则会造成代码路径混乱（变成 /app/hermes-agent/main.py）
# Sliplane 会自动同步你的仓库内容到 WORKDIR
COPY . .

# 升级 pip 并安装依赖
RUN pip install --upgrade pip
RUN if [ -f "requirements.txt" ]; then \
        pip install --no-cache-dir -r requirements.txt; \
    fi

# 启动命令
CMD ["python", "main.py"]
