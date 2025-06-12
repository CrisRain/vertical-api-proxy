import dotenv
from flask import Flask, request, jsonify, Response
import httpx
import json
import uuid
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode
import os
import sys
import gzip
import brotli
import re
import asyncio
import time
import logging
import base64
import queue
import threading

app = Flask(__name__)
# --- Logging Configuration ---
# Configure logger to output to stdout, ensuring visibility when running with Waitress
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
app.logger.handlers.clear() # Clear existing handlers
app.logger.addHandler(handler)
app.logger.setLevel(logging.INFO)

# --- Vertical Studio AI 接口地址 ---
LOGIN_URL = "https://app.verticalstudio.ai/login"
LOGIN_PASSWORD_DATA_URL = "https://app.verticalstudio.ai/login-password.data"
CHAT_API_URL = "https://app.verticalstudio.ai/api/chat"
ARCHIVE_CHAT_URL = "https://app.verticalstudio.ai/api/chat/archive.data"
STREAM_BASE_URL = "https://app.verticalstudio.ai/stream"
STREAM_CORNERS_BASE_URL = "https://app.verticalstudio.ai/stream/corners"
STREAM_DATA_URL = "https://app.verticalstudio.ai/stream.data"
TEXT_CORNER_TYPE = "text"

# --- 基础请求头 ---
BASE_HEADERS = {
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Cache-Control": "no-cache",
    "Origin": "https://app.verticalstudio.ai",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36"
}

# --- 文本模型映射 ---
MODEL_MAPPING = {
    "claude-3-7-sonnet-thinking": "claude-3-7-sonnet-20250219",
    "claude-4-sonnet-thinking": "claude-4-sonnet-20250514",
    "claude-4-opus-thinking": "claude-4-opus-20250514",
    "deepseek-r1": "deepseek-reasoner",
    "deepseek-v3": "deepseek-chat",
    "gemini-2.5-flash-preview": "gemini-2.5-flash-preview-04-17",
    "gemini-2.5-pro-preview": "gemini-2.5-pro-preview-05-06",
    "gpt-4.1": "gpt-4.1",
    "gpt-4.1-mini": "gpt-4.1-mini",
    "gpt-4o": "gpt-4o",
    "o3": "o3",
    "o4-mini": "o4-mini",
    "grok-3": "grok-3",
}

# --- 全局状态变量 ---
# 全局的 httpx.AsyncClient 在与Flask/Waitress集成时会导致事件循环问题，因此被移除。
# Cookie 将作为独立对象进行管理。
GLOBAL_COOKIES = httpx.Cookies()
COOKIE_LAST_REFRESH = None
COOKIE_REFRESH_INTERVAL = 12 * 60 * 60  # 12 hours
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds
COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.json")
SESSIONS = {}
CHAT_IDS = {}
initialization_complete = asyncio.Event()

def load_credentials():
    dotenv.load_dotenv(".env")
    email = os.getenv("VS_EMAIL")
    password = os.getenv("VS_PASSWORD")
    if not email or not password:
        app.logger.error("环境变量 VS_EMAIL 或 VS_PASSWORD 未设置!")
        return None, None
    app.logger.info("成功从环境变量加载凭据。")
    return email, password

async def load_cookies_from_file():
    global COOKIE_LAST_REFRESH, GLOBAL_COOKIES
    if os.path.exists(COOKIE_FILE):
        try:
            with open(COOKIE_FILE, 'r') as f:
                data = json.load(f)
                cookies_dict = data.get("cookies")
                last_refresh_str = data.get("last_refresh")
                if cookies_dict and last_refresh_str:
                    GLOBAL_COOKIES = httpx.Cookies(cookies_dict)
                    COOKIE_LAST_REFRESH = datetime.fromisoformat(last_refresh_str)
                    app.logger.info("成功从文件加载Cookie。")
                    return True
        except Exception as e:
            app.logger.error(f"加载Cookie文件失败: {e}")
    return False

