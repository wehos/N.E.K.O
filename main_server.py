# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mimetypes
mimetypes.add_type("application/javascript", ".js")
import asyncio
import json
import uuid
import logging
from datetime import datetime
import webbrowser
import io

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, File, UploadFile, Form, Body
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from main_helper import core as core, cross_server as cross_server
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse
from utils.preferences import load_user_preferences, update_model_preferences, validate_model_preferences, move_model_to_top
from utils.frontend_utils import find_models, find_model_config_file, find_model_directory
from multiprocessing import Process, Queue, Event
import atexit
import dashscope
from dashscope.audio.tts_v2 import VoiceEnrollmentService
import requests
import httpx
import pathlib, wave
from openai import AsyncOpenAI
from config import MAIN_SERVER_PORT, MONITOR_SERVER_PORT, MEMORY_SERVER_PORT, MODELS_WITH_EXTRA_BODY, TOOL_SERVER_PORT
from config.prompts_sys import emotion_analysis_prompt, proactive_chat_prompt
import glob
from utils.config_manager import get_config_manager

# 确定 templates 目录位置（支持 PyInstaller 打包）
if getattr(sys, 'frozen', False):
    # 打包后运行：从 _MEIPASS 读取
    template_dir = sys._MEIPASS
else:
    # 正常运行：当前目录
    template_dir = "./"

templates = Jinja2Templates(directory=template_dir)

# Configure logging
from utils.logger_config import setup_logging

logger, log_config = setup_logging(service_name="Main", log_level=logging.INFO)

_config_manager = get_config_manager()

def cleanup():
    logger.info("Starting cleanup process")
    for k in sync_message_queue:
        while sync_message_queue[k] and not sync_message_queue[k].empty():
            sync_message_queue[k].get_nowait()
        sync_message_queue[k].close()
        sync_message_queue[k].join_thread()
    logger.info("Cleanup completed")
atexit.register(cleanup)
sync_message_queue = {}
sync_shutdown_event = {}
session_manager = {}
session_id = {}
sync_process = {}
# 每个角色的websocket操作锁，用于防止preserve/restore与cleanup()之间的竞争
websocket_locks = {}
# Global variables for character data (will be updated on reload)
master_name = None
her_name = None
master_basic_config = None
lanlan_basic_config = None
name_mapping = None
lanlan_prompt = None
semantic_store = None
time_store = None
setting_store = None
recent_log = None
catgirl_names = []

async def initialize_character_data():
    """初始化或重新加载角色配置数据"""
    global master_name, her_name, master_basic_config, lanlan_basic_config
    global name_mapping, lanlan_prompt, semantic_store, time_store, setting_store, recent_log
    global catgirl_names, sync_message_queue, sync_shutdown_event, session_manager, session_id, sync_process, websocket_locks
    
    logger.info("正在加载角色配置...")
    
    # 清理无效的voice_id引用
    _config_manager.cleanup_invalid_voice_ids()
    
    # 加载最新的角色数据
    master_name, her_name, master_basic_config, lanlan_basic_config, name_mapping, lanlan_prompt, semantic_store, time_store, setting_store, recent_log = _config_manager.get_character_data()
    catgirl_names = list(lanlan_prompt.keys())
    
    # 为新增的角色初始化资源
    for k in catgirl_names:
        if k not in sync_message_queue:
            sync_message_queue[k] = Queue()
            sync_shutdown_event[k] = Event()
            session_id[k] = None
            sync_process[k] = None
            logger.info(f"为角色 {k} 初始化新资源")
        
        # 确保该角色有websocket锁
        if k not in websocket_locks:
            websocket_locks[k] = asyncio.Lock()
        
        # 更新或创建session manager（使用最新的prompt）
        # 使用锁保护websocket的preserve/restore操作，防止与cleanup()竞争
        async with websocket_locks[k]:
            # 如果已存在且已有websocket连接，保留websocket引用
            old_websocket = None
            if k in session_manager and session_manager[k].websocket:
                old_websocket = session_manager[k].websocket
                logger.info(f"保留 {k} 的现有WebSocket连接")
            
            session_manager[k] = core.LLMSessionManager(
                sync_message_queue[k],
                k,
                lanlan_prompt[k].replace('{LANLAN_NAME}', k).replace('{MASTER_NAME}', master_name)
            )
            
            # 将websocket锁存储到session manager中，供cleanup()使用
            session_manager[k].websocket_lock = websocket_locks[k]
            
            # 恢复websocket引用（如果存在）
            if old_websocket:
                session_manager[k].websocket = old_websocket
                logger.info(f"已恢复 {k} 的WebSocket连接")
    
    # 清理已删除角色的资源
    removed_names = [k for k in session_manager.keys() if k not in catgirl_names]
    for k in removed_names:
        logger.info(f"清理已删除角色 {k} 的资源")
        # 清理队列
        if k in sync_message_queue:
            try:
                while not sync_message_queue[k].empty():
                    sync_message_queue[k].get_nowait()
                sync_message_queue[k].close()
                sync_message_queue[k].join_thread()
            except:
                pass
            del sync_message_queue[k]
        
        # 清理其他资源
        if k in sync_shutdown_event:
            del sync_shutdown_event[k]
        if k in session_manager:
            del session_manager[k]
        if k in session_id:
            del session_id[k]
        if k in sync_process:
            del sync_process[k]
    
    logger.info(f"角色配置加载完成，当前角色: {catgirl_names}，主人: {master_name}")

# 初始化角色数据（使用asyncio.run在模块级别执行async函数）
import asyncio as _init_asyncio
try:
    _init_asyncio.get_event_loop()
except RuntimeError:
    _init_asyncio.set_event_loop(_init_asyncio.new_event_loop())
_init_asyncio.get_event_loop().run_until_complete(initialize_character_data())
lock = asyncio.Lock()

# --- FastAPI App Setup ---
app = FastAPI()

# 配置 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有源（生产环境建议配置具体域名）
    allow_credentials=True,  # 允许携带凭证（cookies, authorization headers等）
    allow_methods=["*"],  # 允许所有HTTP方法
    allow_headers=["*"],  # 允许所有请求头
)

class CustomStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if path.endswith('.js'):
            response.headers['Content-Type'] = 'application/javascript'
        return response

# 确定 static 目录位置（支持 PyInstaller 打包）
if getattr(sys, 'frozen', False):
    # 打包后运行：从 _MEIPASS 读取（onedir 模式下是 _internal）
    static_dir = os.path.join(sys._MEIPASS, 'static')
else:
    # 正常运行：当前目录
    static_dir = 'static'

app.mount("/static", CustomStaticFiles(directory=static_dir), name="static")

# 挂载用户文档下的live2d目录
_config_manager.ensure_live2d_directory()
user_live2d_path = str(_config_manager.live2d_dir)
if os.path.exists(user_live2d_path):
    app.mount("/user_live2d", CustomStaticFiles(directory=user_live2d_path), name="user_live2d")
    logger.info(f"已挂载用户Live2D目录: {user_live2d_path}")

# 使用 FastAPI 的 app.state 来管理启动配置
def get_start_config():
    """从 app.state 获取启动配置"""
    if hasattr(app.state, 'start_config'):
        return app.state.start_config
    return {
        "browser_mode_enabled": False,
        "browser_page": "chara_manager",
        'server': None
    }

def set_start_config(config):
    """设置启动配置到 app.state"""
    app.state.start_config = config

@app.get("/", response_class=HTMLResponse)
async def get_default_index(request: Request):
    return templates.TemplateResponse("templates/index.html", {
        "request": request
    })


@app.get("/api/preferences")
async def get_preferences():
    """获取用户偏好设置"""
    preferences = load_user_preferences()
    return preferences

@app.post("/api/preferences")
async def save_preferences(request: Request):
    """保存用户偏好设置"""
    try:
        data = await request.json()
        if not data:
            return {"success": False, "error": "无效的数据"}
        
        # 验证偏好数据
        if not validate_model_preferences(data):
            return {"success": False, "error": "偏好数据格式无效"}
        
        # 更新偏好
        if update_model_preferences(data['model_path'], data['position'], data['scale']):
            return {"success": True, "message": "偏好设置已保存"}
        else:
            return {"success": False, "error": "保存失败"}
            
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/live2d/models")
async def get_live2d_models(simple: bool = False):
    """
    获取Live2D模型列表
    Args:
        simple: 如果为True，只返回模型名称列表；如果为False，返回完整的模型信息
    """
    try:
        models = find_models()
        
        if simple:
            # 只返回模型名称列表
            model_names = [model["name"] for model in models]
            return {"success": True, "models": model_names}
        else:
            # 返回完整的模型信息（保持向后兼容）
            return models
    except Exception as e:
        logger.error(f"获取Live2D模型列表失败: {e}")
        if simple:
            return {"success": False, "error": str(e)}
        else:
            return []

@app.get("/api/models")
async def get_models_legacy():
    """
    向后兼容的API端点，重定向到新的 /api/live2d/models
    """
    return await get_live2d_models(simple=False)

@app.post("/api/preferences/set-preferred")
async def set_preferred_model(request: Request):
    """设置首选模型"""
    try:
        data = await request.json()
        if not data or 'model_path' not in data:
            return {"success": False, "error": "无效的数据"}
        
        if move_model_to_top(data['model_path']):
            return {"success": True, "message": "首选模型已更新"}
        else:
            return {"success": False, "error": "模型不存在或更新失败"}
            
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/config/page_config")
async def get_page_config(lanlan_name: str = ""):
    """获取页面配置（lanlan_name 和 model_path）"""
    try:
        # 获取角色数据
        _, her_name, _, lanlan_basic_config, _, _, _, _, _, _ = _config_manager.get_character_data()
        
        # 如果提供了 lanlan_name 参数，使用它；否则使用当前角色
        target_name = lanlan_name if lanlan_name else her_name
        
        # 获取 live2d 字段
        live2d = lanlan_basic_config.get(target_name, {}).get('live2d', 'mao_pro')
        
        # 查找所有模型
        models = find_models()
        
        # 根据 live2d 字段查找对应的 model path
        model_path = next((m["path"] for m in models if m["name"] == live2d), find_model_config_file(live2d))
        
        return {
            "success": True,
            "lanlan_name": target_name,
            "model_path": model_path
        }
    except Exception as e:
        logger.error(f"获取页面配置失败: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "lanlan_name": "",
            "model_path": ""
        }

