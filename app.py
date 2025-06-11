import dotenv
from flask import Flask, request, jsonify, Response
import requests
import json
import uuid
# import yaml # No longer needed for credentials, and potentially not at all
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode, quote
import os
import sys
import gzip
import brotli
import re
import time
from threading import Thread
import atexit
import logging
import base64
import requests.utils # 新增导入

app = Flask(__name__)
# 使用UTF-8编码配置日志记录器，以防止在Windows终端中出现乱码
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)
)
app.logger.setLevel(logging.INFO)

# --- Vertical Studio AI 接口地址 ---
LOGIN_URL = "https://app.verticalstudio.ai/login"  # 登录页面
LOGIN_PASSWORD_DATA_URL = "https://app.verticalstudio.ai/login-password.data"  # 登录提交地址
CHAT_API_URL = "https://app.verticalstudio.ai/api/chat"  # 聊天API
ARCHIVE_CHAT_URL = "https://app.verticalstudio.ai/api/chat/archive.data"  # 归档聊天会话地址

# --- 流式传输相关地址 ---
STREAM_BASE_URL = "https://app.verticalstudio.ai/stream"  # 流式传输基础地址
STREAM_CORNERS_BASE_URL = "https://app.verticalstudio.ai/stream/corners"  # 创建不同类型会话的地址
STREAM_DATA_URL = "https://app.verticalstudio.ai/stream.data"  # 流式数据提交地址
TEXT_CORNER_TYPE = "text"  # 文本会话类型

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
# 键是客户端（如OpenAI兼容客户端）发送的模型名称，值是Vertical Studio AI实际使用的模型ID
MODEL_MAPPING = {
    "claude-3-7-sonnet": "claude-3-7-sonnet-20250219",
    "claude-4-sonnet": "claude-4-sonnet-20250514",
    "claude-4-opus": "claude-4-opus-20250219",
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

# --- 全局会话变量 ---
GLOBAL_REQUEST_SESSION = requests.Session() # 全局的 requests Session 对象
COOKIE_LAST_REFRESH = None  # 上次Cookie刷新时间
COOKIE_REFRESH_INTERVAL = 12 * 60 * 60  # Cookie刷新间隔（12小时）
MAX_RETRIES = 3  # 请求失败后的最大重试次数
RETRY_DELAY = 2  # 每次重试之间的延迟（秒）
REQUEST_INTERVAL = 0.5  # 两个请求之间的最小间隔（秒），防止请求过于频繁
COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.json") # Cookie文件路径
SESSIONS = {}  # 存储活跃的聊天会话信息
CHAT_IDS = {}  # 存储VS Chat ID与OpenAI消息ID等的映射关系

def load_credentials():
    """
    从环境变量中加载登录凭据 (VS_EMAIL 和 VS_PASSWORD)。
    :return: (email, password) 元组，如果未设置则返回 (None, None)。
    """
    # 加载.env文件中的环境变量
    dotenv.load_dotenv(".env")

    email = os.getenv("VS_EMAIL")
    password = os.getenv("VS_PASSWORD")
    if not email or not password:
        app.logger.error("环境变量 VS_EMAIL 或 VS_PASSWORD 未设置!")
        return None, None
    app.logger.info("成功从环境变量加载凭据。")
    return email, password

# --- MODIFIED COOKIE HANDLING: IN-MEMORY ONLY ---
def load_cookies_from_file():
    """
    从本地文件加载Cookie。
    :return: 如果成功加载并验证Cookie，返回 True，否则返回 False。
    """
    global GLOBAL_REQUEST_SESSION, COOKIE_LAST_REFRESH
    if os.path.exists(COOKIE_FILE):
        try:
            with open(COOKIE_FILE, 'r') as f:
                data = json.load(f)
                loaded_cookies_dict = data.get("cookies")
                last_refresh_str = data.get("last_refresh")
                if loaded_cookies_dict and last_refresh_str:
                    GLOBAL_REQUEST_SESSION.cookies = requests.utils.cookiejar_from_dict(loaded_cookies_dict)
                    COOKIE_LAST_REFRESH = datetime.fromisoformat(last_refresh_str)
                    app.logger.info("成功从文件加载Cookie到全局Session。")
                    return True
        except (json.JSONDecodeError, IOError, TypeError) as e: # 添加 TypeError 以防 cookiejar_from_dict 出错
            app.logger.error(f"加载Cookie文件失败: {e}")
    return False

def save_cookies_to_file():
    """
    将会话Cookie和刷新时间保存到本地文件。
    """
    global GLOBAL_REQUEST_SESSION, COOKIE_LAST_REFRESH
    if GLOBAL_REQUEST_SESSION.cookies and COOKIE_LAST_REFRESH:
        try:
            with open(COOKIE_FILE, 'w') as f:
                json.dump({
                    "cookies": requests.utils.dict_from_cookiejar(GLOBAL_REQUEST_SESSION.cookies),
                    "last_refresh": COOKIE_LAST_REFRESH.isoformat()
                }, f)
            app.logger.info(f"全局Session的Cookie已成功保存到 {COOKIE_FILE}")
        except IOError as e:
            app.logger.error(f"保存Cookie到文件失败: {e}")
# --- Cookie处理修改结束 ---

def login_and_get_cookies(email, password):
    """
    使用提供的邮箱和密码登录 Vertical Studio AI，并获取会话Cookie。
    :param email: 登录邮箱
    :param password: 登录密码
    :return: 登录成功返回 True，否则返回 False。
    """
    global GLOBAL_REQUEST_SESSION, COOKIE_LAST_REFRESH
    # 使用全局 session 对象
    login_page_headers = BASE_HEADERS.copy()
    login_page_headers.pop("Content-Type", None)

    data_url_headers = BASE_HEADERS.copy()
    data_url_headers.pop("Content-Type", None)

    post_login_headers = BASE_HEADERS.copy()
    post_login_headers["Content-Type"] = "application/x-www-form-urlencoded;charset=UTF-8"

    app.logger.info("正在尝试登录...")
    try:
        # 使用全局 session 进行请求
        response = GLOBAL_REQUEST_SESSION.get(LOGIN_URL, headers=login_page_headers, allow_redirects=True, timeout=15)
        if response.status_code not in [200, 202, 302]:
            app.logger.error(f"访问登录页面失败，状态码: {response.status_code}")
            return False

        email_encoded = urlencode({"email": email}, encoding='utf-8').split("=")[1]
        login_password_data_url_with_email = f"{LOGIN_PASSWORD_DATA_URL}?email={email_encoded}"
        response = GLOBAL_REQUEST_SESSION.get(login_password_data_url_with_email, headers=data_url_headers, allow_redirects=True, timeout=15)
        if response.status_code not in [200, 202, 302]:
            app.logger.error(f"访问 login-password.data 失败，状态码: {response.status_code}")
            return False

        form_data = {"email": email, "password": password}
        response = GLOBAL_REQUEST_SESSION.post(login_password_data_url_with_email, data=urlencode(form_data, encoding='utf-8'), headers=post_login_headers, allow_redirects=True, timeout=15)

        # requests.Session 会自动管理 cookies，我们只需检查是否登录成功
        # 通过检查响应或CookieJar中是否存在特定的认证cookie
        auth_token_present = any("auth-token" in cookie.name.lower() for cookie in GLOBAL_REQUEST_SESSION.cookies if "verticalstudio.ai" in cookie.domain)

        if response.status_code in [200, 202, 302] and auth_token_present:
            COOKIE_LAST_REFRESH = datetime.now(timezone.utc)
            app.logger.info(f"登录成功，全局Session已更新Cookies! Cookies: {requests.utils.dict_from_cookiejar(GLOBAL_REQUEST_SESSION.cookies)}")
            save_cookies_to_file() # 保存更新后的全局 session cookies
            return True
        else:
            app.logger.error(f"登录失败或未获取到认证Cookie: 状态码 {response.status_code}, Cookies from global session: {requests.utils.dict_from_cookiejar(GLOBAL_REQUEST_SESSION.cookies)}, 响应 (url {response.url}): {response.text[:200]}...")
            return False
    except requests.exceptions.RequestException as e:
        app.logger.error(f"登录过程中发生网络错误: {e}")
        return False
    except Exception as e:
        app.logger.error(f"登录过程中发生未知错误: {e}", exc_info=True)
        return False

def check_cookie_refresh():
    """
    检查当前的Cookie是否需要刷新。
    :return: 如果需要刷新（或从未设置过），返回 True，否则返回 False。
    """
    global GLOBAL_REQUEST_SESSION, COOKIE_LAST_REFRESH # 添加 GLOBAL_REQUEST_SESSION
    # 如果全局 session 中没有 cookie，或者上次刷新时间不存在，则需要刷新
    if not GLOBAL_REQUEST_SESSION.cookies or not COOKIE_LAST_REFRESH:
        return True
    return (datetime.now(timezone.utc) - COOKIE_LAST_REFRESH).total_seconds() > COOKIE_REFRESH_INTERVAL

def schedule_cookie_refresh(email, password):
    """
    启动一个后台线程，定期检查并刷新Cookie。
    :param email: 登录邮箱
    :param password: 登录密码
    """
    def refresh_loop():
        """后台线程执行的循环任务。"""
        while True:
            # 检查频率较高（例如每小时），但仅在需要时才真正执行刷新
            # 实际的刷新间隔由 COOKIE_REFRESH_INTERVAL (12小时) 控制
            time.sleep(60 * 60) # 每小时检查一次
            # 如果全局session的cookie为空 (可能是初始状态或已清除) 或者需要刷新
            if not GLOBAL_REQUEST_SESSION.cookies or check_cookie_refresh():
                app.logger.info("全局Session的Cookie 需要刷新或丢失，尝试重新登录...")
                if not login_and_get_cookies(email, password):
                    app.logger.error("Cookie 自动刷新失败。将在下次检查时重试。")
                else:
                    app.logger.info("Cookie 自动刷新成功。")
            else:
                app.logger.debug("Cookie 仍在有效期内，无需刷新。")

    thread = Thread(target=refresh_loop, daemon=True)
    thread.start()
    app.logger.info("已启动 Cookie 定时刷新线程.")

def create_new_chat_session(corner_type=TEXT_CORNER_TYPE):
    """
    在 Vertical Studio AI 上创建一个新的临时聊天会话，并获取其 Chat ID。
    这个过程模拟了用户在网站上开始一次新的聊天的行为。
    :param corner_type: 会话类型，当前只支持 'text'。
    :return: 成功则返回新的 Chat ID 字符串，失败则返回 None。
    """
    app.logger.info(f"尝试为 '{corner_type}' 类型创建新的 VS 会话...")
    try:
        # 使用全局 session，它会自动管理 cookies
        current_headers = BASE_HEADERS.copy()
        current_headers.pop("Content-Type", None) # GET请求通常不需要Content-Type

        if not GLOBAL_REQUEST_SESSION.cookies:
            app.logger.error(f"无法创建 {corner_type} 会话：全局Session的Cookie 未初始化。")
            email, password = load_credentials()
            if not (email and password and login_and_get_cookies(email, password)):
                app.logger.error("尝试即时登录失败，无法继续创建会话。")
                return None
            if not GLOBAL_REQUEST_SESSION.cookies: # 再次检查
                app.logger.error("即时登录后全局Session仍无Cookie，无法创建会话。")
                return None
        
        # 模拟一系列用户操作来获取一个新的会话ID
        # 所有请求都通过 GLOBAL_REQUEST_SESSION 发出
        GLOBAL_REQUEST_SESSION.get(STREAM_BASE_URL, headers=current_headers, allow_redirects=True, timeout=10)
        
        headers_for_post_stream_data = current_headers.copy()
        headers_for_post_stream_data["Content-Type"] = "application/x-www-form-urlencoded;charset=UTF-8"
        dummy_prompt_val = str(uuid.uuid4())
        post_form_data = {"prompt": dummy_prompt_val, "intent": "execute-prompt"}
        stream_data_url_with_query = f"{STREAM_DATA_URL}?searchType=studio"
        GLOBAL_REQUEST_SESSION.post(stream_data_url_with_query, data=urlencode(post_form_data, encoding='utf-8'), headers=headers_for_post_stream_data, allow_redirects=True, timeout=10)
        
        # 这是获取新Chat ID的关键请求
        headers_for_get_corner_data = current_headers.copy() # GET 请求不需要 Content-Type
        specific_corner_data_url = f"{STREAM_CORNERS_BASE_URL}/{corner_type}.data?prompt={dummy_prompt_val}"
        response = GLOBAL_REQUEST_SESSION.get(specific_corner_data_url, headers=headers_for_get_corner_data, allow_redirects=False, timeout=10)
        
        new_chat_id = None
        # VS AI 通过重定向(Location头)或在响应体中返回新的会话URL，我们需要从中解析出Chat ID
        if response.status_code in [202, 301, 302, 303, 307, 308] and 'Location' in response.headers:
            location_url = response.headers['Location']
            app.logger.info(f"创建会话 ({corner_type}) 时状态码 {response.status_code}，尝试从 Location 头提取: {location_url}")
            pattern = rf'/stream/corners/{corner_type}/([\w-]+)'
            match = re.search(pattern, location_url)
            if match: new_chat_id = match.group(1)
            else: app.logger.warning(f"Location 头 '{location_url}' ({corner_type}) 不匹配期望的 Chat ID 格式。")
        elif response.status_code == 200:
            app.logger.info(f"创建会话 ({corner_type}) 时状态码 200，尝试从响应内容解析。")
            content_to_search = response.text
            # 处理可能的压缩内容
            if 'Content-Encoding' in response.headers:
                encoding = response.headers['Content-Encoding'].lower()
                if encoding == 'gzip': content_to_search = gzip.decompress(response.content).decode('utf-8', errors='ignore')
                elif encoding == 'br': content_to_search = brotli.decompress(response.content).decode('utf-8', errors='ignore')
            pattern = rf'/stream/corners/{corner_type}/([\w-]+)'
            match = re.search(pattern, content_to_search)
            if match: new_chat_id = match.group(1)
            else: app.logger.warning(f"响应内容 ({corner_type}) 中未能找到 Chat ID。内容预览: {content_to_search[:200]}")
        else:
            app.logger.warning(f"创建会话 ({corner_type}) 时收到非预期状态码: {response.status_code}。响应头: {response.headers}, 响应体预览: {response.text[:200]}")

        if new_chat_id:
            app.logger.info(f"成功从响应提取到 Chat ID ({corner_type} 类型): {new_chat_id}")
            return new_chat_id
        else:
            app.logger.error(f"未能从响应中提取 Chat ID ({corner_type} 类型)。最终状态: {response.status_code}, 最终头: {response.headers}, 最终体预览: {response.text[:200]}")
            return None
    except requests.exceptions.RequestException as e:
        app.logger.error(f"创建临时 VS Chat ID ({corner_type} 类型) 时发生网络错误: {e}")
        return None
    except Exception as e:
        app.logger.error(f"创建临时 VS Chat ID ({corner_type} 类型) 时发生未知错误: {e}", exc_info=True)
        return None

def make_request_with_retry(method, url, headers=None, json_data=None, data=None, stream=False):
    """
    发送一个HTTP请求，并带有自动重试和认证失败后自动重新登录的逻辑。
    :param method: 请求方法 (GET, POST, etc.)
    :param url: 请求URL
    :param headers: 请求头
    :param json_data: 发送的JSON数据
    :param data: 发送的表单数据
    :param stream: 是否为流式请求
    :return: 成功则返回 requests.Response 对象，所有重试失败则返回 None。
    """
    for attempt in range(MAX_RETRIES):
        app.logger.debug(f"请求 {method} {url}, 尝试 {attempt + 1}/{MAX_RETRIES}")
        try:
            time.sleep(REQUEST_INTERVAL)
            # 使用全局 session，它会自动管理 cookies
            # headers 参数仍然可以用于传递额外的、非 cookie 的头部信息
            current_headers = headers.copy() if headers else BASE_HEADERS.copy() # 确保至少有基础头

            if not GLOBAL_REQUEST_SESSION.cookies:
                 app.logger.warning(f"请求 {url} 时全局Session的Cookie为空，请求可能失败或触发重新登录!");
            
            app.logger.debug(f"通过全局Session发送请求到 {url} 使用额外头: {current_headers if headers else '无额外头，使用Session默认头'}")
            
            timeout_config = (10, 120) if stream or method.upper() == "POST" else (10,30)

            # 所有请求都通过 GLOBAL_REQUEST_SESSION 发出
            if method.upper() == "POST":
                response = GLOBAL_REQUEST_SESSION.post(url, headers=current_headers, json=json_data, data=data, stream=stream, timeout=timeout_config)
            else: # GET
                response = GLOBAL_REQUEST_SESSION.get(url, headers=current_headers, stream=stream, timeout=timeout_config)

            if response.status_code in [200, 202, 302]: return response
            # 如果是401/403，并且我们有全局session，尝试重新登录会更新全局session的cookies
            elif response.status_code in [401, 403]:
                app.logger.warning(f"认证失败 (状态码 {response.status_code}) for {url}，尝试重新登录...")
                email, password = load_credentials()
                if email and password and login_and_get_cookies(email, password):
                    app.logger.info("重新登录成功，正在重试原始请求...")
                    continue # 继续下一次循环以重试
                else: app.logger.error("重新登录失败或无凭据。"); return response
            app.logger.warning(f"请求 {url} 失败，状态码: {response.status_code}，响应: {response.text[:200] if response.content else 'No content'}...")
        except requests.exceptions.Timeout: app.logger.warning(f"请求超时: {url}...")
        except requests.exceptions.ConnectionError as e: app.logger.error(f"请求连接错误 for {url}: {e}")
        except requests.exceptions.RequestException as e: app.logger.error(f"一般请求异常 for {url}: {e}")
        
        if attempt < MAX_RETRIES - 1: time.sleep(RETRY_DELAY * (attempt + 1))
    app.logger.error(f"所有 {MAX_RETRIES} 次重试均失败: {url}")
    return None

def delete_chat_session(chat_id_to_delete):
    """
    在完成一次请求后，删除在VS AI上创建的临时聊天会话。
    :param chat_id_to_delete: 要删除的VS Chat ID。
    :return: 总是返回 True，表示尝试了删除操作。
    """
    if not chat_id_to_delete: return False
    app.logger.info(f"准备删除临时 VS Chat 会话: {chat_id_to_delete}")
    try:
        # make_request_with_retry 会使用全局 session
        headers_for_archive = BASE_HEADERS.copy() # make_request_with_retry 会处理基础头和 cookies
        if not GLOBAL_REQUEST_SESSION.cookies: # 检查全局 session 是否有 cookies
            app.logger.error("无法删除会话：全局Session的Cookie 未初始化。")
            return False
        headers_for_archive["Content-Type"] = "application/x-www-form-urlencoded;charset=UTF-8"
        headers_for_archive["Referer"] = f"{STREAM_CORNERS_BASE_URL}/{TEXT_CORNER_TYPE}/{chat_id_to_delete}"
        
        payload = {"chat": chat_id_to_delete}
        # make_request_with_retry 会使用 GLOBAL_REQUEST_SESSION
        response = make_request_with_retry("POST", ARCHIVE_CHAT_URL, headers=headers_for_archive, data=urlencode(payload, encoding='utf-8'))
        if response and response.status_code == 200: app.logger.info(f"临时 VS Chat 会话 {chat_id_to_delete} 删除成功!")
        else: app.logger.warning(f"删除临时 VS Chat 会话 {chat_id_to_delete} 失败，状态码: {response.status_code if response else '无响应'}")
    except Exception as e: app.logger.error(f"删除临时 VS Chat 会话 {chat_id_to_delete} 时发生错误: {e}", exc_info=True)
    return True

def initialize():
    """
    应用启动时的初始化函数。
    加载凭据、登录并获取Cookie、启动Cookie自动刷新线程。
    """
    email, password = load_credentials()
    if not (email and password): app.logger.critical("未能加载凭据，程序退出。"); exit(1)
    
    # load_cookies_from_file() 现在总是返回False，强制登录。
    # check_cookie_refresh() 仍然相关，以防万一Cookie通过其他方式加载（目前不可能）
    if not load_cookies_from_file() or check_cookie_refresh():
        app.logger.info("Cookie 无效、不存在或已过期。尝试登录...")
        if not login_and_get_cookies(email, password): app.logger.critical("登录失败，请检查凭据和网络。程序退出。"); exit(1)
    else:
        app.logger.info("使用已加载的 Cookie。")
    schedule_cookie_refresh(email, password)
    app.logger.info("初始化完成。")

def generate_stream_response(content_chunk, model, message_id):
    """
    为流式响应生成一个符合OpenAI格式的数据块。
    :param content_chunk: AI生成的文本内容块。
    :param model: 使用的模型名称。
    :param message_id: OpenAI格式的消息ID。
    :return: 格式化后的SSE事件字符串。
    """
    chunk_data = {"id": message_id, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
                  "choices": [{"delta": {"content": content_chunk}, "index": 0, "finish_reason": None}]}
    return f"data: {json.dumps(chunk_data)}\n\n"

def generate_stream_done(model, message_id):
    """
    为流式响应生成一个表示结束的空数据块。
    :param model: 使用的模型名称。
    :param message_id: OpenAI格式的消息ID。
    :return: 格式化后的SSE事件字符串。
    """
    done_data = {"id": message_id, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
                 "choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}]}
    return f"data: {json.dumps(done_data)}\n\n"

def build_prompt_with_history_and_instructions(messages_array):
    """
    根据OpenAI格式的消息历史记录，构建一个适用于Vertical Studio AI的单一字符串提示。
    VS AI似乎不直接支持多轮对话历史，所以我们将历史格式化为单个提示。
    :param messages_array: OpenAI格式的消息数组。
    :return: 构建好的字符串提示。
    """
    if not messages_array: return ""
    system_prompt_content = ""
    dialogue_messages = messages_array
    # 分离系统提示和对话消息
    if messages_array[0].get("role") == "system":
        system_prompt_content = messages_array[0].get("content", "").strip()
        dialogue_messages = messages_array[1:]
    
    # 对话历史格式化
    # 按照\n\nHuman: 和\n\nAssistant: 的格式
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
        else:
            app.logger.warning(f"未知角色 '{role}'，跳过该消息: {content[:50]}...")

    formatted_history = "\n".join(history_parts).strip()
    
    # 组合最终的提示
    final_prompt_elements = []
    if formatted_history: final_prompt_elements.append(formatted_history)
    final_prompt_elements.append("\n\nAssistant:") # 提示AI开始生成回复
    
    final_prompt = "\n".join(final_prompt_elements).strip()
    app.logger.debug(f"--- 构建的 VS AI 文本提示 ---\n{final_prompt[:500]}...\n--- 提示结束 ---")
    return system_prompt_content,final_prompt

@app.route('/v1/chat/completions', methods=['POST'])
def chat_completions():
    """
    处理 OpenAI 兼容的聊天补全请求的主路由。
    """
    data = request.get_json()
    client_messages = data.get("messages", [])
    model_requested = data.get("model", list(MODEL_MAPPING.keys())[0])
    stream = data.get("stream", True)
    temp_vs_chat_id = None # 用于确保在请求结束时删除临时会话

    try:
        if not client_messages: return jsonify({"error": "未提供消息"}), 400
        # 将OpenAI格式的消息历史构建为VS AI所需的单个提示
        system_prompt_content,final_prompt_for_vs_ai = build_prompt_with_history_and_instructions(client_messages)

        # 为本次请求创建一个临时的VS AI会话
        temp_vs_chat_id = create_new_chat_session(corner_type=TEXT_CORNER_TYPE)
        if not temp_vs_chat_id: return jsonify({"error": "创建文本聊天会话失败"}), 500
        
        # 生成符合OpenAI和VS AI格式的消息ID
        openai_msg_id = f"chatcmpl-{uuid.uuid4().hex}"
        vs_msg_id = str(uuid.uuid4()).replace("-", "")[:16]
        # 调整 createdAt 格式为以 'Z' 结尾
        created_at = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        
        # 记录会话ID和消息ID的映射关系
        CHAT_IDS.setdefault(temp_vs_chat_id, []).append({"type": "text", "openai_id": openai_msg_id, "vs_id": vs_msg_id, "timestamp": created_at})
        SESSIONS[temp_vs_chat_id] = {"prompt_sent_preview": final_prompt_for_vs_ai[:100], "model_requested": model_requested}

        # 将客户端请求的模型名称映射到VS AI的实际模型ID
        vs_text_model_id = MODEL_MAPPING.get(model_requested, list(MODEL_MAPPING.values())[0])

        # 构建发送给VS AI聊天API的载荷
        target_payload = {
            "message": {"id": vs_msg_id, "createdAt": created_at, "role": "user", "content": final_prompt_for_vs_ai, "parts": [{"type": "text", "text": final_prompt_for_vs_ai}]},
            "cornerType": TEXT_CORNER_TYPE, "chatId": temp_vs_chat_id,
            "settings": {
                "modelId": vs_text_model_id,  # 修正字段名称： textModelId -> modelId
                "customSystemPrompt": system_prompt_content
                # "toneOfVoice": "default",
                # "systemPromptPreset": "default"  # 新增字段
            }
        }

        # reasoning 字段的添加逻辑保持不变，基于 vs_text_model_id
        if vs_text_model_id == "claude-4-sonnet-20250514" or MODEL_MAPPING.get(model_requested) == "claude-4-sonnet-20250514": # 确保使用正确的模型ID判断
            target_payload["settings"]["reasoning"] = "on"

        headers_for_chat = BASE_HEADERS.copy()
        headers_for_chat["Content-Type"] = "application/json"
        # 模拟浏览器行为，设置Referer头
        headers_for_chat["Referer"] = f"{STREAM_CORNERS_BASE_URL}/{TEXT_CORNER_TYPE}/{temp_vs_chat_id}"
        
        # 发送请求到VS AI，始终请求流式响应以便统一处理
        response_from_vs_ai = make_request_with_retry("POST", CHAT_API_URL, headers=headers_for_chat, json_data=target_payload, stream=True)
        if response_from_vs_ai is None or response_from_vs_ai.status_code != 200:
            err_text = response_from_vs_ai.text[:200] if response_from_vs_ai and response_from_vs_ai.content else "No response object or content"
            err_status = response_from_vs_ai.status_code if response_from_vs_ai else "N/A"
            return jsonify({"error": f"VS AI API 文本聊天请求失败，状态: {err_status}, 响应预览: {err_text}"}), 500

        # 根据客户端是否要求流式响应，返回不同格式的数据
        if stream:
            return Response(_handle_stream_response(response_from_vs_ai, model_requested, openai_msg_id), mimetype="text/event-stream")
        else:
            ai_reply_full = _handle_non_stream_response(response_from_vs_ai)
            openai_response = {"id": openai_msg_id, "object": "chat.completion", "created": int(time.time()), "model": model_requested,
                               "choices": [{"message": {"role": "assistant", "content": ai_reply_full}, "index": 0, "finish_reason": "stop"}],
                               "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}} # usage是哑值
            return jsonify(openai_response)
    except Exception as e:
        app.logger.error(f"处理 /v1/chat/completions 时发生异常: {e}", exc_info=True)
        return jsonify({"error": f"内部服务器错误: {str(e)}"}), 500
    finally:
        # 确保无论成功失败，都尝试删除此次请求创建的临时会话
        if temp_vs_chat_id: delete_chat_session(temp_vs_chat_id)

def _parse_vs_ai_stream(response_from_vs_ai):
    """
    一个生成器，用于解析来自 Vertical Studio AI 的服务器发送事件(SSE)流。
    该函数迭代响应行，解码它们，并从以 "0:" 开头的行中提取内容块。
    它会 yield 每个内容块，直到遇到流结束标记 ("e:" 或 "d:")。

    :param response_from_vs_ai: 来自 requests 库的响应对象，其内容是SSE流。
    :return: 一个生成器，逐个 yield 内容字符串块。
    """
    for line in response_from_vs_ai.iter_lines():
        if not line:
            continue
        decoded_line = line.decode('utf-8')
        # 调试日志记录原始SSE行
        app.logger.debug(f"VS AI Raw SSE: {decoded_line}")
        if decoded_line.startswith("0:"):
            try:
                # 提取并解码JSON编码的内容块
                content_chunk = json.loads(decoded_line[2:])
                yield content_chunk
            except json.JSONDecodeError:
                app.logger.warning(f"无法将SSE数据块解析为JSON: {decoded_line}")
        elif decoded_line.startswith(("e:", "d:")):
            # 流结束标记
            app.logger.info(f"VS AI SSE stream finished.")
            # 结束生成器
            return

def _handle_stream_response(response_from_vs_ai, model_name, openai_msg_id):
    """
    使用 _parse_vs_ai_stream 生成器处理流式响应，并将其转换为OpenAI格式的SSE事件流。
    :param response_from_vs_ai: VS AI的响应对象。
    :param model_name: 模型名称。
    :param openai_msg_id: OpenAI格式的消息ID。
    :return: 一个生成器，逐块产生符合OpenAI格式的SSE事件。
    """
    # 迭代解析器生成的每个内容块
    for content_chunk in _parse_vs_ai_stream(response_from_vs_ai):
        yield generate_stream_response(content_chunk, model_name, openai_msg_id)
    
    # 在流结束后，发送最后的完成事件
    app.logger.info(f"Text Gen SSE stream finished for {openai_msg_id}")
    yield generate_stream_done(model_name, openai_msg_id)
    yield "data: [DONE]\n\n"

def _handle_non_stream_response(response_from_vs_ai):
    """
    使用 _parse_vs_ai_stream 生成器处理流式响应，并将其高效地聚合成单个字符串。
    :param response_from_vs_ai: VS AI的响应对象。
    :return: 完整的AI回复字符串。
    """
    # 使用 "".join() 和生成器来高效地聚合所有内容块
    return "".join(_parse_vs_ai_stream(response_from_vs_ai))


@app.route('/v1/chat/new', methods=['GET'])
def new_chat_test_endpoint():
    """
    一个用于测试创建和删除会话流程的调试端点。
    """
    results = {}
    text_chat_id = create_new_chat_session(corner_type=TEXT_CORNER_TYPE)
    if text_chat_id:
        results["text_session_test"] = f"Created and subsequently deleted {text_chat_id}"
        delete_chat_session(text_chat_id)
    else: results["text_session_test"] = "Failed to create text session"
    return jsonify(results)

def get_all_models_info():
    """
    构建符合OpenAI格式的可用模型列表。
    :return: 包含所有文本和图像模型信息的列表。
    """
    models = [{"id": k, "object": "model", "owned_by": "vsp-text", "permission": []} for k in MODEL_MAPPING.keys()]
    return models

@app.route('/v1/models', methods=['GET'])
def get_models_endpoint():
    """
    OpenAI 兼容的获取模型列表的路由。
    """
    return jsonify({"data": get_all_models_info(), "object": "list"})

def cleanup():
    """
    程序退出时执行的清理函数。
    """
    app.logger.info("程序正在退出...")
    save_cookies_to_file()
    app.logger.info("程序退出。")

def print_then_install(package_name, install_name):
    """
    检查到依赖未安装时，打印错误信息并退出程序。
    """
    app.logger.critical(f"{package_name} 未安装。请运行: pip install {install_name}")
    exit(1)

if __name__ == '__main__':
    # 注册退出处理函数
    atexit.register(cleanup)
    try: import brotli
    except ImportError: print_then_install("Brotli", "brotli")
    
    # 执行应用初始化
    initialize()
    
    # 从环境变量获取端口，默认为7860
    app_port = int(os.environ.get("PORT", 7860))
    # 启动Flask应用
    app.run(host='0.0.0.0', port=app_port, debug=False)