async def save_cookies_to_file():
    global GLOBAL_COOKIES, COOKIE_LAST_REFRESH
    if GLOBAL_COOKIES and COOKIE_LAST_REFRESH:
        try:
            with open(COOKIE_FILE, 'w') as f:
                json.dump({
                    "cookies": dict(GLOBAL_COOKIES),
                    "last_refresh": COOKIE_LAST_REFRESH.isoformat()
                }, f)
            app.logger.info(f"Cookie已成功保存到 {COOKIE_FILE}")
        except IOError as e:
            app.logger.error(f"保存Cookie到文件失败: {e}")

async def login_and_get_cookies(email, password):
    global COOKIE_LAST_REFRESH, GLOBAL_COOKIES
    app.logger.info("正在尝试登录...")
    try:
        async with httpx.AsyncClient(headers=BASE_HEADERS, timeout=30, follow_redirects=True) as client:
            await client.get(LOGIN_URL)
            email_encoded = urlencode({"email": email})
            login_password_url = f"{LOGIN_PASSWORD_DATA_URL}?{email_encoded}"
            await client.get(login_password_url)
            
            form_data = {"email": email, "password": password}
            response = await client.post(login_password_url, data=form_data, headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
            
            if response.status_code in [200, 202, 302] and any('auth-token' in name for name in client.cookies):
                GLOBAL_COOKIES = client.cookies
                COOKIE_LAST_REFRESH = datetime.now(timezone.utc)
                app.logger.info(f"登录成功，已更新Cookies!")
                await save_cookies_to_file()
                return True
            else:
                # 恢复对auth-token的检查，因为现在是在一个干净的会话中
                app.logger.error(f"登录失败: 状态码 {response.status_code}, 响应: {response.text[:200]}... Cookies: {client.cookies}")
                return False
    except httpx.RequestError as e:
        app.logger.error(f"登录过程中发生网络错误: {e}")
        return False

def check_cookie_refresh():
    if not COOKIE_LAST_REFRESH:
        return True
    return (datetime.now(timezone.utc) - COOKIE_LAST_REFRESH).total_seconds() > COOKIE_REFRESH_INTERVAL

async def schedule_cookie_refresh(email, password):
    async def refresh_loop():
        while True:
            await asyncio.sleep(60 * 60) # 1 hour
            if check_cookie_refresh():
                app.logger.info("Cookie需要刷新，尝试重新登录...")
                if not await login_and_get_cookies(email, password):
                    app.logger.error("Cookie自动刷新失败。")
                else:
                    app.logger.info("Cookie自动刷新成功。")

    asyncio.create_task(refresh_loop())
    app.logger.info("已启动Cookie定时刷新任务。")

async def create_new_chat_session(client, corner_type=TEXT_CORNER_TYPE):
    app.logger.info(f"尝试为 '{corner_type}' 类型创建新的VS会话...")
    try:
        await client.get(STREAM_BASE_URL)
        
        dummy_prompt_val = str(uuid.uuid4())
        post_form_data = {"prompt": dummy_prompt_val, "intent": "execute-prompt"}
        await client.post(f"{STREAM_DATA_URL}?searchType=studio", data=post_form_data, headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
        
        specific_corner_data_url = f"{STREAM_CORNERS_BASE_URL}/{corner_type}.data?prompt={dummy_prompt_val}"
        response = await client.get(specific_corner_data_url, follow_redirects=False)

        new_chat_id = None
        if response.status_code in [202, 301, 302, 303, 307, 308] and 'Location' in response.headers:
            location_url = response.headers['Location']
            match = re.search(rf'/stream/corners/{corner_type}/([\w-]+)', location_url)
            if match: new_chat_id = match.group(1)
        elif response.status_code == 200:
            content_to_search = response.text
            match = re.search(rf'/stream/corners/{corner_type}/([\w-]+)', content_to_search)
            if match: new_chat_id = match.group(1)

        if new_chat_id:
            app.logger.info(f"成功提取到Chat ID: {new_chat_id}")
            return new_chat_id
        else:
            app.logger.error(f"未能提取Chat ID。状态: {response.status_code}, 头: {response.headers}")
            return None
    except httpx.RequestError as e:
        app.logger.error(f"创建VS Chat ID时发生网络错误: {e}")
        return None

async def make_request_with_retry(client, method, url, **kwargs):
    # This function now requires the client to be passed in.
    for attempt in range(MAX_RETRIES):
        try:
            # The client is passed in, so we use it directly.
            response = await client.request(method, url, **kwargs)
            if response.status_code in [401, 403]:
                app.logger.warning(f"认证失败 (状态码 {response.status_code})，尝试重新登录...")
                email, password = load_credentials()
                if email and password and await login_and_get_cookies(email, password):
                    app.logger.info("重新登录成功，但需要调用者使用新Cookie重试。")
                    # Signal to the caller that a retry should happen with fresh cookies.
                    return "RETRY_WITH_NEW_CLIENT"
                else:
                    app.logger.error("重新登录失败。")
                    return None
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as e:
            app.logger.warning(f"请求失败: {e.response.status_code}, 响应: {e.response.text[:200]}")
        except httpx.RequestError as e:
            app.logger.error(f"请求发生错误: {e}")
        
        if attempt < MAX_RETRIES - 1:
            await asyncio.sleep(RETRY_DELAY * (attempt + 1))
    app.logger.error(f"所有 {MAX_RETRIES} 次重试均失败: {url}")
    return None

async def delete_chat_session(client, chat_id_to_delete):
    if not chat_id_to_delete: return
    app.logger.info(f"准备删除临时VS Chat会话: {chat_id_to_delete}")
    try:
        headers = {
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Referer": f"{STREAM_CORNERS_BASE_URL}/{TEXT_CORNER_TYPE}/{chat_id_to_delete}"
        }
        payload = {"chat": chat_id_to_delete}
        response = await make_request_with_retry(client, "POST", ARCHIVE_CHAT_URL, headers=headers, data=payload)
        if response and response != "RETRY_WITH_NEW_CLIENT" and response.status_code == 200:
            app.logger.info(f"临时VS Chat会话 {chat_id_to_delete} 删除成功!")
        else:
            status_text = '无响应'
            if isinstance(response, httpx.Response):
                status_text = response.status_code
            elif response == "RETRY_WITH_NEW_CLIENT":
                status_text = "需要使用新客户端重试"
            app.logger.warning(f"删除临时VS Chat会话 {chat_id_to_delete} 失败，状态码: {status_text}")
    except Exception as e:
        app.logger.error(f"删除临时VS Chat会话时发生错误: {e}")

async def initialize():
    email, password = load_credentials()
    if not (email and password):
        app.logger.critical("未能加载凭据，程序退出。")
        sys.exit(1)

    if not await load_cookies_from_file() or check_cookie_refresh():
        app.logger.info("Cookie无效或过期，尝试登录...")
        if not await login_and_get_cookies(email, password):
            app.logger.critical("登录失败，请检查凭据和网络。程序退出。")
            sys.exit(1)
    else:
        app.logger.info("使用已加载的Cookie。")

    await schedule_cookie_refresh(email, password)
    app.logger.info("初始化完成。")
    app.logger.info("✅ 服务已就绪，可以开始接收 API 请求。")
    initialization_complete.set()

def create_openai_error_response(message, error_type="invalid_request_error", status_code=500):
    # This function relies on Flask's app context. It cannot be used inside
    # the stream_async_generator's separate event loop.
    return jsonify({"error": {"message": message, "type": error_type}}), status_code

def create_manual_openai_error_chunk(message, error_type="invalid_request_error"):
    """
    Creates an OpenAI-compatible error chunk as a dictionary,
    without relying on Flask's app/request context.
    """
    return {
        "error": {
            "message": message,
            "type": error_type,
            "param": None,
            "code": None
        }
    }

# ... [保留 generate_stream_response, generate_stream_done, 等辅助函数] ...
def generate_stream_response(content_chunk, model, message_id):
    chunk_data = {"id": message_id, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
                  "choices": [{"delta": {"content": content_chunk}, "index": 0, "finish_reason": None}]}
    return f"data: {json.dumps(chunk_data)}\n\n"

def generate_stream_done(model, message_id):
    done_data = {"id": message_id, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
                 "choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}]}
    return f"data: {json.dumps(done_data)}\n\n"

def generate_stream_reasoning_response(reasoning_chunk, model, message_id):
    chunk_data = {"id": message_id, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
                  "choices": [{"delta": {"reasoning_content": reasoning_chunk}, "index": 0, "finish_reason": None}]}
    return f"data: {json.dumps(chunk_data)}\n\n"



def build_prompt_with_history_and_instructions(messages_array):
    if not messages_array: return ""
    system_prompt_content = ""
    dialogue_messages = messages_array
    if messages_array[0].get("role") == "system":
        system_prompt_content = messages_array[0].get("content", "").strip()
        dialogue_messages = messages_array[1:]
    
    history_parts = []
    for message in dialogue_messages:
        role = message.get("role", "user").lower()
        content = message.get("content", "").strip()
        if not content: continue
        if role == "system":
            system_prompt_content += f"\n\n{content}"
        elif role in ["user", "human"]:
            history_parts.append(f"\n\nHuman: {content}")
        elif role in ["assistant", "ai"]:
            history_parts.append(f"\n\nAssistant: {content}")

    formatted_history = "\n".join(history_parts).strip()
    
    final_prompt_elements = []
    if formatted_history: final_prompt_elements.append(formatted_history)
    final_prompt_elements.append("\n\nAssistant:")
    
    final_prompt = "\n".join(final_prompt_elements).strip()
    return system_prompt_content, final_prompt

@app.route('/v1/chat/completions', methods=['POST'])
async def chat_completions():
    data = request.get_json()
    if not data:
        return create_openai_error_response("请求体不是有效的JSON。", status_code=400)

    client_messages = data.get("messages", [])
    if not client_messages:
        return create_openai_error_response("`messages`是必需的属性。", status_code=400)

    model_requested = data.get("model", list(MODEL_MAPPING.keys())[0])
    stream = data.get("stream", False)
    
    # Use a client with a lifecycle tied to the request
    async with httpx.AsyncClient(headers=BASE_HEADERS, cookies=GLOBAL_COOKIES, timeout=30, follow_redirects=True) as client:
        temp_vs_chat_id = None
        try:
            temp_vs_chat_id = await create_new_chat_session(client)
            if not temp_vs_chat_id:
                return create_openai_error_response("无法创建新的聊天会话。", status_code=502)

            system_prompt, final_prompt = build_prompt_with_history_and_instructions(client_messages)
            vs_text_model_id = MODEL_MAPPING.get(model_requested, list(MODEL_MAPPING.values())[0])
            
            openai_msg_id = f"chatcmpl-{uuid.uuid4().hex}"
            vs_msg_id = str(uuid.uuid4()).replace("-", "")[:16]
            created_at = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')

            payload = {
                "message": {"id": vs_msg_id, "createdAt": created_at, "role": "user", "content": final_prompt, "parts": [{"type": "text", "text": final_prompt}]},
                "cornerType": TEXT_CORNER_TYPE, "chatId": temp_vs_chat_id,
                "settings": {"modelId": vs_text_model_id, "customSystemPrompt": system_prompt}
            }
            if "claude" in vs_text_model_id:
                 payload["settings"]["reasoning"] = "on"

            headers = {"Content-Type": "application/json", "Referer": f"{STREAM_CORNERS_BASE_URL}/{TEXT_CORNER_TYPE}/{temp_vs_chat_id}"}
            
            if stream:
                data_queue = queue.Queue()

                async def producer():
                    # This async producer runs the original async generator
                    # and puts the data into a thread-safe queue.
                    nonlocal temp_vs_chat_id
                    client_for_stream = httpx.AsyncClient(headers=BASE_HEADERS, cookies=GLOBAL_COOKIES, timeout=30, follow_redirects=True)
                    try:
                        async with client_for_stream.stream("POST", CHAT_API_URL, json=payload, headers=headers, timeout=120) as response:
                            if response.status_code >= 400:
                                error_body = await response.aread()
                                error_msg = f"Upstream API error: {response.status_code} - {error_body.decode()}"
                                app.logger.error(error_msg)
                                error_chunk = create_manual_openai_error_chunk(error_msg)
                                data_queue.put(f"data: {json.dumps(error_chunk)}\n\n")
                                return

                            async for line in response.aiter_lines():
                                if line.startswith("0:"):
                                    chunk = json.loads(line[2:])
                                    data_queue.put(generate_stream_response(chunk, model_requested, openai_msg_id))
                                elif line.startswith("g:"):
                                    chunk = json.loads(line[2:])
                                    data_queue.put(generate_stream_reasoning_response(chunk, model_requested, openai_msg_id))
                            
                            data_queue.put(generate_stream_done(model_requested, openai_msg_id))
                            data_queue.put("data: [DONE]\n\n")

                    except Exception as e:
                        app.logger.error(f"Error during stream proxy: {e}", exc_info=True)
                        error_chunk = create_manual_openai_error_chunk(f"Internal stream error: {e}")
                        data_queue.put(f"data: {json.dumps(error_chunk)}\n\n")
                    
                    finally:
                        await client_for_stream.aclose()
                        if temp_vs_chat_id:
                            async with httpx.AsyncClient(headers=BASE_HEADERS, cookies=GLOBAL_COOKIES) as cleanup_client:
                                await delete_chat_session(cleanup_client, temp_vs_chat_id)
                                temp_vs_chat_id = None
                        data_queue.put(None)  # Signal that we are done.

                def consumer():
                    # This sync consumer pulls data from the queue and yields it.
                    # This is what Flask's Response object will interact with.
                    while True:
                        item = data_queue.get()
                        if item is None:
                            break
                        yield item

                # Run the async producer in a separate daemon thread.
                thread = threading.Thread(target=lambda: asyncio.run(producer()), daemon=True)
                thread.start()

                return Response(consumer(), mimetype="text/event-stream")

            # Handle non-streaming case
            else:
                response = await make_request_with_retry(client, "POST", CHAT_API_URL, json=payload, headers=headers, timeout=120)

                if response == "RETRY_WITH_NEW_CLIENT":
                    app.logger.info("Retrying request with fresh cookies...")
                    async with httpx.AsyncClient(headers=BASE_HEADERS, cookies=GLOBAL_COOKIES, timeout=30, follow_redirects=True) as retry_client:
                        response = await make_request_with_retry(retry_client, "POST", CHAT_API_URL, json=payload, headers=headers, timeout=120)

                if response is None or response == "RETRY_WITH_NEW_CLIENT":
                    return create_openai_error_response("上游API请求失败。", status_code=502)

                full_content = []
                # The response body is already fully in memory for non-streaming requests
                for line in response.text.splitlines():
                     if line.startswith("0:"):
                        full_content.append(json.loads(line[2:]))
                
                final_text = "".join(full_content)
                # Cleanup after successful processing
                await delete_chat_session(client, temp_vs_chat_id)

                return jsonify({
                    "id": openai_msg_id, "object": "chat.completion", "created": int(time.time()), "model": model_requested,
                    "choices": [{"message": {"role": "assistant", "content": final_text}, "index": 0, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                })

        except Exception as e:
            app.logger.error(f"处理请求时发生错误: {e}", exc_info=True)
            if temp_vs_chat_id:
                # Use a new client for cleanup in case the original one is in a bad state.
                async with httpx.AsyncClient(headers=BASE_HEADERS, cookies=GLOBAL_COOKIES) as cleanup_client:
                    await delete_chat_session(cleanup_client, temp_vs_chat_id)
            return create_openai_error_response(f"内部服务器错误: {str(e)}", status_code=500)


@app.route('/v1/models', methods=['GET'])
def get_models_endpoint():
    models = [{"id": k, "object": "model", "owned_by": "vsp-text", "permission": []} for k in MODEL_MAPPING.keys()]
    return jsonify({"data": models, "object": "list"})

async def main():
    # 在程序启动时执行初始化
    await initialize()
    
    # 初始化完成后，再启动 ASGI 服务器
    from hypercorn.config import Config
    from hypercorn.asyncio import serve as hypercorn_serve

    config = Config()
    app_port = int(os.environ.get("PORT", 7860))
    config.bind = [f"0.0.0.0:{app_port}"]
    config.graceful_timeout = 5  # 设置优雅关闭的超时时间
    app.logger.info(f"服务器正在 http://0.0.0.0:{app_port} 上启动...")
    await hypercorn_serve(app, config)


if __name__ == '__main__':
    # 设置Windows特定的asyncio事件循环策略以避免常见错误
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    # 运行主异步函数
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        app.logger.info("服务器被手动中断。")
