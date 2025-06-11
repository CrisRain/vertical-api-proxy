# 部署 Vertical Studio AI 代理到 Hugging Face Spaces (Docker)

本指南将引导你完成将 Vertical Studio AI 代理 Flask 应用部署到 Hugging Face Spaces 的步骤。该应用通过 Docker 容器化，并利用 Hugging Face Spaces 的 Secrets 功能安全管理凭据。

## 目录

1.  [先决条件](#1-先决条件)
2.  [项目文件](#2-项目文件)
3.  [部署步骤](#3-部署步骤)
    *   [3.1 创建 Hugging Face Space](#31-创建-hugging-face-space)
    *   [3.2 上传文件](#32-上传文件)
    *   [3.3 配置 Secrets](#33-配置-secrets)
4.  [构建与运行](#4-构建与运行)
5.  [访问应用](#5-访问应用)
6.  [重要说明](#6-重要说明)
7.  [故障排除](#7-故障排除)
8.  [测试端点](#8-测试端点)

## 1. 先决条件

*   一个 **Hugging Face 账户** ([huggingface.co](https://huggingface.co/))
*   **Vertical Studio AI 账户凭据** (邮箱和密码)
*   本地安装 **Git** (用于克隆和推送，或者你也可以使用 Hugging Face UI 上传文件)
*   （可选）本地安装 **Docker** (用于在本地测试 Dockerfile，但 Hugging Face Spaces 会在云端构建)

## 2. 项目文件

确保你的项目根目录包含以下文件：

1.  **`app.py`**:
    *   核心 Flask 应用代码。
    *   **重要**: 此版本应已修改为从环境变量 (`VS_EMAIL`, `VS_PASSWORD`) 读取凭据，而不是从 `config.yml` 文件。
    *   应用的监听端口应配置为从环境变量 `PORT` 读取，默认为 `7860` (Hugging Face Spaces 的标准)。
    *   Cookie 管理已修改为纯内存，不写入文件系统。

2.  **`requirements.txt`**:
    ```txt
    flask
    requests
    # pyyaml (如果 app.py 中已完全移除 yaml 依赖则不需要)
    brotli
    ```
    *(请根据你的 `app.py` 最终的 import 确认 `pyyaml` 是否仍需要)*

3.  **`Dockerfile`**:
    ```dockerfile
    # 使用一个轻量级的 Python 基础镜像
    FROM python:3.9-slim

    # 设置工作目录
    WORKDIR /app

    # 复制依赖文件并安装依赖
    COPY requirements.txt requirements.txt
    RUN pip install --no-cache-dir -r requirements.txt

    # 复制应用代码到工作目录
    COPY app.py .

    # Hugging Face Spaces 通常期望应用监听 7860 端口
    ENV PORT=7860
    EXPOSE 7860

    # 运行应用的命令
    CMD ["python", "app.py"]
    ```

**注意**: **不要** 将包含敏感凭据的 `config.yml` 文件上传到你的代码仓库或 Docker 镜像中。

## 3. 部署步骤

### 3.1 创建 Hugging Face Space

1.  登录到 [Hugging Face](https://huggingface.co/).
2.  点击你的头像，选择 "New Space"。
3.  **Space name**: 给你的 Space 起一个唯一的名字 (例如, `your-username/vs-ai-proxy`)。
4.  **License**: 选择一个合适的许可证 (例如, `mit`)。
5.  **Space SDK**: 选择 **Docker**。
6.  **Docker template**: 选择 **No template** (因为我们提供自己的 `Dockerfile`)。
7.  **Hardware**: 对于这个应用，"CPU basic" 通常就足够了。
8.  **Visibility**: 选择 "Public" 或 "Private"。
9.  点击 "**Create Space**"。

### 3.2 上传文件

Space 创建后，你会被重定向到一个 Git 仓库页面。

*   **选项 A: 使用 Git**
    1.  克隆你的新 Space 仓库到本地：
        ```bash
        git clone https://huggingface.co/spaces/YOUR_USERNAME/YOUR_SPACE_NAME
        ```
    2.  将 `app.py`, `requirements.txt`, 和 `Dockerfile` 文件复制到克隆的仓库目录中。
    3.  提交并推送文件：
        ```bash
        cd YOUR_SPACE_NAME
        git add app.py requirements.txt Dockerfile
        git commit -m "Initial application files"
        git push
        ```

*   **选项 B: 使用 Hugging Face UI**
    1.  在你的 Space 页面，点击 "Files" 标签。
    2.  点击 "Add file" -> "Upload files"。
    3.  选择并上传 `app.py`, `requirements.txt`, 和 `Dockerfile`。

### 3.3 配置 Secrets

这是安全管理你的 Vertical Studio AI 凭据的关键步骤。

1.  在你的 Space 页面，进入 "Settings" 标签。
2.  在左侧菜单中，找到并点击 "Secrets"。
3.  点击 "**New secret**"。
4.  添加第一个 Secret：
    *   **Name**: `VS_EMAIL`
    *   **Value**: 你的 Vertical Studio AI 邮箱地址 (例如 `your_email@example.com`)
5.  点击 "Add secret"。
6.  再次点击 "**New secret**"。
7.  添加第二个 Secret：
    *   **Name**: `VS_PASSWORD`
    *   **Value**: 你的 Vertical Studio AI 密码
8.  点击 "Add secret"。

这些 Secrets 将作为环境变量注入到你的 Docker 容器中，`app.py` 会读取它们。

## 4. 构建与运行

当你将文件推送到 Space 仓库或通过 UI 上传后，Hugging Face Spaces 会自动开始构建 Docker 镜像并尝试运行你的应用。

*   你可以在 Space 页面的主界面或 "Builds" 标签下查看构建日志和应用运行日志。
*   如果构建成功并且应用正常启动，状态会显示为 "Running"。

## 5. 访问应用

一旦应用成功运行，Hugging Face 会在你的 Space 页面提供一个公共 URL。它通常看起来像这样：
`https://YOUR_USERNAME-YOUR_SPACE_NAME.hf.space`

你的 API 端点将是这个 URL 加上你在 `app.py` 中定义的 Flask 路由。例如：

*   获取模型列表: `GET https://YOUR_USERNAME-YOUR_SPACE_NAME.hf.space/v1/models`
*   文本聊天: `POST https://YOUR_USERNAME-YOUR_SPACE_NAME.hf.space/v1/chat/completions`
*   图片生成: `POST https://YOUR_USERNAME-YOUR_SPACE_NAME.hf.space/v1/images/generations`

## 6. 重要说明

*   **Cookie 管理**: 此版本的应用将 Cookie 存储在内存中。这意味着每次应用重启（例如，Space 更新、手动重启或 Hugging Face 平台维护）后，应用都需要重新登录到 Vertical Studio AI。后台线程会尝试定期刷新 Cookie 以保持会话。
*   **端口**: 应用配置为在 Docker 容器内监听 `7860` 端口，Hugging Face Spaces 会自动将外部流量路由到此端口。
*   **日志**: Flask 应用的日志 (通过 `app.logger` 或 `print` 语句) 会输出到 Docker 容器的标准输出/标准错误，你可以在 Hugging Face Space 页面的日志部分查看。
*   **资源**: Hugging Face Spaces 的免费层级有资源限制。如果你的应用遇到性能问题或频繁重启，可能需要考虑升级硬件。
*   **安全性**: 再次强调，**切勿** 将你的 `VS_EMAIL` 和 `VS_PASSWORD`硬编码到代码中或提交到 Git 仓库。始终使用 Secrets。

## 7. 故障排除

*   **"Application Error"**:
    *   检查 Space 日志。通常错误信息会直接显示在那里。
    *   确认 `Dockerfile` 和 `requirements.txt` 是否正确。
    *   确认 `app.py` 没有语法错误。
*   **登录失败 / Cookie 问题**:
    *   仔细检查你在 Hugging Face Secrets 中设置的 `VS_EMAIL` 和 `VS_PASSWORD` 是否准确无误。
    *   检查应用日志中关于登录尝试的输出。
    *   由于 Cookie 现在是内存中的，如果应用因任何原因重启，它将尝试重新登录。如果登录持续失败，凭据可能是主要原因。
*   **模型名称问题**:
    *   对于图片生成，请确保客户端请求中的模型名称与 `app.py` 中 `IMAGE_MODEL_MAPPING` 字典的**键**匹配（例如，使用 `"dall-e-3"` 而不是 `"dall-e-3-vs"`）。
*   **"Permission Denied" (保存 Cookie 文件)**: 此问题已通过将 Cookie 管理更改为纯内存来解决。如果你看到此错误，请确保你使用的是最新版本的 `app.py`。

## 8. 测试端点

你可以使用 `curl` 或 Postman 等工具来测试部署的应用。

*   **获取模型列表 (GET)**:
    ```bash
    curl https://YOUR_USERNAME-YOUR_SPACE_NAME.hf.space/v1/models
    ```

*   **文本聊天 (POST)**:
    ```bash
    curl -X POST https://YOUR_USERNAME-YOUR_SPACE_NAME.hf.space/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "user", "content": "Hello, how are you?"}
        ],
        "stream": false
    }'
    ```

*   **图片生成 (POST)**:
    ```bash
    curl -X POST https://YOUR_USERNAME-YOUR_SPACE_NAME.hf.space/v1/images/generations \
    -H "Content-Type: application/json" \
    -d '{
        "model": "dall-e-3",
        "prompt": "A cute cat wearing a small wizard hat",
        "n": 1,
        "size": "1024x1024"
    }'
    ```
    (注意: `n` 和 `size` 参数目前在 `app.py` 中可能未被使用，但符合 OpenAI API 格式)

---

祝你部署顺利！