@app.get("/api/config/core_api")
async def get_core_config_api():
    """获取核心配置（API Key）"""
    try:
        # 尝试从core_config.json读取
        try:
            from utils.config_manager import get_config_manager
            config_manager = get_config_manager()
            core_config_path = str(config_manager.get_config_path('core_config.json'))
            with open(core_config_path, 'r', encoding='utf-8') as f:
                core_cfg = json.load(f)
                api_key = core_cfg.get('coreApiKey', '')
        except FileNotFoundError:
            # 如果文件不存在，返回当前配置中的CORE_API_KEY
            core_config = _config_manager.get_core_config()
            api_key = core_config['CORE_API_KEY']
        
        return {
            "api_key": api_key,
            "coreApi": core_cfg.get('coreApi', 'qwen'),
            "assistApi": core_cfg.get('assistApi', 'qwen'),
            "assistApiKeyQwen": core_cfg.get('assistApiKeyQwen', ''),
            "assistApiKeyOpenai": core_cfg.get('assistApiKeyOpenai', ''),
            "assistApiKeyGlm": core_cfg.get('assistApiKeyGlm', ''),
            "assistApiKeyStep": core_cfg.get('assistApiKeyStep', ''),
            "assistApiKeySilicon": core_cfg.get('assistApiKeySilicon', ''),
            "mcpToken": core_cfg.get('mcpToken', ''),  # 添加mcpToken字段
            "enableCustomApi": core_cfg.get('enableCustomApi', False),  # 添加enableCustomApi字段
            "success": True
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@app.post("/api/config/core_api")
async def update_core_config(request: Request):
    """更新核心配置（API Key）"""
    try:
        data = await request.json()
        if not data:
            return {"success": False, "error": "无效的数据"}
        
        # 检查是否启用了自定义API
        enable_custom_api = data.get('enableCustomApi', False)
        
        # 如果启用了自定义API，不需要强制检查核心API key
        if not enable_custom_api:
            # 检查是否为免费版配置
            is_free_version = data.get('coreApi') == 'free' or data.get('assistApi') == 'free'
            
            if 'coreApiKey' not in data:
                return {"success": False, "error": "缺少coreApiKey字段"}
            
            api_key = data['coreApiKey']
            if api_key is None:
                return {"success": False, "error": "API Key不能为null"}
            
            if not isinstance(api_key, str):
                return {"success": False, "error": "API Key必须是字符串类型"}
            
            api_key = api_key.strip()
            
            # 免费版允许使用 'free-access' 作为API key，不进行空值检查
            if not is_free_version and not api_key:
                return {"success": False, "error": "API Key不能为空"}
        
        # 保存到core_config.json
        from pathlib import Path
        from utils.config_manager import get_config_manager
        config_manager = get_config_manager()
        core_config_path = str(config_manager.get_config_path('core_config.json'))
        # 确保配置目录存在
        Path(core_config_path).parent.mkdir(parents=True, exist_ok=True)
        
        # 构建配置对象
        core_cfg = {}
        
        # 只有在启用自定义API时，才允许不设置coreApiKey
        if enable_custom_api:
            # 启用自定义API时，coreApiKey是可选的
            if 'coreApiKey' in data:
                api_key = data['coreApiKey']
                if api_key is not None and isinstance(api_key, str):
                    core_cfg['coreApiKey'] = api_key.strip()
        else:
            # 未启用自定义API时，必须设置coreApiKey
            api_key = data.get('coreApiKey', '')
            if api_key is not None and isinstance(api_key, str):
                core_cfg['coreApiKey'] = api_key.strip()
        if 'coreApi' in data:
            core_cfg['coreApi'] = data['coreApi']
        if 'assistApi' in data:
            core_cfg['assistApi'] = data['assistApi']
        if 'assistApiKeyQwen' in data:
            core_cfg['assistApiKeyQwen'] = data['assistApiKeyQwen']
        if 'assistApiKeyOpenai' in data:
            core_cfg['assistApiKeyOpenai'] = data['assistApiKeyOpenai']
        if 'assistApiKeyGlm' in data:
            core_cfg['assistApiKeyGlm'] = data['assistApiKeyGlm']
        if 'assistApiKeyStep' in data:
            core_cfg['assistApiKeyStep'] = data['assistApiKeyStep']
        if 'assistApiKeySilicon' in data:
            core_cfg['assistApiKeySilicon'] = data['assistApiKeySilicon']
        if 'mcpToken' in data:
            core_cfg['mcpToken'] = data['mcpToken']
        if 'enableCustomApi' in data:
            core_cfg['enableCustomApi'] = data['enableCustomApi']
        
        # 添加用户自定义API配置
        if 'summaryModelProvider' in data:
            core_cfg['summaryModelProvider'] = data['summaryModelProvider']
        if 'summaryModelUrl' in data:
            core_cfg['summaryModelUrl'] = data['summaryModelUrl']
        if 'summaryModelApiKey' in data:
            core_cfg['summaryModelApiKey'] = data['summaryModelApiKey']
        if 'correctionModelProvider' in data:
            core_cfg['correctionModelProvider'] = data['correctionModelProvider']
        if 'correctionModelUrl' in data:
            core_cfg['correctionModelUrl'] = data['correctionModelUrl']
        if 'correctionModelApiKey' in data:
            core_cfg['correctionModelApiKey'] = data['correctionModelApiKey']
        if 'emotionModelProvider' in data:
            core_cfg['emotionModelProvider'] = data['emotionModelProvider']
        if 'emotionModelUrl' in data:
            core_cfg['emotionModelUrl'] = data['emotionModelUrl']
        if 'emotionModelApiKey' in data:
            core_cfg['emotionModelApiKey'] = data['emotionModelApiKey']
        if 'visionModelProvider' in data:
            core_cfg['visionModelProvider'] = data['visionModelProvider']
        if 'visionModelUrl' in data:
            core_cfg['visionModelUrl'] = data['visionModelUrl']
        if 'visionModelApiKey' in data:
            core_cfg['visionModelApiKey'] = data['visionModelApiKey']
        if 'omniModelProvider' in data:
            core_cfg['omniModelProvider'] = data['omniModelProvider']
        if 'omniModelUrl' in data:
            core_cfg['omniModelUrl'] = data['omniModelUrl']
        if 'omniModelApiKey' in data:
            core_cfg['omniModelApiKey'] = data['omniModelApiKey']
        if 'ttsModelProvider' in data:
            core_cfg['ttsModelProvider'] = data['ttsModelProvider']
        if 'ttsModelUrl' in data:
            core_cfg['ttsModelUrl'] = data['ttsModelUrl']
        if 'ttsModelApiKey' in data:
            core_cfg['ttsModelApiKey'] = data['ttsModelApiKey']
        
        with open(core_config_path, 'w', encoding='utf-8') as f:
            json.dump(core_cfg, f, indent=2, ensure_ascii=False)
        
        # API配置更新后，需要先通知所有客户端，再关闭session，最后重新加载配置
        logger.info("API配置已更新，准备通知客户端并重置所有session...")
        
        # 1. 先通知所有连接的客户端即将刷新（WebSocket还连着）
        notification_count = 0
        for lanlan_name, mgr in session_manager.items():
            if mgr.is_active and mgr.websocket:
                try:
                    await mgr.websocket.send_text(json.dumps({
                        "type": "reload_page",
                        "message": "API配置已更新，页面即将刷新"
                    }))
                    notification_count += 1
                    logger.info(f"已通知 {lanlan_name} 的前端刷新页面")
                except Exception as e:
                    logger.warning(f"通知 {lanlan_name} 的WebSocket失败: {e}")
        
        logger.info(f"已通知 {notification_count} 个客户端")
        
        # 2. 立刻关闭所有活跃的session（这会断开所有WebSocket）
        sessions_ended = []
        for lanlan_name, mgr in session_manager.items():
            if mgr.is_active:
                try:
                    await mgr.end_session(by_server=True)
                    sessions_ended.append(lanlan_name)
                    logger.info(f"{lanlan_name} 的session已结束")
                except Exception as e:
                    logger.error(f"结束 {lanlan_name} 的session时出错: {e}")
        
        # 3. 重新加载配置并重建session manager
        logger.info("正在重新加载配置...")
        try:
            await initialize_character_data()
            logger.info("配置重新加载完成，新的API配置已生效")
        except Exception as reload_error:
            logger.error(f"重新加载配置失败: {reload_error}")
            return {"success": False, "error": f"配置已保存但重新加载失败: {str(reload_error)}"}
        
        logger.info(f"已通知 {notification_count} 个连接的客户端API配置已更新")
        return {"success": True, "message": "API Key已保存并重新加载配置", "sessions_ended": len(sessions_ended)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.on_event("startup")
async def startup_event():
    global sync_process
    logger.info("Starting sync connector processes")
    # 启动同步连接器进程
    for k in sync_process:
        if sync_process[k] is None:
            sync_process[k] = Process(
                target=cross_server.sync_connector_process,
                args=(sync_message_queue[k], sync_shutdown_event[k], k, f"ws://localhost:{MONITOR_SERVER_PORT}", {'bullet': False, 'monitor': True})
            )
            sync_process[k].start()
            logger.info(f"同步连接器进程已启动 (PID: {sync_process[k].pid})")
    
    # 如果启用了浏览器模式，在服务器启动完成后打开浏览器
    current_config = get_start_config()
    print(f"启动配置: {current_config}")
    if current_config['browser_mode_enabled']:
        import threading
        
        def launch_browser_delayed():
            # 等待一小段时间确保服务器完全启动
            import time
            time.sleep(1)
            # 从 app.state 获取配置
            config = get_start_config()
            url = f"http://127.0.0.1:{MAIN_SERVER_PORT}/{config['browser_page']}"
            try:
                webbrowser.open(url)
                logger.info(f"服务器启动完成，已打开浏览器访问: {url}")
            except Exception as e:
                logger.error(f"打开浏览器失败: {e}")
        
        # 在独立线程中启动浏览器
        t = threading.Thread(target=launch_browser_delayed, daemon=True)
        t.start()


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时执行"""
    logger.info("Shutting down sync connector processes")
    # 关闭同步服务器连接
    for k in sync_process:
        if sync_process[k] is not None:
            sync_shutdown_event[k].set()
            sync_process[k].join(timeout=3)  # 等待进程正常结束
            if sync_process[k].is_alive():
                sync_process[k].terminate()  # 如果超时，强制终止
    logger.info("同步连接器进程已停止")
    
    # 向memory_server发送关闭信号
    try:
        import requests
        from config import MEMORY_SERVER_PORT
        shutdown_url = f"http://localhost:{MEMORY_SERVER_PORT}/shutdown"
        response = requests.post(shutdown_url, timeout=2)
        if response.status_code == 200:
            logger.info("已向memory_server发送关闭信号")
        else:
            logger.warning(f"向memory_server发送关闭信号失败，状态码: {response.status_code}")
    except Exception as e:
        logger.warning(f"向memory_server发送关闭信号时出错: {e}")


@app.websocket("/ws/{lanlan_name}")
async def websocket_endpoint(websocket: WebSocket, lanlan_name: str):
    await websocket.accept()
    this_session_id = uuid.uuid4()
    async with lock:
        global session_id
        session_id[lanlan_name] = this_session_id
    logger.info(f"⭐websocketWebSocket accepted: {websocket.client}, new session id: {session_id[lanlan_name]}, lanlan_name: {lanlan_name}")
    
    # 立即设置websocket到session manager，以支持主动搭话
    # 注意：这里设置后，即使cleanup()被调用，websocket也会在start_session时重新设置
    if lanlan_name in session_manager:
        session_manager[lanlan_name].websocket = websocket
        logger.info(f"✅ 已设置 {lanlan_name} 的WebSocket连接")
    else:
        logger.error(f"❌ 错误：{lanlan_name} 不在session_manager中！当前session_manager: {list(session_manager.keys())}")

    try:
        while True:
            data = await websocket.receive_text()
            if session_id[lanlan_name] != this_session_id:
                await session_manager[lanlan_name].send_status(f"切换至另一个终端...")
                await websocket.close()
                break
            message = json.loads(data)
            action = message.get("action")
            # logger.debug(f"WebSocket received action: {action}") # Optional debug log

            if action == "start_session":
                session_manager[lanlan_name].active_session_is_idle = False
                input_type = message.get("input_type", "audio")
                if input_type in ['audio', 'screen', 'camera', 'text']:
                    # 传递input_mode参数，告知session manager使用何种模式
                    mode = 'text' if input_type == 'text' else 'audio'
                    asyncio.create_task(session_manager[lanlan_name].start_session(websocket, message.get("new_session", False), mode))
                else:
                    await session_manager[lanlan_name].send_status(f"Invalid input type: {input_type}")

            elif action == "stream_data":
                asyncio.create_task(session_manager[lanlan_name].stream_data(message))

            elif action == "end_session":
                session_manager[lanlan_name].active_session_is_idle = False
                asyncio.create_task(session_manager[lanlan_name].end_session())

            elif action == "pause_session":
                session_manager[lanlan_name].active_session_is_idle = True
                asyncio.create_task(session_manager[lanlan_name].end_session())

            elif action == "ping":
                # 心跳保活消息，回复pong
                await websocket.send_text(json.dumps({"type": "pong"}))
                # logger.debug(f"收到心跳ping，已回复pong")

            else:
                logger.warning(f"Unknown action received: {action}")
                await session_manager[lanlan_name].send_status(f"Unknown action: {action}")

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {websocket.client}")
    except Exception as e:
        error_message = f"WebSocket handler error: {e}"
        logger.error(f"💥 {error_message}")
        try:
            await session_manager[lanlan_name].send_status(f"Server error: {e}")
        except:
            pass
    finally:
        logger.info(f"Cleaning up WebSocket resources: {websocket.client}")
        await session_manager[lanlan_name].cleanup()
        # 注意：cleanup() 会清空 websocket，但只在连接真正断开时调用
        # 如果连接还在，websocket应该保持设置
        if session_manager[lanlan_name].websocket == websocket:
            session_manager[lanlan_name].websocket = None

@app.post('/api/notify_task_result')
async def notify_task_result(request: Request):
    """供工具/任务服务回调：在下一次正常回复之后，插入一条任务完成提示。"""
    try:
        data = await request.json()
        # 如果未显式提供，则使用当前默认角色
        _, her_name_current, _, _, _, _, _, _, _, _ = _config_manager.get_character_data()
        lanlan = data.get('lanlan_name') or her_name_current
        text = (data.get('text') or '').strip()
        if not text:
            return JSONResponse({"success": False, "error": "text required"}, status_code=400)
        mgr = session_manager.get(lanlan)
        if not mgr:
            return JSONResponse({"success": False, "error": "lanlan not found"}, status_code=404)
        # 将提示加入待插入队列
        mgr.pending_extra_replies.append(text)
        return {"success": True}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@app.post('/api/proactive_chat')
async def proactive_chat(request: Request):
    """主动搭话：爬取热门内容，让AI决定是否主动发起对话"""
    try:
        from utils.web_scraper import fetch_trending_content, format_trending_content
        
        # 获取当前角色数据
        master_name_current, her_name_current, _, _, _, _, _, _, _, _ = _config_manager.get_character_data()
        
        data = await request.json()
        lanlan_name = data.get('lanlan_name') or her_name_current
        
        # 获取session manager
        mgr = session_manager.get(lanlan_name)
        if not mgr:
            return JSONResponse({"success": False, "error": f"角色 {lanlan_name} 不存在"}, status_code=404)
        
        # 检查是否正在响应中（如果正在说话，不打断）
        if mgr.is_active and hasattr(mgr.session, '_is_responding') and mgr.session._is_responding:
            return JSONResponse({
                "success": False, 
                "error": "AI正在响应中，无法主动搭话",
                "message": "请等待当前响应完成"
            }, status_code=409)
        
        logger.info(f"[{lanlan_name}] 开始主动搭话流程...")
        
        # 1. 爬取热门内容
        try:
            trending_content = await fetch_trending_content(bilibili_limit=10, weibo_limit=10)
            
            if not trending_content['success']:
                return JSONResponse({
                    "success": False,
                    "error": "无法获取热门内容",
                    "detail": trending_content.get('error', '未知错误')
                }, status_code=500)
            
            formatted_content = format_trending_content(trending_content)
            logger.info(f"[{lanlan_name}] 成功获取热门内容")
            
        except Exception as e:
            logger.error(f"[{lanlan_name}] 获取热门内容失败: {e}")
            return JSONResponse({
                "success": False,
                "error": "爬取热门内容时出错",
                "detail": str(e)
            }, status_code=500)
        
        # 2. 获取new_dialogue prompt
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"http://localhost:{MEMORY_SERVER_PORT}/new_dialog/{lanlan_name}", timeout=5.0)
                memory_context = resp.text
        except Exception as e:
            logger.warning(f"[{lanlan_name}] 获取记忆上下文失败，使用空上下文: {e}")
            memory_context = ""
        
        # 3. 构造提示词（使用prompts_sys中的模板）
        system_prompt = proactive_chat_prompt.format(
            lanlan_name=lanlan_name,
            master_name=master_name_current,
            trending_content=formatted_content,
            memory_context=memory_context
        )

        # 4. 直接使用langchain ChatOpenAI获取AI回复（不创建临时session）
        try:
            core_config = _config_manager.get_core_config()
            
            # 直接使用langchain ChatOpenAI发送请求
            from langchain_openai import ChatOpenAI
            from langchain_core.messages import SystemMessage
            
            llm = ChatOpenAI(
                model=core_config['CORRECTION_MODEL'],
                base_url=core_config['OPENROUTER_URL'],
                api_key=core_config['OPENROUTER_API_KEY'],
                temperature=1.1,
                streaming=False  # 不需要流式，直接获取完整响应
            )
            
            # 发送请求获取AI决策
            print(system_prompt)
            response = await asyncio.wait_for(
                llm.ainvoke([SystemMessage(content=system_prompt)]),
                timeout=10.0
            )
            response_text = response.content.strip()
            
            logger.info(f"[{lanlan_name}] AI决策结果: {response_text[:100]}...")
            
            # 5. 判断AI是否选择搭话
            if "[PASS]" in response_text or not response_text:
                return JSONResponse({
                    "success": True,
                    "action": "pass",
                    "message": "AI选择暂时不搭话"
                })
            
            # 6. AI选择搭话，需要通过session manager处理
            # 首先检查是否有真实的websocket连接
            if not mgr.websocket:
                return JSONResponse({
                    "success": False,
                    "error": "没有活跃的WebSocket连接，无法主动搭话。请先打开前端页面。"
                }, status_code=400)
            
            # 检查websocket是否连接
            try:
                from starlette.websockets import WebSocketState
                if hasattr(mgr.websocket, 'client_state'):
                    if mgr.websocket.client_state != WebSocketState.CONNECTED:
                        return JSONResponse({
                            "success": False,
                            "error": "WebSocket未连接，无法主动搭话"
                        }, status_code=400)
            except Exception as e:
                logger.warning(f"检查WebSocket状态失败: {e}")
            
            # 检查是否有现有的session，如果没有则创建一个文本session
            session_created = False
            if not mgr.session or not hasattr(mgr.session, '_conversation_history'):
                logger.info(f"[{lanlan_name}] 没有活跃session，创建文本session用于主动搭话")
                # 使用现有的真实websocket启动session
                await mgr.start_session(mgr.websocket, new=True, input_mode='text')
                session_created = True
                logger.info(f"[{lanlan_name}] 文本session已创建")
            
            # 如果是新创建的session，等待TTS准备好
            if session_created and mgr.use_tts:
                logger.info(f"[{lanlan_name}] 等待TTS准备...")
                max_wait = 5  # 最多等待5秒
                wait_step = 0.1
                waited = 0
                while waited < max_wait:
                    async with mgr.tts_cache_lock:
                        if mgr.tts_ready:
                            logger.info(f"[{lanlan_name}] TTS已准备好")
                            break
                    await asyncio.sleep(wait_step)
                    waited += wait_step
                
                if waited >= max_wait:
                    logger.warning(f"[{lanlan_name}] TTS准备超时，继续发送（可能没有语音）")
            
            # 现在可以将AI的话添加到对话历史中
            from langchain_core.messages import AIMessage
            mgr.session._conversation_history.append(AIMessage(content=response_text))
            logger.info(f"[{lanlan_name}] 已将主动搭话添加到对话历史")
            
            # 生成新的speech_id（用于TTS）
            from uuid import uuid4
            async with mgr.lock:
                mgr.current_speech_id = str(uuid4())
            
            # 通过handle_text_data处理这段话（触发TTS和前端显示）
            # 分chunk发送以模拟流式效果
            chunks = [response_text[i:i+10] for i in range(0, len(response_text), 10)]
            for i, chunk in enumerate(chunks):
                await mgr.handle_text_data(chunk, is_first_chunk=(i == 0))
                await asyncio.sleep(0.05)  # 小延迟模拟流式
            
            # 调用response完成回调
            if hasattr(mgr, 'handle_response_complete'):
                await mgr.handle_response_complete()
            
            return JSONResponse({
                "success": True,
                "action": "chat",
                "message": "主动搭话已发送",
                "lanlan_name": lanlan_name
            })
            
        except asyncio.TimeoutError:
            logger.error(f"[{lanlan_name}] AI回复超时")
            return JSONResponse({
                "success": False,
                "error": "AI处理超时"
            }, status_code=504)
        except Exception as e:
            logger.error(f"[{lanlan_name}] AI处理失败: {e}")
            return JSONResponse({
                "success": False,
                "error": "AI处理失败",
                "detail": str(e)
            }, status_code=500)
        
    except Exception as e:
        logger.error(f"主动搭话接口异常: {e}")
        return JSONResponse({
            "success": False,
            "error": "服务器内部错误",
            "detail": str(e)
        }, status_code=500)

@app.get("/l2d", response_class=HTMLResponse)
async def get_l2d_manager(request: Request):
    """渲染Live2D模型管理器页面"""
    return templates.TemplateResponse("templates/l2d_manager.html", {
        "request": request
    })

@app.get('/api/characters/current_live2d_model')
async def get_current_live2d_model(catgirl_name: str = ""):
    """获取指定角色或当前角色的Live2D模型信息"""
    try:
        characters = _config_manager.load_characters()
        
        # 如果没有指定角色名称，使用当前猫娘
        if not catgirl_name:
            catgirl_name = characters.get('当前猫娘', '')
        
        # 查找指定角色的Live2D模型
        live2d_model_name = None
        model_info = None
        
        # 在猫娘列表中查找
        if '猫娘' in characters and catgirl_name in characters['猫娘']:
            catgirl_data = characters['猫娘'][catgirl_name]
            live2d_model_name = catgirl_data.get('live2d')
        
        # 如果找到了模型名称，获取模型信息
        if live2d_model_name:
            try:
                # 使用 find_model_directory 查找模型目录（支持 static 和用户文档目录）
                model_dir, url_prefix = find_model_directory(live2d_model_name)
                if os.path.exists(model_dir):
                    # 查找模型配置文件
                    model_files = [f for f in os.listdir(model_dir) if f.endswith('.model3.json')]
                    if model_files:
                        model_file = model_files[0]
                        model_path = f'{url_prefix}/{live2d_model_name}/{model_file}'
                        model_info = {
                            'name': live2d_model_name,
                            'path': model_path
                        }
            except Exception as e:
                logger.warning(f"获取模型信息失败: {e}")
        
        # 回退机制：如果没有找到模型，使用默认的mao_pro
        if not live2d_model_name or not model_info:
            logger.info(f"猫娘 {catgirl_name} 未设置Live2D模型，回退到默认模型 mao_pro")
            live2d_model_name = 'mao_pro'
            try:
                # 查找mao_pro模型
                model_dir, url_prefix = find_model_directory('mao_pro')
                if os.path.exists(model_dir):
                    model_files = [f for f in os.listdir(model_dir) if f.endswith('.model3.json')]
                    if model_files:
                        model_file = model_files[0]
                        model_path = f'{url_prefix}/mao_pro/{model_file}'
                        model_info = {
                            'name': 'mao_pro',
                            'path': model_path,
                            'is_fallback': True  # 标记这是回退模型
                        }
            except Exception as e:
                logger.error(f"获取默认模型mao_pro失败: {e}")
        
        return JSONResponse(content={
            'success': True,
            'catgirl_name': catgirl_name,
            'model_name': live2d_model_name,
            'model_info': model_info
        })
        
    except Exception as e:
        logger.error(f"获取角色Live2D模型失败: {e}")
        return JSONResponse(content={
            'success': False,
            'error': str(e)
        })

@app.get('/chara_manager', response_class=HTMLResponse)
async def chara_manager(request: Request):
    """渲染主控制页面"""
    return templates.TemplateResponse('templates/chara_manager.html', {"request": request})

@app.get('/voice_clone', response_class=HTMLResponse)
async def voice_clone_page(request: Request):
    return templates.TemplateResponse("templates/voice_clone.html", {"request": request})

@app.get("/api_key", response_class=HTMLResponse)
async def api_key_settings(request: Request):
    """API Key 设置页面"""
    return templates.TemplateResponse("templates/api_key_settings.html", {
        "request": request
    })

@app.get('/api/characters')
async def get_characters():
    return JSONResponse(content=_config_manager.load_characters())

@app.get('/api/characters/current_catgirl')
async def get_current_catgirl():
    """获取当前使用的猫娘名称"""
    characters = _config_manager.load_characters()
    current_catgirl = characters.get('当前猫娘', '')
    return JSONResponse(content={'current_catgirl': current_catgirl})

@app.post('/api/characters/current_catgirl')
async def set_current_catgirl(request: Request):
    """设置当前使用的猫娘"""
    data = await request.json()
    catgirl_name = data.get('catgirl_name', '') if data else ''
    
    if not catgirl_name:
        return JSONResponse({'success': False, 'error': '猫娘名称不能为空'}, status_code=400)
    
    characters = _config_manager.load_characters()
    if catgirl_name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '指定的猫娘不存在'}, status_code=404)
    
    old_catgirl = characters.get('当前猫娘', '')
    characters['当前猫娘'] = catgirl_name
    _config_manager.save_characters(characters)
    # 自动重新加载配置
    await initialize_character_data()
    
    # 通过WebSocket通知所有连接的客户端
    # 使用session_manager中的websocket，但需要确保websocket已设置
    notification_count = 0
    logger.info(f"开始通知WebSocket客户端：猫娘从 {old_catgirl} 切换到 {catgirl_name}")
    
    message = json.dumps({
        "type": "catgirl_switched",
        "new_catgirl": catgirl_name,
        "old_catgirl": old_catgirl
    })
    
    # 遍历所有session_manager，尝试发送消息
    for lanlan_name, mgr in session_manager.items():
        ws = mgr.websocket
        logger.info(f"检查 {lanlan_name} 的WebSocket: websocket存在={ws is not None}")
        
        if ws:
            try:
                await ws.send_text(message)
                notification_count += 1
                logger.info(f"✅ 已通过WebSocket通知 {lanlan_name} 的连接：猫娘已从 {old_catgirl} 切换到 {catgirl_name}")
            except Exception as e:
                logger.warning(f"❌ 通知 {lanlan_name} 的连接失败: {e}")
                # 如果发送失败，可能是连接已断开，清空websocket引用
                if mgr.websocket == ws:
                    mgr.websocket = None
    
    if notification_count > 0:
        logger.info(f"✅ 已通过WebSocket通知 {notification_count} 个连接的客户端：猫娘已从 {old_catgirl} 切换到 {catgirl_name}")
    else:
        logger.warning(f"⚠️ 没有找到任何活跃的WebSocket连接来通知猫娘切换")
        logger.warning(f"提示：请确保前端页面已打开并建立了WebSocket连接，且已调用start_session")
    
    return {"success": True}

@app.post('/api/characters/reload')
async def reload_character_config():
    """重新加载角色配置（热重载）"""
    try:
        await initialize_character_data()
        return {"success": True, "message": "角色配置已重新加载"}
    except Exception as e:
        logger.error(f"重新加载角色配置失败: {e}")
        return JSONResponse(
            {'success': False, 'error': f'重新加载失败: {str(e)}'}, 
            status_code=500
        )

@app.post('/api/characters/master')
async def update_master(request: Request):
    data = await request.json()
    if not data or not data.get('档案名'):
        return JSONResponse({'success': False, 'error': '档案名为必填项'}, status_code=400)
    characters = _config_manager.load_characters()
    characters['主人'] = {k: v for k, v in data.items() if v}
    _config_manager.save_characters(characters)
    # 自动重新加载配置
    await initialize_character_data()
    return {"success": True}

@app.post('/api/characters/catgirl')
async def add_catgirl(request: Request):
    data = await request.json()
    if not data or not data.get('档案名'):
        return JSONResponse({'success': False, 'error': '档案名为必填项'}, status_code=400)
    
    characters = _config_manager.load_characters()
    key = data['档案名']
    if key in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '该猫娘已存在'}, status_code=400)
    
    if '猫娘' not in characters:
        characters['猫娘'] = {}
    
    # 创建猫娘数据，只保存非空字段
    catgirl_data = {}
    for k, v in data.items():
        if k != '档案名':
            # voice_id 特殊处理：空字符串表示删除该字段
            if k == 'voice_id' and v == '':
                continue  # 不添加该字段，相当于删除
            elif v:  # 只保存非空字段
                catgirl_data[k] = v
    
    characters['猫娘'][key] = catgirl_data
    _config_manager.save_characters(characters)
    # 自动重新加载配置
    await initialize_character_data()
    return {"success": True}

@app.put('/api/characters/catgirl/{name}')
async def update_catgirl(name: str, request: Request):
    data = await request.json()
    if not data:
        return JSONResponse({'success': False, 'error': '无数据'}, status_code=400)
    characters = _config_manager.load_characters()
    if name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)
    
    # 记录更新前的voice_id，用于检测是否变更
    old_voice_id = characters['猫娘'][name].get('voice_id', '')
    
    # 如果包含voice_id，验证其有效性
    if 'voice_id' in data:
        voice_id = data['voice_id']
        # 空字符串表示删除voice_id，跳过验证
        if voice_id != '' and not _config_manager.validate_voice_id(voice_id):
            voices = _config_manager.get_voices_for_current_api()
            available_voices = list(voices.keys())
            return JSONResponse({
                'success': False, 
                'error': f'voice_id "{voice_id}" 在当前API的音色库中不存在',
                'available_voices': available_voices
            }, status_code=400)
    
    # 只更新前端传来的字段，未传字段保留原值，且不允许通过此接口修改 system_prompt
    removed_fields = []
    for k, v in characters['猫娘'][name].items():
        if k not in data and k not in ('档案名', 'system_prompt', 'voice_id', 'live2d'):
            removed_fields.append(k)
    for k in removed_fields:
        characters['猫娘'][name].pop(k)
    
    # 处理voice_id的特殊逻辑：如果传入空字符串，则删除该字段
    if 'voice_id' in data and data['voice_id'] == '':
        characters['猫娘'][name].pop('voice_id', None)
    
    # 更新其他字段
    for k, v in data.items():
        if k not in ('档案名', 'voice_id') and v:
            characters['猫娘'][name][k] = v
        elif k == 'voice_id' and v:  # voice_id非空时才更新
            characters['猫娘'][name][k] = v
    _config_manager.save_characters(characters)
    
    # 获取更新后的voice_id
    new_voice_id = characters['猫娘'][name].get('voice_id', '')
    voice_id_changed = (old_voice_id != new_voice_id)
    
    # 如果是当前活跃的猫娘且voice_id发生了变更，需要先通知前端，再关闭session
    is_current_catgirl = (name == characters.get('当前猫娘', ''))
    session_ended = False
    
    if voice_id_changed and is_current_catgirl and name in session_manager:
        # 检查是否有活跃的session
        if session_manager[name].is_active:
            logger.info(f"检测到 {name} 的voice_id已变更（{old_voice_id} -> {new_voice_id}），准备刷新...")
            
            # 1. 先发送刷新消息（WebSocket还连着）
            if session_manager[name].websocket:
                try:
                    await session_manager[name].websocket.send_text(json.dumps({
                        "type": "reload_page",
                        "message": "语音已更新，页面即将刷新"
                    }))
                    logger.info(f"已通知 {name} 的前端刷新页面")
                except Exception as e:
                    logger.warning(f"通知前端刷新页面失败: {e}")
            
            # 2. 立刻关闭session（这会断开WebSocket）
            try:
                await session_manager[name].end_session(by_server=True)
                session_ended = True
                logger.info(f"{name} 的session已结束")
            except Exception as e:
                logger.error(f"结束session时出错: {e}")
    
    # 自动重新加载配置
    await initialize_character_data()
    if voice_id_changed:
        logger.info(f"配置已重新加载，新的voice_id已生效")
    
    return {"success": True, "voice_id_changed": voice_id_changed, "session_restarted": session_ended}

@app.put('/api/characters/catgirl/l2d/{name}')
async def update_catgirl_l2d(name: str, request: Request):
    """更新指定猫娘的Live2D模型设置"""
    try:
        data = await request.json()
        live2d_model = data.get('live2d')
        
        if not live2d_model:
            return JSONResponse(content={
                'success': False,
                'error': '未提供Live2D模型名称'
            })
        
        # 加载当前角色配置
        characters = _config_manager.load_characters()
        
        # 确保猫娘配置存在
        if '猫娘' not in characters:
            characters['猫娘'] = {}
        
        # 确保指定猫娘的配置存在
        if name not in characters['猫娘']:
            characters['猫娘'][name] = {}
        
        # 更新Live2D模型设置
        characters['猫娘'][name]['live2d'] = live2d_model
        
        # 保存配置
        _config_manager.save_characters(characters)
        # 自动重新加载配置
        await initialize_character_data()
        
        return JSONResponse(content={
            'success': True,
            'message': f'已更新角色 {name} 的Live2D模型为 {live2d_model}'
        })
        
    except Exception as e:
        logger.error(f"更新角色Live2D模型失败: {e}")
        return JSONResponse(content={
            'success': False,
            'error': str(e)
        })

@app.put('/api/characters/catgirl/voice_id/{name}')
async def update_catgirl_voice_id(name: str, request: Request):
    data = await request.json()
    if not data:
        return JSONResponse({'success': False, 'error': '无数据'}, status_code=400)
    characters = _config_manager.load_characters()
    if name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)
    if 'voice_id' in data:
        voice_id = data['voice_id']
        # 验证voice_id是否在voice_storage中
        if not _config_manager.validate_voice_id(voice_id):
            voices = _config_manager.get_voices_for_current_api()
            available_voices = list(voices.keys())
            return JSONResponse({
                'success': False, 
                'error': f'voice_id "{voice_id}" 在当前API的音色库中不存在',
                'available_voices': available_voices
            }, status_code=400)
        characters['猫娘'][name]['voice_id'] = voice_id
    _config_manager.save_characters(characters)
    
    # 如果是当前活跃的猫娘，需要先通知前端，再关闭session
    is_current_catgirl = (name == characters.get('当前猫娘', ''))
    session_ended = False
    
    if is_current_catgirl and name in session_manager:
        # 检查是否有活跃的session
        if session_manager[name].is_active:
            logger.info(f"检测到 {name} 的voice_id已更新，准备刷新...")
            
            # 1. 先发送刷新消息（WebSocket还连着）
            if session_manager[name].websocket:
                try:
                    await session_manager[name].websocket.send_text(json.dumps({
                        "type": "reload_page",
                        "message": "语音已更新，页面即将刷新"
                    }))
                    logger.info(f"已通知 {name} 的前端刷新页面")
                except Exception as e:
                    logger.warning(f"通知前端刷新页面失败: {e}")
            
            # 2. 立刻关闭session（这会断开WebSocket）
            try:
                await session_manager[name].end_session(by_server=True)
                session_ended = True
                logger.info(f"{name} 的session已结束")
            except Exception as e:
                logger.error(f"结束session时出错: {e}")
    
    # 3. 重新加载配置，让新的voice_id生效
    await initialize_character_data()
    logger.info(f"配置已重新加载，新的voice_id已生效")
    
    return {"success": True, "session_restarted": session_ended}

@app.post('/api/characters/clear_voice_ids')
async def clear_voice_ids():
    """清除所有角色的本地Voice ID记录"""
    try:
        characters = _config_manager.load_characters()
        cleared_count = 0
        
        # 清除所有猫娘的voice_id
        if '猫娘' in characters:
            for name in characters['猫娘']:
                if 'voice_id' in characters['猫娘'][name] and characters['猫娘'][name]['voice_id']:
                    characters['猫娘'][name]['voice_id'] = ''
                    cleared_count += 1
        
        _config_manager.save_characters(characters)
        # 自动重新加载配置
        await initialize_character_data()
        
        return JSONResponse({
            'success': True, 
            'message': f'已清除 {cleared_count} 个角色的Voice ID记录',
            'cleared_count': cleared_count
        })
    except Exception as e:
        return JSONResponse({
            'success': False, 
            'error': f'清除Voice ID记录时出错: {str(e)}'
        }, status_code=500)

@app.post('/api/characters/set_microphone')
async def set_microphone(request: Request):
    try:
        data = await request.json()
        microphone_id = data.get('microphone_id')
        
        # 使用标准的load/save函数
        characters_data = _config_manager.load_characters()
        
        # 添加或更新麦克风选择
        characters_data['当前麦克风'] = microphone_id
        
        # 保存配置
        _config_manager.save_characters(characters_data)
        # 自动重新加载配置
        await initialize_character_data()
        
        return {"success": True}
    except Exception as e:
        logger.error(f"保存麦克风选择失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.get('/api/characters/get_microphone')
async def get_microphone():
    try:
        # 使用配置管理器加载角色配置
        characters_data = _config_manager.load_characters()
        
        # 获取保存的麦克风选择
        microphone_id = characters_data.get('当前麦克风')
        
        return {"microphone_id": microphone_id}
    except Exception as e:
        logger.error(f"获取麦克风选择失败: {e}")
        return {"microphone_id": None}

@app.post('/api/voice_clone')
async def voice_clone(file: UploadFile = File(...), prefix: str = Form(...)):
    # 直接读取到内存
    try:
        file_content = await file.read()
        file_buffer = io.BytesIO(file_content)
    except Exception as e:
        logger.error(f"读取文件到内存失败: {e}")
        return JSONResponse({'error': f'读取文件失败: {e}'}, status_code=500)


    def validate_audio_file(file_buffer: io.BytesIO, filename: str) -> tuple[str, str]:
        """
        验证音频文件类型和格式
        返回: (mime_type, error_message)
        """
        file_path_obj = pathlib.Path(filename)
        file_extension = file_path_obj.suffix.lower()
        
        # 检查文件扩展名
        if file_extension not in ['.wav', '.mp3', '.m4a']:
            return "", f"不支持的文件格式: {file_extension}。仅支持 WAV、MP3 和 M4A 格式。"
        
        # 根据扩展名确定MIME类型
        if file_extension == '.wav':
            mime_type = "audio/wav"
            # 检查WAV文件是否为16bit
            try:
                file_buffer.seek(0)
                with wave.open(file_buffer, 'rb') as wav_file:
                    # 检查采样宽度（bit depth）
                    if wav_file.getsampwidth() != 2:  # 2 bytes = 16 bits
                        return "", f"WAV文件必须是16bit格式，当前文件是{wav_file.getsampwidth() * 8}bit。"
                    
                    # 检查声道数（建议单声道）
                    channels = wav_file.getnchannels()
                    if channels > 1:
                        return "", f"建议使用单声道WAV文件，当前文件有{channels}个声道。"
                    
                    # 检查采样率
                    sample_rate = wav_file.getframerate()
                    if sample_rate not in [8000, 16000, 22050, 44100, 48000]:
                        return "", f"建议使用标准采样率(8000, 16000, 22050, 44100, 48000)，当前文件采样率: {sample_rate}Hz。"
                file_buffer.seek(0)
            except Exception as e:
                return "", f"WAV文件格式错误: {str(e)}。请确认您的文件是合法的WAV文件。"
                
        elif file_extension == '.mp3':
            mime_type = "audio/mpeg"
            try:
                file_buffer.seek(0)
                # 读取更多字节以支持不同的MP3格式
                header = file_buffer.read(32)
                file_buffer.seek(0)

                # 检查文件大小是否合理
                file_size = len(file_buffer.getvalue())
                if file_size < 1024:  # 至少1KB
                    return "", "MP3文件太小，可能不是有效的音频文件。"
                if file_size > 1024 * 1024 * 10:  # 10MB
                    return "", "MP3文件太大，可能不是有效的音频文件。"
                
                # 更宽松的MP3文件头检查
                # MP3文件通常以ID3标签或帧同步字开头
                # 检查是否以ID3标签开头 (ID3v2)
                has_id3_header = header.startswith(b'ID3')
                # 检查是否有帧同步字 (FF FA, FF FB, FF F2, FF F3, FF E3等)
                has_frame_sync = False
                for i in range(len(header) - 1):
                    if header[i] == 0xFF and (header[i+1] & 0xE0) == 0xE0:
                        has_frame_sync = True
                        break
                
                # 如果既没有ID3标签也没有帧同步字，则认为文件可能无效
                # 但这只是一个警告，不应该严格拒绝
                if not has_id3_header and not has_frame_sync:
                    return mime_type, "警告: MP3文件可能格式不标准，文件头: {header[:4].hex()}"
                        
            except Exception as e:
                return "", f"MP3文件读取错误: {str(e)}。请确认您的文件是合法的MP3文件。"
                
        elif file_extension == '.m4a':
            mime_type = "audio/mp4"
            try:
                file_buffer.seek(0)
                # 读取文件头来验证M4A格式
                header = file_buffer.read(32)
                file_buffer.seek(0)
                
                # M4A文件应该以'ftyp'盒子开始，通常在偏移4字节处
                # 检查是否包含'ftyp'标识
                if b'ftyp' not in header:
                    return "", "M4A文件格式无效或已损坏。请确认您的文件是合法的M4A文件。"
                
                # 进一步验证：检查是否包含常见的M4A类型标识
                # M4A通常包含'mp4a', 'M4A ', 'M4V '等类型
                valid_types = [b'mp4a', b'M4A ', b'M4V ', b'isom', b'iso2', b'avc1']
                has_valid_type = any(t in header for t in valid_types)
                
                if not has_valid_type:
                    return mime_type,  "警告: M4A文件格式无效或已损坏。请确认您的文件是合法的M4A文件。"
                        
            except Exception as e:
                return "", f"M4A文件读取错误: {str(e)}。请确认您的文件是合法的M4A文件。"
        
        return mime_type, ""

    try:
        # 1. 验证音频文件
        mime_type, error_msg = validate_audio_file(file_buffer, file.filename)
        if not mime_type:
            return JSONResponse({'error': error_msg}, status_code=400)
        
        # 检查文件大小（tfLink支持最大100MB）
        file_size = len(file_content)
        if file_size > 100 * 1024 * 1024:  # 100MB
            return JSONResponse({'error': '文件大小超过100MB，超过tfLink的限制'}, status_code=400)
        
        # 2. 上传到 tfLink - 直接使用内存中的内容
        file_buffer.seek(0)
        # 根据tfLink API文档，使用multipart/form-data上传文件
        # 参数名应为'file'
        files = {'file': (file.filename, file_buffer, mime_type)}
        
        # 添加更多的请求头，确保兼容性
        headers = {
            'Accept': 'application/json'
        }
        
        logger.info(f"正在上传文件到tfLink，文件名: {file.filename}, 大小: {file_size} bytes, MIME类型: {mime_type}")
        resp = requests.post('http://47.101.214.205:8000/api/upload', files=files, headers=headers, timeout=60)

        # 检查响应状态
        if resp.status_code != 200:
            logger.error(f"上传到tfLink失败，状态码: {resp.status_code}, 响应内容: {resp.text}")
            return JSONResponse({'error': f'上传到tfLink失败，状态码: {resp.status_code}, 详情: {resp.text[:200]}'}, status_code=500)
            
        try:
            # 解析JSON响应
            data = resp.json()
            logger.info(f"tfLink原始响应: {data}")
            
            # 获取下载链接
            tmp_url = None
            possible_keys = ['downloadLink', 'download_link', 'url', 'direct_link', 'link', 'download_url']
            for key in possible_keys:
                if key in data:
                    tmp_url = data[key]
                    logger.info(f"找到下载链接键: {key}")
                    break
            
            if not tmp_url:
                logger.error(f"无法从响应中提取URL: {data}")
                return JSONResponse({'error': f'上传成功但无法从响应中提取URL'}, status_code=500)
            
            # 确保URL有效
            if not tmp_url.startswith(('http://', 'https://')):
                logger.error(f"无效的URL格式: {tmp_url}")
                return JSONResponse({'error': f'无效的URL格式: {tmp_url}'}, status_code=500)
                
            # 测试URL是否可访问
            test_resp = requests.head(tmp_url, timeout=10)
            if test_resp.status_code >= 400:
                logger.error(f"生成的URL无法访问: {tmp_url}, 状态码: {test_resp.status_code}")
                return JSONResponse({'error': f'生成的临时URL无法访问，请重试'}, status_code=500)
                
            logger.info(f"成功获取临时URL并验证可访问性: {tmp_url}")
                
        except ValueError:
            raw_text = resp.text
            logger.error(f"上传成功但响应格式无法解析: {raw_text}")
            return JSONResponse({'error': f'上传成功但响应格式无法解析: {raw_text[:200]}'}, status_code=500)
        
        # 3. 用直链注册音色
        core_config = _config_manager.get_core_config()
        dashscope.api_key = core_config['AUDIO_API_KEY']
        service = VoiceEnrollmentService()
        target_model = "cosyvoice-v2"
        
        # 重试配置
        max_retries = 3
        retry_delay = 3  # 重试前等待的秒数
        
        for attempt in range(max_retries):
            try:
                logger.info(f"开始音色注册（尝试 {attempt + 1}/{max_retries}），使用URL: {tmp_url}")
                # 设置超时参数
                import time
                start_time = time.time()
                
                # 添加超时装饰器或使用上下文管理器
                # 这里使用try-except块和时间检查来实现简单的超时控制
                voice_id = None
                
                # 创建一个超时标志
                timeout_occurred = False
                
                try:
                    # 尝试执行音色注册，设置一个较大的超时时间
                    voice_id = service.create_voice(target_model=target_model, prefix=prefix, url=tmp_url)
                except Exception as inner_e:
                    error_str = str(inner_e)
                    if "ResponseTimeout" in error_str or "response timeout" in error_str.lower():
                        timeout_occurred = True
                        logger.warning(f"音色注册超时: {error_str}")
                    else:
                        raise inner_e
                
                if timeout_occurred:
                    return JSONResponse({
                        'error': '音色注册超时，请稍后重试',
                        'detail': '服务器响应超时，这可能是由于网络延迟或服务繁忙导致的',
                        'file_url': tmp_url,
                        'suggestion': '请检查您的网络连接，或稍后再试'
                    }, status_code=408)
                    
                logger.info(f"音色注册成功，voice_id: {voice_id}")
                voice_data = {
                    'voice_id': voice_id,
                    'prefix': prefix,
                    'file_url': tmp_url,
                    'created_at': datetime.now().isoformat()
                }
                try:
                    _config_manager.save_voice_for_current_api(voice_id, voice_data)
                    logger.info(f"voice_id已保存到音色库: {voice_id}")
                    
                    # 验证voice_id是否能够被正确读取
                    if not _config_manager.validate_voice_id(voice_id):
                        logger.error(f"voice_id保存后验证失败: {voice_id}")
                        return JSONResponse({
                            'error': f'音色注册成功但保存验证失败，请重试',
                            'voice_id': voice_id,
                            'file_url': tmp_url
                        }, status_code=500)
                    logger.info(f"voice_id保存验证成功: {voice_id}")
                    
                except Exception as save_error:
                    logger.error(f"保存voice_id到音色库失败: {save_error}")
                    return JSONResponse({
                        'error': f'音色注册成功但保存到音色库失败: {str(save_error)}',
                        'voice_id': voice_id,
                        'file_url': tmp_url
                    }, status_code=500)
                return JSONResponse({
                    'voice_id': voice_id,
                    'request_id': service.get_last_request_id(),
                    'file_url': tmp_url,
                    'message': '音色注册成功并已保存到音色库'
                })
            except Exception as e:
                logger.error(f"音色注册失败（尝试 {attempt + 1}/{max_retries}）: {str(e)}")
                # 详细的错误信息
                error_detail = str(e)
                
                # 添加对ResponseTimeout的专门处理
                if "ResponseTimeout" in error_detail or "response timeout" in error_detail.lower():
                    return JSONResponse({
                        'error': '音色注册超时，请稍后重试',
                        'detail': error_detail,
                        'file_url': tmp_url,
                        'suggestion': '请检查您的网络连接，或稍后再试'
                    }, status_code=408)
                
                # 处理415错误（文件下载失败）- 如果不是最后一次尝试，则等待后重试
                elif "download audio failed" in error_detail or "415" in error_detail:
                    if attempt < max_retries - 1:
                        logger.warning(f"检测到文件下载失败（415错误），等待 {retry_delay} 秒后重试...")
                        await asyncio.sleep(retry_delay)
                        continue  # 重试
                    else:
                        logger.error(f"音色注册失败: 达到最大重试次数（{max_retries}次）")
                        return JSONResponse({
                            'error': f'音色注册失败: 无法下载音频文件，已尝试{max_retries}次',
                            'detail': error_detail,
                            'file_url': tmp_url,
                            'suggestion': '请检查文件URL是否可访问，或稍后重试'
                        }, status_code=415)
                
                # 其他错误直接返回
                return JSONResponse({
                    'error': f'音色注册失败: {error_detail}',
                    'file_url': tmp_url
                }, status_code=500)
    except Exception as e:
        # 确保tmp_url在出现异常时也有定义
        tmp_url = locals().get('tmp_url', '未获取到URL')
        logger.error(f"注册音色时发生未预期的错误: {str(e)}")
        return JSONResponse({'error': f'注册音色时发生错误: {str(e)}', 'file_url': tmp_url}, status_code=500)

@app.get('/api/voices')
async def get_voices():
    """获取当前API key对应的所有已注册音色"""
    return {"voices": _config_manager.get_voices_for_current_api()}

@app.post('/api/voices')
async def register_voice(request: Request):
    """注册新音色"""
    try:
        data = await request.json()
        voice_id = data.get('voice_id')
        voice_data = data.get('voice_data')
        
        if not voice_id or not voice_data:
            return JSONResponse({
                'success': False,
                'error': '缺少必要参数'
            }, status_code=400)
        
        # 准备音色数据
        complete_voice_data = {
            **voice_data,
            'voice_id': voice_id,
            'created_at': datetime.now().isoformat()
        }
        
        try:
            _config_manager.save_voice_for_current_api(voice_id, complete_voice_data)
        except Exception as e:
            logger.warning(f"保存音色配置失败: {e}")
            return JSONResponse({
                'success': False,
                'error': f'保存音色配置失败: {str(e)}'
            }, status_code=500)
            
        return {"success": True, "message": "音色注册成功"}
    except Exception as e:
        return JSONResponse({
            'success': False,
            'error': str(e)
        }, status_code=500)

@app.delete('/api/characters/catgirl/{name}')
async def delete_catgirl(name: str):
    import shutil
    
    characters = _config_manager.load_characters()
    if name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)
    
    # 检查是否是当前正在使用的猫娘
    current_catgirl = characters.get('当前猫娘', '')
    if name == current_catgirl:
        return JSONResponse({'success': False, 'error': '不能删除当前正在使用的猫娘！请先切换到其他猫娘后再删除。'}, status_code=400)
    
    # 删除对应的记忆文件
    try:
        memory_paths = [_config_manager.memory_dir, _config_manager.project_memory_dir]
        files_to_delete = [
            f'semantic_memory_{name}',  # 语义记忆目录
            f'time_indexed_{name}',     # 时间索引数据库文件
            f'settings_{name}.json',    # 设置文件
            f'recent_{name}.json',      # 最近聊天记录文件
        ]
        
        for base_dir in memory_paths:
            for file_name in files_to_delete:
                file_path = base_dir / file_name
                if file_path.exists():
                    try:
                        if file_path.is_dir():
                            shutil.rmtree(file_path)
                        else:
                            file_path.unlink()
                        logger.info(f"已删除: {file_path}")
                    except Exception as e:
                        logger.warning(f"删除失败 {file_path}: {e}")
    except Exception as e:
        logger.error(f"删除记忆文件时出错: {e}")
    
    # 删除角色配置
    del characters['猫娘'][name]
    _config_manager.save_characters(characters)
    await initialize_character_data()
    return {"success": True}

@app.post('/api/beacon/shutdown')
async def beacon_shutdown():
    """Beacon API for graceful server shutdown"""
    try:
        # 从 app.state 获取配置
        current_config = get_start_config()
        # Only respond to beacon if server was started with --open-browser
        if current_config['browser_mode_enabled']:
            logger.info("收到beacon信号，准备关闭服务器...")
            # Schedule server shutdown
            asyncio.create_task(shutdown_server_async())
            return {"success": True, "message": "服务器关闭信号已接收"}
    except Exception as e:
        logger.error(f"Beacon处理错误: {e}")
        return {"success": False, "error": str(e)}

async def shutdown_server_async():
    """异步关闭服务器"""
    try:
        # Give a small delay to allow the beacon response to be sent
        await asyncio.sleep(0.5)
        logger.info("正在关闭服务器...")
        
        # 向memory_server发送关闭信号
        try:
            import requests
            from config import MEMORY_SERVER_PORT
            shutdown_url = f"http://localhost:{MEMORY_SERVER_PORT}/shutdown"
            response = requests.post(shutdown_url, timeout=1)
            if response.status_code == 200:
                logger.info("已向memory_server发送关闭信号")
            else:
                logger.warning(f"向memory_server发送关闭信号失败，状态码: {response.status_code}")
        except Exception as e:
            logger.warning(f"向memory_server发送关闭信号时出错: {e}")
        
        # Signal the server to stop
        current_config = get_start_config()
        if current_config['server'] is not None:
            current_config['server'].should_exit = True
    except Exception as e:
        logger.error(f"关闭服务器时出错: {e}")

@app.post('/api/characters/catgirl/{old_name}/rename')
async def rename_catgirl(old_name: str, request: Request):
    data = await request.json()
    new_name = data.get('new_name') if data else None
    if not new_name:
        return JSONResponse({'success': False, 'error': '新档案名不能为空'}, status_code=400)
    characters = _config_manager.load_characters()
    if old_name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '原猫娘不存在'}, status_code=404)
    if new_name in characters['猫娘']:
        return JSONResponse({'success': False, 'error': '新档案名已存在'}, status_code=400)
    # 重命名
    characters['猫娘'][new_name] = characters['猫娘'].pop(old_name)
    # 如果当前猫娘是被重命名的猫娘，也需要更新
    if characters.get('当前猫娘') == old_name:
        characters['当前猫娘'] = new_name
    _config_manager.save_characters(characters)
    # 自动重新加载配置
    await initialize_character_data()
    return {"success": True}

@app.post('/api/characters/catgirl/{name}/unregister_voice')
async def unregister_voice(name: str):
    """解除猫娘的声音注册"""
    try:
        characters = _config_manager.load_characters()
        if name not in characters.get('猫娘', {}):
            return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)
        
        # 检查是否已有voice_id
        if not characters['猫娘'][name].get('voice_id'):
            return JSONResponse({'success': False, 'error': '该猫娘未注册声音'}, status_code=400)
        
        # 删除voice_id字段
        if 'voice_id' in characters['猫娘'][name]:
            characters['猫娘'][name].pop('voice_id')
        _config_manager.save_characters(characters)
        # 自动重新加载配置
        await initialize_character_data()
        
        logger.info(f"已解除猫娘 '{name}' 的声音注册")
        return {"success": True, "message": "声音注册已解除"}
        
    except Exception as e:
        logger.error(f"解除声音注册时出错: {e}")
        return JSONResponse({'success': False, 'error': f'解除注册失败: {str(e)}'}, status_code=500)

@app.get('/api/memory/recent_files')
async def get_recent_files():
    """获取 memory 目录下所有 recent*.json 文件名列表"""
    from utils.config_manager import get_config_manager
    cm = get_config_manager()
    files = glob.glob(str(cm.memory_dir / 'recent*.json'))
    file_names = [os.path.basename(f) for f in files]
    return {"files": file_names}

@app.get('/api/memory/review_config')
async def get_review_config():
    """获取记忆整理配置"""
    try:
        from utils.config_manager import get_config_manager
        config_manager = get_config_manager()
        config_path = str(config_manager.get_config_path('core_config.json'))
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
                # 如果配置中没有这个键，默认返回True（开启）
                return {"enabled": config_data.get('recent_memory_auto_review', True)}
        else:
            # 如果配置文件不存在，默认返回True（开启）
            return {"enabled": True}
    except Exception as e:
        logger.error(f"读取记忆整理配置失败: {e}")
        return {"enabled": True}

@app.post('/api/memory/review_config')
async def update_review_config(request: Request):
    """更新记忆整理配置"""
    try:
        data = await request.json()
        enabled = data.get('enabled', True)
        
        from utils.config_manager import get_config_manager
        config_manager = get_config_manager()
        config_path = str(config_manager.get_config_path('core_config.json'))
        config_data = {}
        
        # 读取现有配置
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
        
        # 更新配置
        config_data['recent_memory_auto_review'] = enabled
        
        # 保存配置
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"记忆整理配置已更新: enabled={enabled}")
        return {"success": True, "enabled": enabled}
    except Exception as e:
        logger.error(f"更新记忆整理配置失败: {e}")
        return {"success": False, "error": str(e)}

@app.get('/api/memory/recent_file')
async def get_recent_file(filename: str):
    """获取指定 recent*.json 文件内容"""
    from utils.config_manager import get_config_manager
    cm = get_config_manager()
    file_path = str(cm.memory_dir / filename)
    if not (filename.startswith('recent') and filename.endswith('.json')):
        return JSONResponse({"success": False, "error": "文件名不合法"}, status_code=400)
    if not os.path.exists(file_path):
        return JSONResponse({"success": False, "error": "文件不存在"}, status_code=404)
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    return {"content": content}

@app.get("/api/live2d/model_config/{model_name}")
async def get_model_config(model_name: str):
    """获取指定Live2D模型的model3.json配置"""
    try:
        # 查找模型目录（可能在static或用户文档目录）
        model_dir, url_prefix = find_model_directory(model_name)
        if not os.path.exists(model_dir):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型目录不存在"})
        
        # 查找.model3.json文件
        model_json_path = None
        for file in os.listdir(model_dir):
            if file.endswith('.model3.json'):
                model_json_path = os.path.join(model_dir, file)
                break
        
        if not model_json_path or not os.path.exists(model_json_path):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型配置文件不存在"})
        
        with open(model_json_path, 'r', encoding='utf-8') as f:
            config_data = json.load(f)
        
        # 检查并自动添加缺失的配置
        config_updated = False
        
        # 确保FileReferences存在
        if 'FileReferences' not in config_data:
            config_data['FileReferences'] = {}
            config_updated = True
        
        # 确保Motions存在
        if 'Motions' not in config_data['FileReferences']:
            config_data['FileReferences']['Motions'] = {}
            config_updated = True
        
        # 确保Expressions存在
        if 'Expressions' not in config_data['FileReferences']:
            config_data['FileReferences']['Expressions'] = []
            config_updated = True
        
        # 如果配置有更新，保存到文件
        if config_updated:
            with open(model_json_path, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, ensure_ascii=False, indent=4)
            logger.info(f"已为模型 {model_name} 自动添加缺失的配置项")
            
        return {"success": True, "config": config_data}
    except Exception as e:
        logger.error(f"获取模型配置失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.post("/api/live2d/model_config/{model_name}")
async def update_model_config(model_name: str, request: Request):
    """更新指定Live2D模型的model3.json配置"""
    try:
        data = await request.json()
        
        # 查找模型目录（可能在static或用户文档目录）
        model_dir, url_prefix = find_model_directory(model_name)
        if not os.path.exists(model_dir):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型目录不存在"})
        
        # 查找.model3.json文件
        model_json_path = None
        for file in os.listdir(model_dir):
            if file.endswith('.model3.json'):
                model_json_path = os.path.join(model_dir, file)
                break
        
        if not model_json_path or not os.path.exists(model_json_path):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型配置文件不存在"})
        
        # 为了安全，只允许修改 Motions 和 Expressions
        with open(model_json_path, 'r', encoding='utf-8') as f:
            current_config = json.load(f)
            
        if 'FileReferences' in data and 'Motions' in data['FileReferences']:
            current_config['FileReferences']['Motions'] = data['FileReferences']['Motions']
            
        if 'FileReferences' in data and 'Expressions' in data['FileReferences']:
            current_config['FileReferences']['Expressions'] = data['FileReferences']['Expressions']

        with open(model_json_path, 'w', encoding='utf-8') as f:
            json.dump(current_config, f, ensure_ascii=False, indent=4) # 使用 indent=4 保持格式
            
        return {"success": True, "message": "模型配置已更新"}
    except Exception as e:
        logger.error(f"更新模型配置失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.get('/api/live2d/model_files/{model_name}')
async def get_model_files(model_name: str):
    """获取指定Live2D模型的动作和表情文件列表"""
    try:
        # 查找模型目录（可能在static或用户文档目录）
        model_dir, url_prefix = find_model_directory(model_name)
        
        if not os.path.exists(model_dir):
            return {"success": False, "error": f"模型 {model_name} 不存在"}
        
        motion_files = []
        expression_files = []
        
        # 递归搜索所有子文件夹
        def search_files_recursive(directory, target_ext, result_list):
            """递归搜索指定扩展名的文件"""
            try:
                for item in os.listdir(directory):
                    item_path = os.path.join(directory, item)
                    if os.path.isfile(item_path):
                        if item.endswith(target_ext):
                            # 计算相对于模型根目录的路径
                            relative_path = os.path.relpath(item_path, model_dir)
                            # 转换为正斜杠格式（跨平台兼容）
                            relative_path = relative_path.replace('\\', '/')
                            result_list.append(relative_path)
                    elif os.path.isdir(item_path):
                        # 递归搜索子目录
                        search_files_recursive(item_path, target_ext, result_list)
            except Exception as e:
                logger.warning(f"搜索目录 {directory} 时出错: {e}")
        
        # 搜索动作文件
        search_files_recursive(model_dir, '.motion3.json', motion_files)
        
        # 搜索表情文件
        search_files_recursive(model_dir, '.exp3.json', expression_files)
        
        logger.info(f"模型 {model_name} 文件统计: {len(motion_files)} 个动作文件, {len(expression_files)} 个表情文件")
        return {
            "success": True, 
            "motion_files": motion_files,
            "expression_files": expression_files
        }
    except Exception as e:
        logger.error(f"获取模型文件列表失败: {e}")
        return {"success": False, "error": str(e)}

@app.get('/live2d_emotion_manager', response_class=HTMLResponse)
async def live2d_emotion_manager(request: Request):
    """Live2D情感映射管理器页面"""
    try:
        with open('templates/live2d_emotion_manager.html', 'r', encoding='utf-8') as f:
            content = f.read()
        return HTMLResponse(content=content)
    except Exception as e:
        logger.error(f"加载Live2D情感映射管理器页面失败: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get('/api/live2d/emotion_mapping/{model_name}')
async def get_emotion_mapping(model_name: str):
    """获取情绪映射配置"""
    try:
        # 查找模型目录（可能在static或用户文档目录）
        model_dir, url_prefix = find_model_directory(model_name)
        if not os.path.exists(model_dir):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型目录不存在"})
        
        # 查找.model3.json文件
        model_json_path = None
        for file in os.listdir(model_dir):
            if file.endswith('.model3.json'):
                model_json_path = os.path.join(model_dir, file)
                break
        
        if not model_json_path or not os.path.exists(model_json_path):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型配置文件不存在"})
        
        with open(model_json_path, 'r', encoding='utf-8') as f:
            config_data = json.load(f)

        # 优先使用 EmotionMapping；若不存在则从 FileReferences 推导
        emotion_mapping = config_data.get('EmotionMapping')
        if not emotion_mapping:
            derived_mapping = {"motions": {}, "expressions": {}}
            file_refs = config_data.get('FileReferences', {}) or {}

            # 从标准 Motions 结构推导
            motions = file_refs.get('Motions', {}) or {}
            for group_name, items in motions.items():
                files = []
                for item in items or []:
                    try:
                        file_path = item.get('File') if isinstance(item, dict) else None
                        if file_path:
                            files.append(file_path.replace('\\', '/'))
                    except Exception:
                        continue
                derived_mapping["motions"][group_name] = files

            # 从标准 Expressions 结构推导（按 Name 的前缀进行分组，如 happy_xxx）
            expressions = file_refs.get('Expressions', []) or []
            for item in expressions:
                if not isinstance(item, dict):
                    continue
                name = item.get('Name') or ''
                file_path = item.get('File') or ''
                if not file_path:
                    continue
                file_path = file_path.replace('\\', '/')
                # 根据第一个下划线拆分分组
                if '_' in name:
                    group = name.split('_', 1)[0]
                else:
                    # 无前缀的归入 neutral 组，避免丢失
                    group = 'neutral'
                derived_mapping["expressions"].setdefault(group, []).append(file_path)

            emotion_mapping = derived_mapping
        
        return {"success": True, "config": emotion_mapping}
    except Exception as e:
        logger.error(f"获取情绪映射配置失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.post('/api/live2d/upload_model')
async def upload_live2d_model(files: list[UploadFile] = File(...)):
    """上传Live2D模型到用户文档目录"""
    import shutil
    import tempfile
    import zipfile
    
    try:
        if not files:
            return JSONResponse(status_code=400, content={"success": False, "error": "没有上传文件"})
        
        # 创建临时目录来处理上传的文件
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            
            # 保存所有上传的文件到临时目录，保持目录结构
            for file in files:
                # 从文件的相对路径中提取目录结构
                file_path = file.filename
                # 确保路径安全，移除可能的危险路径字符
                file_path = file_path.replace('\\', '/').lstrip('/')
                
                target_file_path = temp_path / file_path
                target_file_path.parent.mkdir(parents=True, exist_ok=True)
                
                # 保存文件
                with open(target_file_path, 'wb') as f:
                    content = await file.read()
                    f.write(content)
            
            # 在临时目录中递归查找.model3.json文件
            model_json_files = list(temp_path.rglob('*.model3.json'))
            
            if not model_json_files:
                return JSONResponse(status_code=400, content={"success": False, "error": "未找到.model3.json文件"})
            
            if len(model_json_files) > 1:
                return JSONResponse(status_code=400, content={"success": False, "error": "上传的文件中包含多个.model3.json文件"})
            
            model_json_file = model_json_files[0]
            
            # 确定模型根目录（.model3.json文件的父目录）
            model_root_dir = model_json_file.parent
            model_name = model_root_dir.name
            
            # 获取用户文档的live2d目录
            config_mgr = get_config_manager()
            config_mgr.ensure_live2d_directory()
            user_live2d_dir = config_mgr.live2d_dir
            
            # 目标目录
            target_model_dir = user_live2d_dir / model_name
            
            # 如果目标目录已存在，返回错误或覆盖（这里选择返回错误）
            if target_model_dir.exists():
                return JSONResponse(status_code=400, content={
                    "success": False, 
                    "error": f"模型 {model_name} 已存在，请先删除或重命名现有模型"
                })
            
            # 复制模型根目录到用户文档的live2d目录
            shutil.copytree(model_root_dir, target_model_dir)
            
            logger.info(f"成功上传Live2D模型: {model_name} -> {target_model_dir}")
            
            return JSONResponse(content={
                "success": True,
                "message": f"模型 {model_name} 上传成功",
                "model_name": model_name,
                "model_path": str(target_model_dir)
            })
            
    except Exception as e:
        logger.error(f"上传Live2D模型失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.post('/api/live2d/emotion_mapping/{model_name}')
async def update_emotion_mapping(model_name: str, request: Request):
    """更新情绪映射配置"""
    try:
        data = await request.json()
        
        if not data:
            return JSONResponse(status_code=400, content={"success": False, "error": "无效的数据"})

        # 查找模型目录（可能在static或用户文档目录）
        model_dir, url_prefix = find_model_directory(model_name)
        if not os.path.exists(model_dir):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型目录不存在"})
        
        # 查找.model3.json文件
        model_json_path = None
        for file in os.listdir(model_dir):
            if file.endswith('.model3.json'):
                model_json_path = os.path.join(model_dir, file)
                break
        
        if not model_json_path or not os.path.exists(model_json_path):
            return JSONResponse(status_code=404, content={"success": False, "error": "模型配置文件不存在"})

        with open(model_json_path, 'r', encoding='utf-8') as f:
            config_data = json.load(f)

        # 统一写入到标准 Cubism 结构（FileReferences.Motions / FileReferences.Expressions）
        file_refs = config_data.setdefault('FileReferences', {})

        # 处理 motions: data 结构为 { motions: { emotion: ["motions/xxx.motion3.json", ...] }, expressions: {...} }
        motions_input = (data.get('motions') if isinstance(data, dict) else None) or {}
        motions_output = {}
        for group_name, files in motions_input.items():
            # 禁止在"常驻"组配置任何motion
            if group_name == '常驻':
                logger.info("忽略常驻组中的motion配置（只允许expression）")
                continue
            items = []
            for file_path in files or []:
                if not isinstance(file_path, str):
                    continue
                normalized = file_path.replace('\\', '/').lstrip('./')
                items.append({"File": normalized})
            motions_output[group_name] = items
        file_refs['Motions'] = motions_output

        # 处理 expressions: 将按 emotion 前缀生成扁平列表，Name 采用 "{emotion}_{basename}" 的约定
        expressions_input = (data.get('expressions') if isinstance(data, dict) else None) or {}

        # 先保留不属于我们情感前缀的原始表达（避免覆盖用户自定义）
        existing_expressions = file_refs.get('Expressions', []) or []
        emotion_prefixes = set(expressions_input.keys())
        preserved_expressions = []
        for item in existing_expressions:
            try:
                name = (item.get('Name') or '') if isinstance(item, dict) else ''
                prefix = name.split('_', 1)[0] if '_' in name else None
                if not prefix or prefix not in emotion_prefixes:
                    preserved_expressions.append(item)
            except Exception:
                preserved_expressions.append(item)

        new_expressions = []
        for emotion, files in expressions_input.items():
            for file_path in files or []:
                if not isinstance(file_path, str):
                    continue
                normalized = file_path.replace('\\', '/').lstrip('./')
                base = os.path.basename(normalized)
                base_no_ext = base.replace('.exp3.json', '')
                name = f"{emotion}_{base_no_ext}"
                new_expressions.append({"Name": name, "File": normalized})

        file_refs['Expressions'] = preserved_expressions + new_expressions

        # 同时保留一份 EmotionMapping（供管理器读取与向后兼容）
        config_data['EmotionMapping'] = data

        # 保存配置到文件
        with open(model_json_path, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"模型 {model_name} 的情绪映射配置已更新（已同步到 FileReferences）")
        return {"success": True, "message": "情绪映射配置已保存"}
    except Exception as e:
        logger.error(f"更新情绪映射配置失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.post('/api/memory/recent_file/save')
async def save_recent_file(request: Request):
    import os, json
    data = await request.json()
    filename = data.get('filename')
    chat = data.get('chat')
    from utils.config_manager import get_config_manager
    cm = get_config_manager()
    file_path = str(cm.memory_dir / filename)
    if not (filename and filename.startswith('recent') and filename.endswith('.json')):
        return JSONResponse({"success": False, "error": "文件名不合法"}, status_code=400)
    arr = []
    for msg in chat:
        t = msg.get('role')
        text = msg.get('text', '')
        arr.append({
            "type": t,
            "data": {
                "content": text,
                "additional_kwargs": {},
                "response_metadata": {},
                "type": t,
                "name": None,
                "id": None,
                "example": False,
                **({"tool_calls": [], "invalid_tool_calls": [], "usage_metadata": None} if t == "ai" else {})
            }
        })
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(arr, f, ensure_ascii=False, indent=2)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post('/api/emotion/analysis')
async def emotion_analysis(request: Request):
    try:
        data = await request.json()
        if not data or 'text' not in data:
            return {"error": "请求体中必须包含text字段"}
        
        text = data['text']
        api_key = data.get('api_key')
        model = data.get('model')
        
        # 使用参数或默认配置
        core_config = _config_manager.get_core_config()
        api_key = api_key or core_config['OPENROUTER_API_KEY']
        model = model or core_config['EMOTION_MODEL']
        
        if not api_key:
            return {"error": "API密钥未提供且配置中未设置默认密钥"}
        
        if not model:
            return {"error": "模型名称未提供且配置中未设置默认模型"}
        
        # 创建异步客户端
        client = AsyncOpenAI(api_key=api_key, base_url=core_config['OPENROUTER_URL'])
        
        # 构建请求消息
        messages = [
            {
                "role": "system", 
                "content": emotion_analysis_prompt
            },
            {
                "role": "user", 
                "content": text
            }
        ]
        
        # 异步调用模型
        request_params = {
            "model": model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 100
        }
        
        # 只有在需要时才添加 extra_body
        if model in MODELS_WITH_EXTRA_BODY:
            request_params["extra_body"] = {"enable_thinking": False}
        
        response = await client.chat.completions.create(**request_params)
        
        # 解析响应
        result_text = response.choices[0].message.content.strip()
        
        # 尝试解析JSON响应
        try:
            import json
            result = json.loads(result_text)
            # 获取emotion和confidence
            emotion = result.get("emotion", "neutral")
            confidence = result.get("confidence", 0.5)
            
            # 当confidence小于0.3时，自动将emotion设置为neutral
            if confidence < 0.3:
                emotion = "neutral"
            
            # 获取 lanlan_name 并推送到 monitor
            lanlan_name = data.get('lanlan_name')
            if lanlan_name and lanlan_name in sync_message_queue:
                sync_message_queue[lanlan_name].put({
                    "type": "json",
                    "data": {
                        "type": "emotion",
                        "emotion": emotion,
                        "confidence": confidence
                    }
                })
            
            return {
                "emotion": emotion,
                "confidence": confidence
            }
        except json.JSONDecodeError:
            # 如果JSON解析失败，返回简单的情感判断
            return {
                "emotion": "neutral",
                "confidence": 0.5
            }
            
    except Exception as e:
        logger.error(f"情感分析失败: {e}")
        return {
            "error": f"情感分析失败: {str(e)}",
            "emotion": "neutral",
            "confidence": 0.0
        }

@app.get('/memory_browser', response_class=HTMLResponse)
async def memory_browser(request: Request):
    return templates.TemplateResponse('templates/memory_browser.html', {"request": request})


@app.get("/{lanlan_name}", response_class=HTMLResponse)
async def get_index(request: Request, lanlan_name: str):
    # lanlan_name 将从 URL 中提取，前端会通过 API 获取配置
    return templates.TemplateResponse("templates/index.html", {
        "request": request
    })

@app.post('/api/agent/flags')
async def update_agent_flags(request: Request):
    """来自前端的Agent开关更新，级联到各自的session manager。"""
    try:
        data = await request.json()
        _, her_name_current, _, _, _, _, _, _, _, _ = _config_manager.get_character_data()
        lanlan = data.get('lanlan_name') or her_name_current
        flags = data.get('flags') or {}
        mgr = session_manager.get(lanlan)
        if not mgr:
            return JSONResponse({"success": False, "error": "lanlan not found"}, status_code=404)
        # Update core flags first
        mgr.update_agent_flags(flags)
        # Forward to tool server for MCP/Computer-Use flags
        try:
            forward_payload = {}
            if 'mcp_enabled' in flags:
                forward_payload['mcp_enabled'] = bool(flags['mcp_enabled'])
            if 'computer_use_enabled' in flags:
                forward_payload['computer_use_enabled'] = bool(flags['computer_use_enabled'])
            if forward_payload:
                async with httpx.AsyncClient(timeout=0.7) as client:
                    r = await client.post(f"http://localhost:{TOOL_SERVER_PORT}/agent/flags", json=forward_payload)
                    if not r.is_success:
                        raise Exception(f"tool_server responded {r.status_code}")
        except Exception as e:
            # On failure, reset flags in core to safe state
            mgr.update_agent_flags({'agent_enabled': False, 'computer_use_enabled': False, 'mcp_enabled': False})
            return JSONResponse({"success": False, "error": f"tool_server forward failed: {e}"}, status_code=502)
        return {"success": True}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get('/api/agent/health')
async def agent_health():
    """Check tool_server health via main_server proxy."""
    try:
        async with httpx.AsyncClient(timeout=0.7) as client:
            r = await client.get(f"http://localhost:{TOOL_SERVER_PORT}/health")
            if not r.is_success:
                return JSONResponse({"status": "down"}, status_code=502)
            data = {}
            try:
                data = r.json()
            except Exception:
                pass
            return {"status": "ok", **({"tool": data} if isinstance(data, dict) else {})}
    except Exception:
        return JSONResponse({"status": "down"}, status_code=502)


@app.get('/api/agent/computer_use/availability')
async def proxy_cu_availability():
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            r = await client.get(f"http://localhost:{TOOL_SERVER_PORT}/computer_use/availability")
            if not r.is_success:
                return JSONResponse({"ready": False, "reasons": [f"tool_server responded {r.status_code}"]}, status_code=502)
            return r.json()
    except Exception as e:
        return JSONResponse({"ready": False, "reasons": [f"proxy error: {e}"]}, status_code=502)


@app.get('/api/agent/mcp/availability')
async def proxy_mcp_availability():
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            r = await client.get(f"http://localhost:{TOOL_SERVER_PORT}/mcp/availability")
            if not r.is_success:
                return JSONResponse({"ready": False, "reasons": [f"tool_server responded {r.status_code}"]}, status_code=502)
            return r.json()
    except Exception as e:
        return JSONResponse({"ready": False, "reasons": [f"proxy error: {e}"]}, status_code=502)


@app.get('/api/agent/tasks')
async def proxy_tasks():
    """Get all tasks from tool server via main_server proxy."""
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            r = await client.get(f"http://localhost:{TOOL_SERVER_PORT}/tasks")
            if not r.is_success:
                return JSONResponse({"tasks": [], "error": f"tool_server responded {r.status_code}"}, status_code=502)
            return r.json()
    except Exception as e:
        return JSONResponse({"tasks": [], "error": f"proxy error: {e}"}, status_code=502)


@app.get('/api/agent/tasks/{task_id}')
async def proxy_task_detail(task_id: str):
    """Get specific task details from tool server via main_server proxy."""
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            r = await client.get(f"http://localhost:{TOOL_SERVER_PORT}/tasks/{task_id}")
            if not r.is_success:
                return JSONResponse({"error": f"tool_server responded {r.status_code}"}, status_code=502)
            return r.json()
    except Exception as e:
        return JSONResponse({"error": f"proxy error: {e}"}, status_code=502)


# Task status polling endpoint for frontend
@app.get('/api/agent/task_status')
async def get_task_status():
    """Get current task status for frontend polling - returns all tasks with their current status."""
    try:
        # Get tasks from tool server using async client with increased timeout
        async with httpx.AsyncClient(timeout=2.5) as client:
            r = await client.get(f"http://localhost:{TOOL_SERVER_PORT}/tasks")
            if not r.is_success:
                return JSONResponse({"tasks": [], "error": f"tool_server responded {r.status_code}"}, status_code=502)
            
            tasks_data = r.json()
            tasks = tasks_data.get("tasks", [])
            debug_info = tasks_data.get("debug", {})
            
            # Enhance task data with additional information if needed
            enhanced_tasks = []
            for task in tasks:
                enhanced_task = {
                    "id": task.get("id"),
                    "status": task.get("status", "unknown"),
                    "type": task.get("type", "unknown"),
                    "lanlan_name": task.get("lanlan_name"),
                    "start_time": task.get("start_time"),
                    "end_time": task.get("end_time"),
                    "params": task.get("params", {}),
                    "result": task.get("result"),
                    "error": task.get("error"),
                    "source": task.get("source", "unknown")  # 添加来源信息
                }
                enhanced_tasks.append(enhanced_task)
            
            return {
                "success": True,
                "tasks": enhanced_tasks,
                "total_count": len(enhanced_tasks),
                "running_count": len([t for t in enhanced_tasks if t.get("status") == "running"]),
                "queued_count": len([t for t in enhanced_tasks if t.get("status") == "queued"]),
                "completed_count": len([t for t in enhanced_tasks if t.get("status") == "completed"]),
                "failed_count": len([t for t in enhanced_tasks if t.get("status") == "failed"]),
                "timestamp": datetime.now().isoformat(),
                "debug": debug_info  # 传递调试信息到前端
            }
        
    except Exception as e:
        return JSONResponse({
            "success": False,
            "tasks": [],
            "error": f"Failed to fetch task status: {str(e)}",
            "timestamp": datetime.now().isoformat()
        }, status_code=500)


@app.post('/api/agent/admin/control')
async def proxy_admin_control(payload):
    """Proxy admin control commands to tool server."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(f"http://localhost:{TOOL_SERVER_PORT}/admin/control", json=payload)
            if not r.is_success:
                return JSONResponse({"success": False, "error": f"tool_server responded {r.status_code}"}, status_code=502)
            
            result = r.json()
            logger.info(f"Admin control result: {result}")
            return result
        
    except Exception as e:
        return JSONResponse({
            "success": False,
            "error": f"Failed to execute admin control: {str(e)}"
        }, status_code=500)


# --- Run the Server ---
if __name__ == "__main__":
    import uvicorn
    import argparse
    import os
    import signal
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--open-browser",   action="store_true",
                        help="启动后是否打开浏览器并监控它")
    parser.add_argument("--page",           type=str, default="",
                        choices=["index", "chara_manager", "api_key", ""],
                        help="要打开的页面路由（不含域名和端口）")
    args = parser.parse_args()

    logger.info("--- Starting FastAPI Server ---")
    # Use os.path.abspath to show full path clearly
    logger.info(f"Serving static files from: {os.path.abspath('static')}")
    logger.info(f"Serving index.html from: {os.path.abspath('templates/index.html')}")
    logger.info(f"Access UI at: http://127.0.0.1:{MAIN_SERVER_PORT} (or your network IP:{MAIN_SERVER_PORT})")
    logger.info("-----------------------------")

    # Custom logging filter to suppress specific endpoints
    class EndpointFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            # Suppress only INFO level logs for specific endpoints
            # Keep WARNING and ERROR logs
            if record.levelno > logging.INFO:
                return True
            return record.getMessage().find("/api/characters/current_catgirl") == -1

    # Add filter to uvicorn access logger
    logging.getLogger("uvicorn.access").addFilter(EndpointFilter())

    # 1) 配置 UVicorn
    config = uvicorn.Config(
        app=app,
        host="0.0.0.0",
        port=MAIN_SERVER_PORT,
        log_level="info",
        loop="asyncio",
        reload=False,
    )
    server = uvicorn.Server(config)
    
    # Set browser mode flag if --open-browser is used
    if args.open_browser:
        # 使用 FastAPI 的 app.state 来管理配置
        start_config = {
            "browser_mode_enabled": True,
            "browser_page": args.page if args.page!='index' else '',
            'server': server
        }
        set_start_config(start_config)
    else:
        # 设置默认配置
        start_config = {
            "browser_mode_enabled": False,
            "browser_page": "",
            'server': server
        }
        set_start_config(start_config)

    print(f"启动配置: {get_start_config()}")

    # 2) 定义服务器关闭回调
    def shutdown_server():
        logger.info("收到浏览器关闭信号，正在关闭服务器...")
        os.kill(os.getpid(), signal.SIGTERM)

    # 4) 启动服务器（阻塞，直到 server.should_exit=True）
    logger.info("--- Starting FastAPI Server ---")
    logger.info(f"Access UI at: http://127.0.0.1:{MAIN_SERVER_PORT}/{args.page}")
    
    try:
        server.run()
    finally:
        logger.info("服务器已关闭")
