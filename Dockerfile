FROM python:3.11-slim

WORKDIR /app

# 安装 uv
RUN pip install uv

# 复制依赖文件
COPY pyproject.toml uv.lock ./

# 安装依赖
RUN uv sync --frozen

# 复制项目文件
COPY main.py config.py gitlab_client.py reviewer.py mcp_server.py ./

# 暴露端口
EXPOSE 8000

# 启动应用
CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
