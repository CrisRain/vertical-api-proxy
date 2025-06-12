import dotenv
from quart import Quart, request, jsonify, Response
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
import aiofiles
import typing
from hypercorn.asyncio import serve
from hypercorn.config import Config

app = Quart(__name__)
# --- Logging Configuration ---
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
app.logger.handlers.clear()
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
# --- Timeout Configuration ---
DEFAULT_REQUEST_TIMEOUT = httpx.Timeout(3600.0, connect=3600.0, read=3600.0, write=3600.0)

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
GLOBAL_COOKIES = httpx.Cookies()
COOKIE_LAST_REFRESH = None
COOKIE_REFRESH_INTERVAL = 12 * 60 * 60  # 12 hours
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds
COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.json")
SESSIONS = {}
CHAT_IDS = {}
initialization_complete = asyncio.Event() # Signals that the server is ready to accept requests
login_pending = asyncio.Event() # Signals if a background login/setup task is active. Set = idle/complete, Clear = active.
login_pending.set() # Initialize as idle
cookies_are_genuinely_valid = False # Tracks if cookies are verified and usable
HTTP_CLIENT: typing.Optional[httpx.AsyncClient] = None # Global HTTP client

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
            async with aiofiles.open(COOKIE_FILE, 'r') as f:
                content = await f.read()
                data = json.loads(content)
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
            async with aiofiles.open(COOKIE_FILE, 'w') as f:
                await f.write(json.dumps({
                    "cookies": dict(GLOBAL_COOKIES),
                    "last_refresh": COOKIE_LAST_REFRESH.isoformat()
                }))
            app.logger.info(f"Cookie已成功保存到 {COOKIE_FILE}")
        except IOError as e:
            app.logger.error(f"保存Cookie到文件失败: {e}")

