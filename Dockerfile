# 使用基础镜像
FROM python:3.10-slim

# 安装必要工具
RUN apt-get update && apt-get install -y curl bash git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 关键：克隆仓库到当前目录
# 注意后面的“.”号，代表直接克隆到 /app
RUN git clone https://github.com/NousResearch/hermes-agent.git .

# 检查并安装依赖
RUN if [ -f "requirements.txt" ]; then \
        pip install --no-cache-dir -r requirements.txt; \
    else \
        pip install --no-cache-dir .; \
    fi

# 修正启动文件路径
# 如果通过 SSH 发现文件在子目录，请改为 "python", "src/main.py"
CMD ["python", "main.py"]
