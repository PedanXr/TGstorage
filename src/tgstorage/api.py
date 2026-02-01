from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Response, Depends, Header, Query
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from importlib import resources
import base64
import hashlib
import hmac
import json
import os
import secrets
import datetime
import shutil
import re
import asyncio
import logging
import time
from typing import List, Optional
from .config import settings
from .database import (
    add_file, get_file_by_id, delete_file_db, 
    get_file_by_share_token, increment_view_count,
    list_files, get_stats, verify_key_db, init_db,
    upsert_user_from_telegram, get_user_by_telegram_id, list_users, set_user_status
)
from .bot import cluster

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

api = FastAPI(title="TG Storage Cluster API")

api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SESSION_COOKIE_NAME = "tg_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 7
TELEGRAM_AUTH_MAX_AGE_SECONDS = 60 * 60 * 24

def build_telegram_data_check_string(payload: dict) -> str:
    entries = []
    for key in sorted(payload.keys()):
        if key == "hash":
            continue
        value = payload.get(key)
        if value is None:
            continue
        entries.append(f"{key}={value}")
    return "\n".join(entries)

def verify_telegram_login(payload: dict) -> bool:
    if not settings.TELEGRAM_LOGIN_BOT_TOKEN:
        logger.error("Missing TELEGRAM_LOGIN_BOT_TOKEN")
        return False
    provided_hash = payload.get("hash")
    if not provided_hash:
        logger.error("Telegram payload missing hash")
        return False
    try:
        auth_date = int(payload.get("auth_date", 0))
    except (TypeError, ValueError):
        logger.error("Invalid auth_date in Telegram payload")
        return False
    now = int(time.time())
    if now - auth_date > TELEGRAM_AUTH_MAX_AGE_SECONDS:
        logger.warning("Telegram auth_date expired: %s", auth_date)
        return False
    data_check_string = build_telegram_data_check_string(payload)
    secret_key = hashlib.sha256(settings.TELEGRAM_LOGIN_BOT_TOKEN.encode()).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed_hash, provided_hash):
        logger.warning("Telegram login hash mismatch")
        return False
    return True

def create_session_token(payload: dict) -> str:
    session_payload = {
        "id": payload.get("id"),
        "username": payload.get("username"),
        "first_name": payload.get("first_name"),
        "last_name": payload.get("last_name"),
        "photo_url": payload.get("photo_url"),
        "auth_date": payload.get("auth_date"),
        "iat": int(time.time()),
        "exp": int(time.time()) + SESSION_MAX_AGE_SECONDS,
    }
    payload_bytes = json.dumps(session_payload, separators=(",", ":"), sort_keys=True).encode()
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode()
    signature = hmac.new(settings.ADMIN_API_KEY.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{signature}"

def verify_session_token(token: str) -> Optional[dict]:
    if not token or "." not in token:
        return None
    payload_b64, signature = token.rsplit(".", 1)
    expected_signature = hmac.new(settings.ADMIN_API_KEY.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        return None
    padding = "=" * (-len(payload_b64) % 4)
    try:
        payload_bytes = base64.urlsafe_b64decode(payload_b64 + padding)
        payload = json.loads(payload_bytes.decode())
    except (ValueError, json.JSONDecodeError):
        return None
    exp = payload.get("exp")
    if exp and int(time.time()) > int(exp):
        return None
    return payload

# Flexible Authentication
async def verify_api_key(
    x_api_key: Optional[str] = Header(None), 
    key: Optional[str] = Query(None),
    request: Request = None
):
    provided_key = (x_api_key or key or "").strip()
    if not provided_key and request is not None:
        session_token = request.cookies.get(SESSION_COOKIE_NAME)
        session_payload = verify_session_token(session_token)
        if session_payload:
            return f"telegram:{session_payload.get('id')}"
    if provided_key:
        session_payload = verify_session_token(provided_key)
        if session_payload:
            return f"telegram:{session_payload.get('id')}"
    if not provided_key:
        logger.error("No API key provided")
        raise HTTPException(status_code=403, detail="API Key required")
        
    if await verify_key_db(provided_key):
        return provided_key
        
    if provided_key == settings.ADMIN_API_KEY.strip():
        return provided_key

    logger.error(f"Auth failed. Provided: {provided_key}")
    raise HTTPException(status_code=403, detail="Invalid API Key")

def is_admin_auth(auth: str) -> bool:
    return auth == settings.ADMIN_API_KEY.strip()

def extract_telegram_id(auth: str) -> Optional[str]:
    if auth and auth.startswith("telegram:"):
        return auth.split(":", 1)[1]
    return None

async def ensure_approved_user(auth: str, action: str) -> None:
    if is_admin_auth(auth):
        return
    telegram_id = extract_telegram_id(auth)
    if telegram_id:
        user = await get_user_by_telegram_id(telegram_id)
        if not user or user["status"] != "approved":
            raise HTTPException(status_code=403, detail=f"User not approved for {action}")

async def verify_upload_access(auth: str = Depends(verify_api_key)) -> str:
    await ensure_approved_user(auth, "uploads")
    return auth

async def verify_admin(auth: str = Depends(verify_api_key)) -> str:
    if not is_admin_auth(auth):
        raise HTTPException(status_code=403, detail="Admin access required")
    return auth

@api.options("/{rest_of_path:path}")
async def preflight_handler(request: Request, rest_of_path: str):
    return Response(status_code=200, headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "*",
        "Access-Control-Allow-Headers": "*",
    })

@api.get("/auth/config")
async def get_auth_config():
    return {"bot_username": settings.TELEGRAM_LOGIN_BOT_USERNAME}

@api.get("/auth/session")
async def get_auth_session(request: Request):
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    session_payload = verify_session_token(session_token)
    if not session_payload:
        raise HTTPException(status_code=401, detail="No active session")
    return {"status": "ok", "user": session_payload}

@api.post("/auth/telegram")
async def telegram_login(request: Request, response: Response):
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid Telegram payload")
    if not verify_telegram_login(payload):
        raise HTTPException(status_code=401, detail="Telegram verification failed")
    telegram_id = payload.get("id")
    await upsert_user_from_telegram(
        telegram_id=telegram_id,
        username=payload.get("username"),
        first_name=payload.get("first_name"),
        last_name=payload.get("last_name"),
    )
    user_record = await get_user_by_telegram_id(telegram_id)
    session_token = create_session_token(payload)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return {
        "status": "ok",
        "token": session_token,
        "user": {
            "id": payload.get("id"),
            "username": payload.get("username"),
            "first_name": payload.get("first_name"),
            "last_name": payload.get("last_name"),
            "photo_url": payload.get("photo_url"),
            "status": user_record["status"] if user_record else "pending",
        },
    }

@api.post("/auth/logout")
async def telegram_logout(response: Response):
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"status": "ok"}