async def login_and_get_cookies(email, password):
    global COOKIE_LAST_REFRESH, GLOBAL_COOKIES, HTTP_CLIENT
    app.logger.info("正在尝试登录...")
    try:
        # Use a temporary client for the login process to avoid altering global client's state prematurely
        async with httpx.AsyncClient(headers=BASE_HEADERS, timeout=30, follow_redirects=True) as login_client:
            await login_client.get(LOGIN_URL)
            email_encoded = urlencode({"email": email})
            login_password_url = f"{LOGIN_PASSWORD_DATA_URL}?{email_encoded}"
            await login_client.get(login_password_url)
            
            form_data = {"email": email, "password": password}
            response = await login_client.post(login_password_url, data=form_data, headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
            
            if response.status_code in [200, 202, 302] and any('auth-token' in name for name in login_client.cookies):
                GLOBAL_COOKIES = login_client.cookies
                COOKIE_LAST_REFRESH = datetime.now(timezone.utc)
                if HTTP_CLIENT:
                    HTTP_CLIENT.cookies = GLOBAL_COOKIES # Update global client's cookies
                app.logger.info(f"登录成功，已更新Cookies (全局和HTTP_CLIENT)!")
                await save_cookies_to_file()
                return True
            else:
                app.logger.error(f"登录失败: 状态码 {response.status_code}, 响应: {response.text[:200]}... Cookies: {login_client.cookies}")
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

async def create_new_chat_session(corner_type=TEXT_CORNER_TYPE): # Removed client argument
    if not HTTP_CLIENT:
        app.logger.error("创建新聊天会话失败：全局 HTTP_CLIENT 未初始化。")
        raise Exception("内部服务器错误: HTTP客户端未就绪")

    app.logger.info(f"尝试为 '{corner_type}' 类型创建新的VS会话...")
    try:
        # Step 1: Initial GET to stream base URL (seems like a pre-step, session warmer?)
        # Using make_request_with_retry to handle potential issues like client not ready or network errors
        response_stream_base = await make_request_with_retry("GET", STREAM_BASE_URL)
        if not response_stream_base:
            app.logger.error(f"创建VS Chat ID的第一步GET {STREAM_BASE_URL} 失败。")
            return None
        
        # Step 2: POST dummy prompt
        dummy_prompt_val = str(uuid.uuid4())
        post_form_data = {"prompt": dummy_prompt_val, "intent": "execute-prompt"}
        response_post_dummy = await make_request_with_retry("POST", f"{STREAM_DATA_URL}?searchType=studio", data=post_form_data, headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
        if not response_post_dummy:
             app.logger.error(f"创建VS Chat ID的第二步POST {STREAM_DATA_URL} 失败。")
             return None

        # Step 3: GET specific corner data URL, expecting a redirect or chat ID in response
        specific_corner_data_url = f"{STREAM_CORNERS_BASE_URL}/{corner_type}.data?prompt={dummy_prompt_val}"
        
        # This specific GET in original code used follow_redirects=False and parsed Location header.
        # We need a client instance that has follow_redirects=False for this step.
        # It's safer to create a temporary client for this specific request if global HTTP_CLIENT follows redirects.
        async with httpx.AsyncClient(headers=BASE_HEADERS, cookies=GLOBAL_COOKIES, timeout=30, follow_redirects=False) as temp_redirect_client:
            response_get_corner = await temp_redirect_client.get(specific_corner_data_url)

        new_chat_id = None
        if response_get_corner.status_code in [202, 301, 302, 303, 307, 308] and 'Location' in response_get_corner.headers:
            location_url = response_get_corner.headers['Location']
            match = re.search(rf'/stream/corners/{corner_type}/([\w-]+)', location_url)
            if match: new_chat_id = match.group(1)
        elif response_get_corner.status_code == 200: # Sometimes the ID is in the body on 200 OK
            content_to_search = response_get_corner.text
            match = re.search(rf'/stream/corners/{corner_type}/([\w-]+)', content_to_search)
            if match: new_chat_id = match.group(1)

        if new_chat_id:
            app.logger.info(f"成功提取到Chat ID: {new_chat_id}")
            return new_chat_id
        else:
            # Log more info for debugging if chat_id extraction fails
            app.logger.error(f"未能提取Chat ID。URL: {specific_corner_data_url}, 状态: {response_get_corner.status_code}, 头: {response_get_corner.headers}, 响应体(部分): {response_get_corner.text[:200]}")
            return None
    except httpx.RequestError as e: # Catch network errors from any step
        app.logger.error(f"创建VS Chat ID时发生网络错误: {e}。这通常表示DNS解析失败或网络连接问题。请检查您的网络设置和到 'app.verticalstudio.ai' 的连接。")
        raise e # Re-raise to be caught by the calling handler (e.g., handle_chat_request)

async def make_request_with_retry(method, url, **kwargs): # Removed client argument
    if not HTTP_CLIENT:
        app.logger.error("全局 HTTP_CLIENT 未初始化! 无法执行请求。")
        # This is a critical internal error.
        # Depending on context, might raise an exception or return a specific error indicator.
        return None

    # kwargs can include 'json', 'data', 'headers'.
    # HTTP_CLIENT.cookies is assumed to be managed and up-to-date via login_and_get_cookies.
    
    for attempt in range(MAX_RETRIES):
        try:
            response = await HTTP_CLIENT.request(method, url, **kwargs)

            if response.status_code in [401, 403]: # Unauthorized or Forbidden
                app.logger.warning(f"请求 {method} {url} 认证失败 (状态码 {response.status_code})。尝试重新登录...")
                email_creds, password_creds = load_credentials() # Ensure var names don't clash if this is nested
                if email_creds and password_creds:
                    if await login_and_get_cookies(email_creds, password_creds): # This updates HTTP_CLIENT.cookies
                        app.logger.info("重新登录成功。将重试之前的请求。")
                        # Cookies in HTTP_CLIENT are now fresh. Continue to the next attempt to retry the request.
                        if attempt < MAX_RETRIES - 1: # Only sleep and continue if there are retries left
                             await asyncio.sleep(RETRY_DELAY) # Wait a bit before retrying
                             continue # Retry the request in the next iteration of the loop
                        else:
                             app.logger.error(f"重新登录成功，但已达到对 {method} {url} 的最大重试次数。")
                             return response # Return the 401/403 response after last attempt
                    else:
                        app.logger.error(f"重新登录失败。无法重试请求 {method} {url}。")
                        return response # Return the 401/403 response
                else:
                    app.logger.error(f"无法加载凭据进行重新登录以重试 {method} {url}。")
                    return response # Return the 401/403 response
            
            response.raise_for_status() # Raise an HTTPStatusError for other 4xx/5xx responses
            return response
        
        except httpx.HTTPStatusError as e: # Errors raised by response.raise_for_status() or non-401/403 status codes
            app.logger.warning(f"请求 {method} {url} 失败 (尝试 {attempt + 1}/{MAX_RETRIES}): 状态码 {e.response.status_code}, 响应(部分): {e.response.text[:200]}")
            # Retry only for specific server-side errors or if configured
            if e.response.status_code in [500, 502, 503, 504]: # Common retryable server errors
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAY * (attempt + 1)) # Exponential backoff might be better
                    continue
            return e.response # Return the error response if not retrying or after last retry
            
        except httpx.RequestError as e: # Network-level errors (ConnectTimeout, ReadTimeout, DNS error etc.)
            app.logger.error(f"请求 {method} {url} (尝试 {attempt + 1}/{MAX_RETRIES}) 发生网络错误: {type(e).__name__} - {e}")
        
        # Common delay for retries due to RequestError or if loop continues after HTTPStatusError retry
        if attempt < MAX_RETRIES - 1:
            await asyncio.sleep(RETRY_DELAY * (attempt + 1))
            
    app.logger.error(f"对 {method} {url} 的所有 {MAX_RETRIES} 次重试均失败。")
    return None # Indicate all retries failed

async def delete_chat_session(chat_id_to_delete): # Removed client argument
    if not HTTP_CLIENT:
        app.logger.error("删除聊天会话失败：全局 HTTP_CLIENT 未初始化。")
        return
    if not chat_id_to_delete:
        app.logger.debug("delete_chat_session called with no chat_id_to_delete.")
        return

    app.logger.info(f"准备删除临时VS Chat会话: {chat_id_to_delete}")
    try:
        headers = {
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Referer": f"{STREAM_CORNERS_BASE_URL}/{TEXT_CORNER_TYPE}/{chat_id_to_delete}"
        }
        payload = {"chat": chat_id_to_delete}
        # Use make_request_with_retry for deleting the session as well
        response = await make_request_with_retry("POST", ARCHIVE_CHAT_URL, headers=headers, data=payload)
        
        if response and response.status_code == 200:
            app.logger.info(f"临时VS Chat会话 {chat_id_to_delete} 删除成功!")
        elif response: # Response received, but not 200 OK
            app.logger.warning(f"删除临时VS Chat会话 {chat_id_to_delete} 失败，状态码: {response.status_code}, 响应(部分): {response.text[:200]}")
        else: # No response from make_request_with_retry, meaning all retries failed
             app.logger.warning(f"删除临时VS Chat会话 {chat_id_to_delete} 失败，所有重试均未成功或未收到响应。")
    except Exception as e: # Catch any other unexpected errors during the delete process
        app.logger.error(f"删除临时VS Chat会话 {chat_id_to_delete} 时发生意外错误: {type(e).__name__} - {e}", exc_info=True)

async def background_login_and_setup(email, password):
    global cookies_are_genuinely_valid, login_pending
    
    login_pending.clear() # Indicate background login is in progress
    app.logger.info("后台登录和设置任务已启动。")
    
    try:
        if await login_and_get_cookies(email, password): # This updates GLOBAL_COOKIES and HTTP_CLIENT.cookies
            cookies_are_genuinely_valid = True
            app.logger.info("后台登录成功。Cookies 已验证并更新。")
            # Schedule regular refreshes only after a successful login
            # Pass email and password to the task directly.
            asyncio.create_task(schedule_cookie_refresh(email, password))
        else:
            cookies_are_genuinely_valid = False
            app.logger.error("后台登录失败。服务可能无法正常处理依赖认证的请求。将不会启动定时刷新。")
            # Consider adding more robust retry logic for background_login_and_setup itself if initial fails,
            # or a periodic check to re-attempt background_login_and_setup.
    except Exception as e:
        cookies_are_genuinely_valid = False
        app.logger.error(f"后台登录任务中发生意外错误: {type(e).__name__} - {e}", exc_info=True)
    finally:
        login_pending.set() # Signal completion of this login attempt (success or fail)
        app.logger.info(f"后台登录和设置任务已结束。最终认证状态: {cookies_are_genuinely_valid}")

async def initialize():
    global cookies_are_genuinely_valid, COOKIE_LAST_REFRESH, GLOBAL_COOKIES, HTTP_CLIENT

    email, password = load_credentials()
    if not (email and password):
        app.logger.critical("未能加载凭据，无法继续初始化。程序退出。")
        # Instead of sys.exit, let Quart handle startup failure if possible, or raise a specific exception.
        raise RuntimeError("VS_EMAIL or VS_PASSWORD not set in environment.")

    # Try to load cookies from file first. This updates GLOBAL_COOKIES and COOKIE_LAST_REFRESH.
    if await load_cookies_from_file():
        if not check_cookie_refresh(): # Check if loaded cookies are still fresh and valid
            cookies_are_genuinely_valid = True
            if HTTP_CLIENT: # Ensure global client uses these loaded cookies
                 HTTP_CLIENT.cookies = GLOBAL_COOKIES # This should be the same object instance if load_cookies_from_file modifies GLOBAL_COOKIES in place
            app.logger.info("成功从文件加载有效且未过期的Cookie。将安排后台刷新。")
            # Schedule refresh task even if current cookies are valid, for future expirations.
            asyncio.create_task(schedule_cookie_refresh(email, password))
        else:
            # Cookies loaded from file but are expired or failed staleness check
            app.logger.info("从文件加载的Cookie已过期或无效。将在后台尝试刷新/登录。")
            cookies_are_genuinely_valid = False # Mark as invalid until background login succeeds
            asyncio.create_task(background_login_and_setup(email, password))
    else:
        # Failed to load cookies from file (e.g., first run, or file corrupted)
        app.logger.info("未能从文件加载Cookie。将在后台尝试执行初始登录。")
        cookies_are_genuinely_valid = False
        asyncio.create_task(background_login_and_setup(email, password))

    app.logger.info("核心初始化逻辑已调度。服务器即将启动。")
    app.logger.info("注意: 依赖认证的API功能可能需要等待后台登录/Cookie验证完成。")
    initialization_complete.set() # Unblock server startup quickly

def create_openai_error_response(message, error_type="invalid_request_error", status_code=500):
    response_data = {"error": {"message": message, "type": error_type}}
    return jsonify(response_data), status_code

def create_manual_openai_error_chunk(message, error_type="invalid_request_error"):
    return {
        "error": {
            "message": message,
            "type": error_type,
            "param": None,
            "code": None
        }
    }

def generate_stream_response(content_chunk, model, message_id):
    chunk_data = {"id": message_id, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
                  "choices": [{"delta": {"content": content_chunk}, "index": 0, "finish_reason": None}]}
    return f"data: {json.dumps(chunk_data)}\n\n"

def generate_stream_done(model, message_id, usage=None):
    done_data = {"id": message_id, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
                 "choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}]}
    if usage:
        done_data['usage'] = usage
    return f"data: {json.dumps(done_data)}\n\n"

def generate_stream_reasoning_response(reasoning_chunk, model, message_id):
    chunk_data = {"id": message_id, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
                  "choices": [{"delta": {"reasoning_content": reasoning_chunk}, "index": 0, "finish_reason": None}]}
    return f"data: {json.dumps(chunk_data)}\n\n"


def build_prompt_with_history_and_instructions(messages_array):
    if not messages_array:
        return "", ""

    system_prompts = []
    history_parts = []
    
    for message in messages_array:
        role = message.get("role", "user").lower()
        content = message.get("content", "")
        
        if not isinstance(content, str):
            app.logger.warning(f"消息内容不是字符串，已跳过: {content}")
            continue
        
        content = content.strip()
        if not content:
            continue

        if role == "system":
            system_prompts.append(content)
        elif role in ["user", "human"]:
            history_parts.append(f"\n\nHuman: {content}")
        elif role in ["assistant", "ai"]:
            history_parts.append(f"\n\nAssistant: {content}")

    system_prompt_content = "\n\n".join(system_prompts)
    formatted_history = "".join(history_parts).strip()
    
    final_prompt_elements = []
    if formatted_history:
        final_prompt_elements.append(formatted_history)
    final_prompt_elements.append("\n\nAssistant:")
    
    final_prompt = "".join(final_prompt_elements)
    return system_prompt_content, final_prompt

async def handle_chat_request(data) -> typing.Union[Response, tuple[Response, int]]:
    client_messages = data.get("messages", [])
    model_requested = data.get("model", list(MODEL_MAPPING.keys())[0])
    stream = data.get("stream", False)
    response_to_return = None

    if not HTTP_CLIENT:
        app.logger.error("处理聊天请求失败：关键的全局 HTTP_CLIENT 未初始化。")
        return create_openai_error_response("内部服务器配置错误，HTTP客户端丢失。", status_code=500)
        
    temp_vs_chat_id = None
    try:
        temp_vs_chat_id = await create_new_chat_session()
        if not temp_vs_chat_id:
            raise Exception("无法创建新的聊天会话。请检查上游服务状态或网络连接。")

        system_prompt, final_prompt = build_prompt_with_history_and_instructions(client_messages)
        vs_text_model_id = MODEL_MAPPING.get(model_requested, list(MODEL_MAPPING.values())[0])
        
        openai_msg_id = f"chatcmpl-{uuid.uuid4().hex}"
        vs_msg_id = str(uuid.uuid4()).replace("-", "")[:16]
        created_at_iso = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')

        payload = {
            "message": {"id": vs_msg_id, "createdAt": created_at_iso, "role": "user", "content": final_prompt, "parts": [{"type": "text", "text": final_prompt}]},
            "cornerType": TEXT_CORNER_TYPE, "chatId": temp_vs_chat_id,
            "settings": {"modelId": vs_text_model_id, "customSystemPrompt": system_prompt}
        }
        if "claude" in vs_text_model_id:
            payload["settings"]["reasoning"] = "on"

        chat_api_headers = {"Content-Type": "application/json", "Referer": f"{STREAM_CORNERS_BASE_URL}/{TEXT_CORNER_TYPE}/{temp_vs_chat_id}"}
        
        if stream:
            queue = asyncio.Queue()

            async def data_reader(target_chat_id, stream_payload_data):
                try:
                    async with httpx.AsyncClient(headers=BASE_HEADERS, cookies=GLOBAL_COOKIES, timeout=None, follow_redirects=False) as client:
                        async with client.stream("POST", CHAT_API_URL, json=stream_payload_data, headers=chat_api_headers) as response:
                            if response.status_code != 200:
                                error_body = await response.aread()
                                await queue.put(httpx.HTTPStatusError(f"上游API流式响应错误: {response.status_code}", request=response.request, response=response))
                                return

                            async for line in response.aiter_lines():
                                await queue.put(line)
                except Exception as e:
                    await queue.put(e)
                finally:
                    await queue.put(None) # Signal completion

            async def heartbeat_sender():
                last_activity_time = time.time()
                while True:
                    await asyncio.sleep(5)  # 更频繁地检查
                    current_time = time.time()
                    # 无论队列状态如何，每30秒发送一次心跳
                    if current_time - last_activity_time >= 5:
                        await queue.put(":heartbeat\n\n")
                        last_activity_time = current_time

            async def stream_generator():
                reader_task = asyncio.create_task(data_reader(temp_vs_chat_id, payload))
                heartbeat_task = asyncio.create_task(heartbeat_sender())
                
                stream_usage_data = None
                try:
                    while True:
                        item = await queue.get()
                        if item is None: # End of stream from reader
                            break
                        
                        if isinstance(item, Exception):
                            raise item

                        if item == ":heartbeat\n\n":
                            yield item.encode('utf-8')
                            continue
                        
                        line_content = item
                        try:
                            if line_content.startswith("e:") or line_content.startswith("d:"):
                                json_data_str = line_content[2:]
                                if json_data_str:
                                    end_stream_data = json.loads(json_data_str)
                                    if end_stream_data.get("usage"):
                                        stream_usage_data = end_stream_data["usage"]
                                break
                            elif line_content.startswith("g:"):
                                yield generate_stream_reasoning_response(json.loads(line_content[2:]), model_requested, openai_msg_id).encode('utf-8')
                            elif line_content.startswith("0:"):
                                yield generate_stream_response(json.loads(line_content[2:]), model_requested, openai_msg_id).encode('utf-8')
                        
                        except (json.JSONDecodeError, Exception) as e:
                            app.logger.warning(f"处理流数据行时出错: {line_content}, Error: {e}")
                            
                    yield generate_stream_done(model_requested, openai_msg_id, stream_usage_data).encode('utf-8')
                except asyncio.CancelledError:
                    app.logger.warning(f"客户端 for chat {temp_vs_chat_id} 断开连接。")
                finally:
                    heartbeat_task.cancel()
                    reader_task.cancel() # Ensure reader task is cancelled
                    await delete_chat_session(temp_vs_chat_id)
            
            response_to_return = Response(stream_generator(), mimetype='text/event-stream') # type: ignore
        else: # Non-streaming logic remains the same
            api_response = await make_request_with_retry("POST", CHAT_API_URL, json=payload, headers=chat_api_headers, timeout=None)
            if not api_response or api_response.status_code != 200:
                raise Exception("上游API请求失败或响应无效。")
            
            full_response_content, non_stream_usage_info = [], None
            for line_item in api_response.text.splitlines():
                if line_item.startswith("0:"):
                    full_response_content.append(json.loads(line_item[2:]))
                elif (line_item.startswith("e:") or line_item.startswith("d:")) and (json_data_str := line_item[2:]):
                    try:
                        end_signal_data = json.loads(json_data_str)
                        if "usage" in end_signal_data: non_stream_usage_info = end_signal_data["usage"]
                    except json.JSONDecodeError: pass
            
            final_response_text = "".join(full_response_content)
            response_to_return = jsonify({
                "id": openai_msg_id, "object": "chat.completion", "created": int(time.time()), "model": model_requested,
                "choices": [{"message": {"role": "assistant", "content": final_response_text}, "index": 0, "finish_reason": "stop"}],
                "usage": non_stream_usage_info or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            })

    except Exception as e:
        app.logger.error(f"处理聊天请求时发生错误: {e}", exc_info=True)
        if stream:
            async def error_stream():
                yield f"data: {json.dumps(create_manual_openai_error_chunk(str(e)))}\n\n".encode('utf-8')
            return Response(error_stream(), mimetype='text/event-stream', status=500) # type: ignore
        else:
            return create_openai_error_response(str(e), status_code=500)
    finally:
        if not stream and temp_vs_chat_id:
            await delete_chat_session(temp_vs_chat_id)

    return response_to_return

@app.route('/v1/chat/completions', methods=['POST'])
async def chat_completions() -> typing.Union[Response, tuple[Response, int]]:
    await initialization_complete.wait() # This should pass quickly once server starts

    # Check if background login/setup is still pending
    if not login_pending.is_set(): # login_pending is clear() if task is running
        app.logger.info("后台认证/设置仍在进行中，请等待片刻...")
        try:
            # Wait for the background task to complete, with a timeout
            await asyncio.wait_for(login_pending.wait(), timeout=15.0) # login_pending.wait() waits until set()
        except asyncio.TimeoutError:
            app.logger.warning("等待后台认证/设置超时。服务可能尚未完全就绪。")
            return create_openai_error_response("服务正在进行初始设置，请稍后重试。", status_code=503) # Service Unavailable

    # After waiting (or if it wasn't pending), check the actual cookie validity
    if not cookies_are_genuinely_valid:
        app.logger.error("无法处理聊天请求：Cookies 无效或后台认证失败。")
        return create_openai_error_response("认证信息无效或后台初始化失败，无法处理请求。请检查服务器日志。", status_code=503)

    # If we reach here, background task is done (or wasn't running) AND cookies are valid.
    try:
        data = await request.get_json()
        if not data:
            return create_openai_error_response("请求体不是有效的JSON，或为空。", status_code=400)
        if not data.get("messages"): # Ensure messages list is present
            return create_openai_error_response("请求体中缺少必需的 `messages` 属性。", status_code=400)
        
        return await handle_chat_request(data)

    except httpx.RequestError as e_req: # Catch network errors from handle_chat_request or its sub-calls
        app.logger.error(f"处理聊天完成请求时发生网络连接错误: {type(e_req).__name__} - {e_req}", exc_info=True)
        return create_openai_error_response(f"上游服务网络连接错误: {e_req}", status_code=502) # Bad Gateway
    except Exception as e_general: # Catch-all for other unexpected errors
        app.logger.error(f"处理聊天完成请求时发生未知错误: {type(e_general).__name__} - {e_general}", exc_info=True)
        return create_openai_error_response(f"内部服务器错误: {e_general}", status_code=500)


@app.route('/v1/models', methods=['GET'])
async def get_models_endpoint():
    models = [{"id": k, "object": "model", "owned_by": "vsp-text", "permission": []} for k in MODEL_MAPPING.keys()]
    return jsonify({"data": models, "object": "list"})

# --- Server Startup & Shutdown ---
@app.before_serving
async def startup():
    global HTTP_CLIENT
    # Initialize the global HTTP client first
    # Cookies will be added to it by initialize() or background_login_and_setup() via login_and_get_cookies()
    HTTP_CLIENT = httpx.AsyncClient(headers=BASE_HEADERS, timeout=DEFAULT_REQUEST_TIMEOUT, follow_redirects=True)
    app.logger.info("全局 HTTP_CLIENT 已在启动时创建。")

    # Run the main initialization logic. This will set initialization_complete event quickly.
    # Actual login might happen in the background.
    await initialize()
    # Log after initialize() has run, which sets initialization_complete
    app.logger.info("服务核心启动流程完成。可开始接受请求。后台任务可能仍在运行。")

@app.after_serving
async def shutdown():
    global HTTP_CLIENT
    if HTTP_CLIENT:
        app.logger.info("正在关闭全局 HTTP_CLIENT...")
        await HTTP_CLIENT.aclose()
        HTTP_CLIENT = None
        app.logger.info("全局 HTTP_CLIENT 已关闭。")
    app.logger.info("服务器已关闭。")

if __name__ == "__main__":
    # Force UTF-8 encoding on Windows for stdio streams
    if sys.platform == "win32":
        os.environ["PYTHONIOENCODING"] = "utf-8"

    # Create a Hypercorn configuration object
    config = Config()
    port = int(os.environ.get("PORT", 7860))
    config.bind = [f"0.0.0.0:{port}"]
    config.keep_alive_timeout = 3600.0
    config.read_timeout = 3600
    # 添加这些关键配置
    config.worker_class = "asyncio"  # 确保使用asyncio工作模式

    app.config['RESPONSE_TIMEOUT'] = 3600  # 设置Quart的响应超时
    
    # Run the app with Hypercorn asyncio server
    asyncio.run(serve(app, config))
