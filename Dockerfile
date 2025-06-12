# 使用一个轻量级的 Python 基础镜像
FROM python:3.9-slim

# 设置工作目录
WORKDIR /app

# 复制依赖文件并安装依赖
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码到工作目录
COPY . .

# 暴露应用端口
# Hugging Face Spaces 通常期望应用监听 7860 端口，但我们将使其可配置
ENV PORT=${PORT:-7860}
EXPOSE $PORT

# 使用 Hypercorn 运行应用
CMD ["python", "app.py"]
