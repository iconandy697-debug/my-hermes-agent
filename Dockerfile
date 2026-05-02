FROM python:3.11-slim

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    git curl sudo \
    && rm -rf /var/lib/apt/lists/*

# 安装 Hermes Agent
RUN curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash

WORKDIR /app

# 暴露端口
EXPOSE 8080

# 启动 Hermes Agent
CMD ["hermes", "gateway", "--host", "0.0.0.0", "--port", "8080"]