@api.get("/", response_class=HTMLResponse)
async def get_dashboard():
    try:
        return resources.files("tgstorage").joinpath("index.html").read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, AttributeError) as exc:
        logger.warning("Falling back to local index.html: %s", exc)
        index_path = os.path.join(os.path.dirname(__file__), "index.html")
        with open(index_path, "r", encoding="utf-8") as handle:
            return handle.read()

async def start_bot():
    await init_db()
    await cluster.start_all()

@api.on_event("startup")
async def startup():
    asyncio.create_task(start_bot())

@api.post("/upload")
async def upload(
    file: UploadFile = File(...), 
    expiration_days: int = Form(None),
    password: str = Form(None),
    auth: str = Depends(verify_upload_access)
):
    bot = await cluster.get_healthy_bot()
    if not bot:
        raise HTTPException(status_code=503, detail="No healthy bots available")

    temp_path = f"temp_{secrets.token_hex(4)}_{file.filename}"
    try:
        def save_file():
            with open(temp_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            return os.path.getsize(temp_path)

        file_size = await asyncio.to_thread(save_file)
        
        # Check file size limit (50 MB = 52428800 bytes)
        MAX_FILE_SIZE = 52428800
        if file_size > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413, 
                detail=f"File too large. Maximum allowed size is 50 MB. Telegram Bot API limits file uploads to 50 MB per file."
            )
        logger.info(f"Uploading {file.filename} via {bot._custom_name}")
        
        is_video = file.content_type and "video" in file.content_type.lower()
        
        with open(temp_path, 'rb') as doc_file:
            if is_video:
                message = await asyncio.wait_for(
                    bot.send_video(chat_id=settings.CHANNEL_ID, video=doc_file, filename=file.filename, supports_streaming=True),
                    timeout=600
                )
            else:
                message = await asyncio.wait_for(
                    bot.send_document(chat_id=settings.CHANNEL_ID, document=doc_file, filename=file.filename),
                    timeout=300
                )
        
        media = message.video or message.document
        file_id = media.file_id
        share_token = secrets.token_urlsafe(16)
        exp_date = (datetime.datetime.now() + datetime.timedelta(days=expiration_days)).isoformat() if expiration_days else None
        
        await add_file(
            file_id,
            message.message_id,
            file.filename,
            file_size,
            file.content_type or "application/octet-stream",
            exp_date,
            share_token,
            password,
            auth,
        )
        
        return {
            "status": "success", 
            "file_id": file_id, 
            "direct_link": f"{settings.BASE_URL}/dl/{file_id}/{file.filename}", 
            "share_link": f"{settings.BASE_URL}/share/{share_token}"
        }
    except Exception as e:
        logger.error(f"Upload failure: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        def cleanup():
            if os.path.exists(temp_path):
                try: os.remove(temp_path)
                except: pass
        await asyncio.to_thread(cleanup)

async def stream_file_response(file_data, filename, bot, request: Request):
    await increment_view_count(file_data['file_id'])
    file_size = file_data['file_size']
    mime = file_data['mime_type']
    
    range_header = request.headers.get("Range")
    start_byte = 0
    end_byte = file_size - 1
    status_code = 200
    
    if range_header:
        match = re.match(r"bytes=(\d+)-(\d+)?", range_header)
        if match:
            start_byte = int(match.group(1))
            if match.group(2):
                end_byte = int(match.group(2))
            status_code = 206

    content_length = end_byte - start_byte + 1

    async def stream_file(url, start, end):
        import httpx
        proxy_url = None
        proxy_host = getattr(settings, "PROXY_HOST", None)
        proxy_port = getattr(settings, "PROXY_PORT", None)
        proxy_user = getattr(settings, "PROXY_USER", None)
        proxy_pass = getattr(settings, "PROXY_PASS", None)
        if proxy_host and proxy_port:
            if proxy_user and proxy_pass:
                proxy_url = f"http://{proxy_user}:{proxy_pass}@{proxy_host}:{proxy_port}"
            else:
                proxy_url = f"http://{proxy_host}:{proxy_port}"

        headers = {"Range": f"bytes={start}-{end}"}
        async with httpx.AsyncClient(proxy=proxy_url) as client:
            async with client.stream("GET", url, headers=headers) as r:
                async for chunk in r.aiter_bytes():
                    yield chunk

    try:
        tg_file = await bot.get_file(file_data['file_id'])
        disposition = "inline" if any(x in mime for x in ["image", "text", "pdf", "video", "audio"]) else "attachment"
        headers = {
            "Accept-Ranges": "bytes", 
            "Content-Length": str(content_length), 
            "Content-Type": mime, 
            "Content-Disposition": f"{disposition}; filename=\"{filename}\""
        }
        if status_code == 206:
            headers["Content-Range"] = f"bytes {start_byte}-{end_byte}/{file_size}"

        return StreamingResponse(stream_file(tg_file.file_path, start_byte, end_byte), status_code=status_code, headers=headers)
    except Exception as e:
        logger.error(f"Streaming error: {e}")
        raise HTTPException(status_code=500, detail="Error streaming from Telegram")

@api.get("/share/{token}")
async def get_share_page(token: str, request: Request):
    file_data = await get_file_by_share_token(token)
    if not file_data:
        raise HTTPException(status_code=404, detail="Link expired or invalid")
    bot = await cluster.get_healthy_bot()
    if not bot: raise HTTPException(status_code=503, detail="Bots unavailable")
    return await stream_file_response(file_data, file_data['file_name'], bot, request)

@api.get("/debug/db")
async def debug_db(auth: str = Depends(verify_api_key)):
    files = await list_files(100, 0, auth_key=auth)
    return {"count": len(files), "files": [dict(f) for f in files]}

@api.get("/stats")
async def get_system_stats(auth: str = Depends(verify_api_key)):
    return await get_stats()

@api.get("/files")
async def list_all_files(limit: int = 50, offset: int = 0, search: str = None, auth: str = Depends(verify_api_key)):
    logger.info(f"Listing files: limit={limit}, offset={offset}, search={search}")
    await ensure_approved_user(auth, "listing files")
    files = await list_files(limit, offset, search, auth_key=auth)
    result = [dict(f) for f in files]
    logger.info(f"Found {len(result)} files")
    return result

@api.get("/admin/users")
async def list_admin_users(status: Optional[str] = Query(None), auth: str = Depends(verify_admin)):
    users = await list_users(status=status)
    return [dict(u) for u in users]

@api.post("/admin/users/{telegram_id}/approve")
async def approve_admin_user(telegram_id: str, auth: str = Depends(verify_admin)):
    user = await get_user_by_telegram_id(telegram_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    await set_user_status(telegram_id, "approved")
    updated = await get_user_by_telegram_id(telegram_id)
    return {"status": "ok", "user": dict(updated)}

@api.post("/admin/users/{telegram_id}/block")
async def block_admin_user(telegram_id: str, auth: str = Depends(verify_admin)):
    user = await get_user_by_telegram_id(telegram_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    await set_user_status(telegram_id, "blocked")
    updated = await get_user_by_telegram_id(telegram_id)
    return {"status": "ok", "user": dict(updated)}

@api.get("/f/{file_id}/{filename}")
@api.get("/dl/{file_id}/{filename}")
async def download_file(file_id: str, filename: str, request: Request, password: str = None):
    file_data = await get_file_by_id(file_id)
    if not file_data: raise HTTPException(status_code=404, detail="File not found")
    if file_data['password'] and file_data['password'] != password: raise HTTPException(status_code=403, detail="Password required")
    bot = await cluster.get_healthy_bot()
    if not bot: raise HTTPException(status_code=503, detail="Bots unavailable")
    return await stream_file_response(file_data, filename, bot, request)

@api.delete("/file/{file_id}")
async def delete_file_endpoint(file_id: str, auth: str = Depends(verify_api_key)):
    file_data = await get_file_by_id(file_id)
    if not file_data: raise HTTPException(status_code=404, detail="File not found")
    try: await cluster.delete_messages(settings.CHANNEL_ID, file_data['message_id'])
    except Exception as e: logger.error(f"Error deleting Telegram message: {e}")
    await delete_file_db(file_id)
    return {"status": "success", "message": "File deleted"}
