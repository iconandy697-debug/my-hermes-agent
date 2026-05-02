FROM python:3.10-slim

# 1. 安装基础工具
RUN apt-get update && apt-get install -y curl bash git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 2. 克隆仓库（注意：hermes-agent 仓库内可能还有子目录）
RUN git clone https://github.com/NousResearch/hermes-agent.git .

# 3. 检查并安装依赖
# 使用 shell 脚本判断是否存在 requirements.txt，如果不存在则尝试直接安装项目
RUN if [ -f "requirements.txt" ]; then \
        pip install --no-cache-dir -r requirements.txt; \
    elif [ -f "setup.py" ]; then \
        pip install --no-cache-dir .; \
    else \
        echo "Warning: No requirements.txt or setup.py found. Checking subdirectories..."; \
    fi

# 4. 启动命令（请根据仓库实际的启动脚本调整，通常是 main.py）
CMD ["python", "main.py"]
