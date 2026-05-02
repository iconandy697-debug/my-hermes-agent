FROM python:3.10-slim
RUN apt-get update && apt-get install -y curl bash git
WORKDIR /app
RUN git clone https://github.com/NousResearch/hermes-agent.git .
RUN pip install -r requirements.txt
CMD ["python", "main.py"]
