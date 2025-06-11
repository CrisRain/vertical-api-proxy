# 使用一个轻量级的 Python 基础镜像
FROM python:3.9-slim

# 设置工作目录
WORKDIR /app

# 复制依赖文件并安装依赖
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码到工作目录
COPY app.py .
# 注意：config.yml 将通过环境变量处理，所以不直接复制到镜像中

# Hugging Face Spaces 通常期望应用监听 7860 端口
ENV PORT=7860
EXPOSE 7860

# 运行应用的命令
CMD ["python", "app.py"]
