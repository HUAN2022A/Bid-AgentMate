FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts

# 前端构建产物（多阶段构建留给阶段 1 之后；当前先在宿主机 npm build 后拷入）
# COPY ../frontend/dist ./frontend/dist  # docker build context 限制，改用 compose volume 或后续多阶段

EXPOSE 8000
