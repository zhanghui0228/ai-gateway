FROM python:3.12-slim

WORKDIR /app

# 安装 git(自动更新功能需要)
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# 依赖单独一层,利用构建缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py config.py ./
COPY gateway/ gateway/
COPY routes/ routes/
COPY web/ web/

# 数据目录(数据库/密钥),挂载卷持久化
RUN mkdir -p /app/data
ENV PYTHONUNBUFFERED=1

EXPOSE 5100

# 初始管理员密码可用环境变量覆盖(仅首次启动生效)
ENV GW_ADMIN_PASSWORD=admin123

CMD ["python", "app.py", "--host", "0.0.0.0", "--port", "5100"]
