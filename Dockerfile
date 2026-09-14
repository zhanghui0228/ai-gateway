FROM python:3.12-slim

WORKDIR /app

# 安装 git(自动更新功能需要)
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# 克隆代码(保留 .git 目录,支持容器内自动更新)
RUN git clone https://github.com/zhanghui0228/ai-gateway.git /app

# 安装依赖
RUN pip install --no-cache-dir -r requirements.txt

# 数据目录(数据库/密钥),挂载卷持久化
RUN mkdir -p /app/data
ENV PYTHONUNBUFFERED=1

EXPOSE 5100

# 初始管理员密码可用环境变量覆盖(仅首次启动生效)
ENV GW_ADMIN_PASSWORD=admin123

CMD ["python", "app.py", "--host", "0.0.0.0", "--port", "5100"]
