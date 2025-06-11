# 🚀 Vertical-2-API：您的终极AI模型统一网关 🚀

[![Python](https://img.shields.io/badge/Python-3.8+-blue?style=for-the-badge&logo=python)](https://www.python.org/) [![Flask](https://img.shields.io/badge/Flask-3.0-white?style=for-the-badge&logo=flask)](https://flask.palletsprojects.com/) [![Docker](https://img.shields.io/badge/Docker-Ready-blue?style=for-the-badge&logo=docker)](https://www.docker.com/) [![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](./LICENSE)

**将强大的 [Vertical Studio AI](https://app.verticalstudio.ai) 生态无缝融入您的工作流！**

`Vertical-2-API` 是一个专为开发者和AI爱好者打造的高性能、企业级API转换服务。它将 Vertical Studio AI 平台背后数十种顶尖的大语言模型（LLM），通过一个稳定、可靠的接口，转换为完全兼容 **OpenAI API** 的标准格式。

这意味着，您现在可以在**不修改任何现有代码**的情况下，将您的应用程序、工具和服务（例如 LangChain, LlamaIndex, one-api等）直接对接到一个更加丰富、多元的AI模型世界。

---

## ✨ 核心亮点

*   **⚡ 极致性能与稳定**：内置企业级的会话管理和Cookie自动刷新机制，支持高并发请求。拥有自动重试和精细化日志，确保服务7x24小时稳定可靠。

*   **🔌 真正的“即插即用”**：100% 兼容 OpenAI API 标准。只需修改API基地址和模型名称，即可让您现有的应用瞬间拥有驱动数十种顶级模型的能力。

*   **🤖 海量尖端模型支持**：一键解锁包括 `Claude 3/4`, `GPT-4o/4.1`, `Gemini 2.5 Pro`, `Deepseek V3`, `Grok-3` 在内的全球领先AI模型。告别在多个平台间切换的烦恼！

*   **🔮 高级流式传输与“思考”洞察**：不仅支持标准流式响应，更能实时捕获并传输模型的“思考”（Reasoning）过程（适用于支持该功能的模型），为您提供前所未有的AI决策透明度。

*   **📦 一键部署**：提供 `Dockerfile`，无论是本地开发还是云端生产环境，都可以通过 Docker 实现一键启动，轻松部署。

*   **🛠️ 轻量且专注**：基于 Python 和 Flask 构建，核心代码精炼，无多余依赖，确保资源占用低，运行效率高。

---

## 🧠 支持的模型

通过 `Vertical-2-API`，您可以直接访问以下在 Vertical Studio AI 上可用的模型：

| 客户端模型名称                 | Vertical Studio AI 内部模型ID             |
| ------------------------------ | ----------------------------------------- |
| `claude-3-7-sonnet-thinking`   | `claude-3-7-sonnet-20250219`              |
| `claude-4-sonnet-thinking`     | `claude-4-sonnet-20250514`                |
| `claude-4-opus-thinking`       | `claude-4-opus-20250514`                  |
| `deepseek-r1`                  | `deepseek-reasoner`                       |
| `deepseek-v3`                  | `deepseek-chat`                           |
| `gemini-2.5-flash-preview`     | `gemini-2.5-flash-preview-04-17`          |
| `gemini-2.5-pro-preview`       | `gemini-2.5-pro-preview-05-06`            |
| `gpt-4.1`                      | `gpt-4.1`                                 |
| `gpt-4.1-mini`                 | `gpt-4.1-mini`                            |
| `gpt-4o`                       | `gpt-4o`                                  |
| `o3`                           | `o3`                                      |
| `o4-mini`                      | `o4-mini`                                 |
| `grok-3`                       | `grok-3`                                  |

---

## 🚀 快速开始

### 1. 环境准备

克隆本仓库到您的本地：
```bash
git clone https://github.com/CrisRain/vertical-api-proxy.git
cd vertical-2-api
```

### 2. 配置您的凭据

复制 `.env.example` 文件（如果存在）或直接创建一个名为 `.env` 的新文件，并填入您的 Vertical Studio AI 登录凭据：

```env
# .env
VS_EMAIL="your_email@example.com"
VS_PASSWORD="your_password"

# 可选：自定义服务运行的端口
PORT=7860
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 启动服务！

```bash
python app.py
```
当您在终端看到类似以下的输出时，代表服务已成功启动：
```
* Running on http://0.0.0.0:7860
```

---

## 🐳 Docker 部署 (推荐)

我们强烈建议使用 Docker 进行部署，以获得最佳的兼容性和隔离性。

1.  **构建 Docker 镜像**:
    ```bash
    docker build -t vertical-2-api .
    ```

2.  **运行 Docker 容器**:
    确保您的 `.env` 文件已准备就绪。
    ```bash
    docker run -d --env-file ./.env -p 7860:7860 --name v2api vertical-2-api
    ```
    这将在后台启动一个名为 `v2api` 的容器，并将服务的 `7860` 端口映射到您的主机。

---

## 💻 如何使用

启动服务后，您可以将任何兼容 OpenAI 的客户端指向 `http://localhost:7860/v1`。

### `curl` 示例

这是一个使用 `curl` 调用 `gpt-4o` 模型的例子：

```bash
curl http://localhost:7860/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer any-string-is-ok" \
  -d '{
    "model": "gpt-4o",
    "messages": [
      {
        "role": "system",
        "content": "你是一个乐于助人的AI助手。"
      },
      {
        "role": "user",
        "content": "你好！请介绍一下你自己。"
      }
    ],
    "stream": true
  }'
```

### Python 客户端示例

```python
import openai

client = openai.OpenAI(
    api_key="any-string-will-do",
    base_url="http://localhost:7860/v1",
)

stream = client.chat.completions.create(
    model="claude-4-opus-thinking",
    messages=[{"role": "user", "content": "给我讲一个关于程序员的笑话"}],
    stream=True,
)

for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="")

print()
```

---

## ⚙️ 配置

您可以通过环境变量配置服务：

| 变量          | 描述                           | 默认值 |
| ------------- | ------------------------------ | ------ |
| `VS_EMAIL`    | **必需**，您的登录邮箱。       | -      |
| `VS_PASSWORD` | **必需**，您的登录密码。       | -      |
| `PORT`        | 服务监听的端口。               | `7860` |

---

## 📄 许可证

本项目采用 [MIT License](./LICENSE) 授权。