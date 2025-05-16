import asyncio
import hashlib
import hmac
import io
import json
import os
import platform
import shutil
import sys
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, FSInputFile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from fastapi import BackgroundTasks
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, Depends, Response, UploadFile, \
    File, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from telegram import WebAppData
from typing import Optional, List
from urllib.parse import parse_qs

import database as db
from ai_integration import get_ai_response
from config import MANAGER_GROUP_CHAT_ID, TELEGRAM_BOT_TOKEN
from config import logger
from minio_storage import minio_storage
from models import UserInfo, Message as DbMessage, Chat, WebSocketMessage, MediaContent
from telegram_bot import notify_managers_new_request, bot as tg_bot, create_manager_chat_topic
from utils import cleanup_chat_files
from websocket_manager import manager as ws_manager


def get_windows_version():
    try:
        return platform.system(), platform.release()
    except:
        return "Windows", "10"


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:

        await db.connect_db()
        from telegram_bot import run_bot
        loop = asyncio.get_running_loop()
        loop.create_task(run_bot())
        logger.info("FastAPI приложение запущено, бот запущен в фоновом режиме.")

        yield

        await db.close_db()
        logger.info("FastAPI приложение остановлено.")
    except Exception as e:
        logger.error(f"Ошибка в lifespan: {e}")
        raise


app = FastAPI(title="VROOM Chat Service", lifespan=lifespan)

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

MAX_FILE_SIZE = 250 * 1024 * 1024
ALLOWED_FILE_TYPES = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/gif': '.gif', 'application/pdf': '.pdf',
    'application/msword': '.doc', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
    'application/vnd.ms-excel': '.xls', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
    'text/plain': '.txt', 'video/quicktime': '.mov', 'video/mp4': '.mp4'}


async def get_user_from_query(user_id: int, user_name: Optional[str] = None) -> db.User:
    """Получает или создает пользователя по ID из query-параметра."""
    user = await db.find_or_create_user(user_id, user_name)
    if not user:
        logger.error(f"Не удалось получить или создать пользователя с ID: {user_id}")
        raise HTTPException(status_code=500, detail="Failed to process user information")
    return user


@app.get("/", response_class=HTMLResponse)
async def get_chat_page(request: Request):
    """Отдает основную HTML страницу чата"""

    return templates.TemplateResponse("chat.html", {"request": request})


@app.post("/chat/{chat_id}/feedback", status_code=200)
async def handle_feedback(chat_id: str, data: dict):
    """Обработка нажатия кнопки 'Я доволен ответом'"""
    logger.info(f"Получен feedback для чата {chat_id}: {data}")
    action = data.get("action")

    if action == "satisfied":
        chat = await db.get_chat_by_id(chat_id)
        if not chat:
            raise HTTPException(status_code=404, detail="Chat not found")
        if chat.status == "closed":
            logger.info(f"Чат {chat_id} уже был закрыт.")
            return {"message": "Chat already closed"}

        success = await db.update_chat_status(chat_id, "closed")
        if success:
            logger.info(f"Чат {chat_id} закрыт по кнопке 'Я доволен ответом'.")

            await ws_manager.send_personal_message({"type": "status_update", "payload": {"status": "closed",
                                                                                         "message": "Спасибо за обратную связь!",
                                                                                         "show_new_chat_button": True,
                                                                                         "chat_id": chat_id}},
                chat.user_id)
            return {"message": "Chat closed successfully"}
        else:
            logger.error(f"Не удалось закрыть чат {chat_id} по кнопке 'Я доволен ответом'.")
            raise HTTPException(status_code=500, detail="Failed to close chat")
    else:
        raise HTTPException(status_code=400, detail="Invalid action")


@app.post("/chat/{chat_id}/request_manager")
async def handle_request_manager(chat_id: str):
    """Обработчик запроса на подключение менеджера к чату"""

    chat = await db.get_chat_by_id(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Чат не найден")

    old_topic_id = None
    try:
        if chat.status == "closed":

            old_topic_id = chat.topic_id
            if old_topic_id:
                try:

                    await tg_bot.get_forum_topic(MANAGER_GROUP_CHAT_ID, old_topic_id)

                    topic = await tg_bot.get_forum_topic(MANAGER_GROUP_CHAT_ID, old_topic_id)
                    current_name = topic.name.replace('[ЗАКРЫТ] ', '')

                    await tg_bot.edit_forum_topic(MANAGER_GROUP_CHAT_ID, old_topic_id, name=f"[АКТИВЕН] {current_name}")
                    logger.info(f"Изменено название топика {old_topic_id} для чата {chat_id} на активный")

                    await tg_bot.send_message(MANAGER_GROUP_CHAT_ID, "🔄 Чат переоткрыт клиентом",
                        message_thread_id=old_topic_id)

                    await db.reopen_chat(chat_id, old_topic_id)

                    await ws_manager.send_personal_message({"type": "status_update", "payload": {
                        "message": "Чат переоткрыт. Ожидайте ответа оператора.", "chat_id": chat_id}}, chat.user_id)
                    return {"status": "success", "message": "Чат переоткрыт"}
                except Exception as e:
                    logger.warning(f"Не удалось переоткрыть топик {old_topic_id}: {e}")

                    old_topic_id = None

            await db.reopen_chat(chat_id)
            chat.status = "active"
            chat.manager_requested = False
            chat.topic_id = None

    except Exception as e:
        logger.error(f"Ошибка при проверке/переоткрытии чата: {e}")

    user = await db.get_user(chat.user_id)
    if not user:
        logger.error(f"Не найден пользователь {chat.user_id} при запросе менеджера для чата {chat_id}")
        raise HTTPException(status_code=500, detail="User not found for the chat")

    if not chat.topic_id:
        topic = await create_manager_chat_topic(user, chat)
        if not topic:
            logger.error(f"Не удалось создать топик для чата {chat_id}")
            await ws_manager.send_personal_message({"type": "error", "payload": {
                "message": "Не удалось отправить запрос оператору. Попробуйте позже.", "chat_id": chat_id}},
                chat.user_id)
            raise HTTPException(status_code=500, detail="Failed to create topic")
        chat.topic_id = topic.message_thread_id

    history = await db.get_chat_history(chat_id, limit=250)
    first_message_text = history[0].text if history else "Клиент нажал кнопку 'Позвать оператора'"

    success = await notify_managers_new_request(user, chat, first_message_text, chat.topic_id)
    if not success:
        logger.warning(f"Не удалось отправить уведомление менеджерам для чата {chat_id}")

        await ws_manager.send_personal_message({"type": "error", "payload": {
            "message": "Не удалось отправить уведомление оператору. Попробуйте позже.", "chat_id": chat_id}},
            chat.user_id)
        return {"status": "warning", "message": "Запрос отправлен, но возникли проблемы с уведомлением оператора"}

    await ws_manager.send_personal_message({"type": "status_update",
        "payload": {"message": f"Запрос отправлен оператору. Ожидайте ответа.", "chat_id": chat_id,
            "user_info": {"user_id": user.user_id, "user_name": user.user_name, "language": user.language,
                "currency": user.currency}}}, chat.user_id)

    success = await db.set_manager_requested(chat_id, topic_id=chat.topic_id)
    if not success:
        raise HTTPException(status_code=500, detail="Не удалось обновить статус чата")

    return {"status": "success", "message": "Запрос на менеджера отправлен"}


@app.get("/api/media/{file_path:path}")
async def get_media(file_path: str):
    """Получает медиа-файл из MinIO"""
    try:

        presigned_url = minio_storage.get_presigned_url(file_path)

        return RedirectResponse(url=presigned_url)
    except Exception as e:
        logger.error(f"Ошибка при получении файла {file_path}: {e}")
        raise HTTPException(status_code=404, detail="Файл не найден")


@app.post("/upload")
async def upload_file(request: Request, file: UploadFile = File(...)):
    try:

        chat_id = request.query_params.get("chat_id")
        message = request.query_params.get("message")
        sender_id = request.query_params.get("sender_id")

        logger.info(f"Начало загрузки файла. chat_id: {chat_id}, filename: {file.filename}")

        if not chat_id:
            logger.error("Отсутствует chat_id")
            raise HTTPException(status_code=400, detail="Не указан ID чата")

        if not message:
            logger.error("Отсутствует message")
            raise HTTPException(status_code=400, detail="Не указано сообщение")

        if not sender_id:
            logger.error("Отсутствует sender_id")
            raise HTTPException(status_code=400, detail="Не указан ID отправителя")

        file_size = 0
        chunk_size = 1024
        while chunk := await file.read(chunk_size):
            file_size += len(chunk)
            if file_size > MAX_FILE_SIZE:
                logger.warning(f"Файл слишком большой: {file_size} байт")
                raise HTTPException(status_code=400, detail=f"Размер файла превышает {MAX_FILE_SIZE / 1024 / 1024}MB")
        await file.seek(0)

        if file.content_type not in ALLOWED_FILE_TYPES:
            logger.warning(f"Неподдерживаемый тип файла: {file.content_type}")
            raise HTTPException(status_code=400,
                detail=f"Неподдерживаемый тип файла. Разрешены: {', '.join(ALLOWED_FILE_TYPES.keys())}")

        temp_file = io.BytesIO()
        while chunk := await file.read(chunk_size):
            temp_file.write(chunk)
        temp_file.seek(0)

        minio_path = f"{chat_id}/{file.filename}"

        try:

            minio_storage.client.put_object(minio_storage.bucket_name, minio_path, temp_file, file_size,
                content_type=file.content_type)

            presigned_url = minio_storage.get_presigned_url(minio_path)

            message_data = json.loads(message)
            text = message_data.get("payload", {}).get("text", "")

            media_type = "photo" if file.content_type.startswith("image/") else "video" if file.content_type.startswith(
                "video/") else "voice" if file.content_type.startswith("audio/") else "document"

            media = MediaContent(type=media_type, file_id=minio_path, mime_type=file.content_type, file_size=file_size)

            db_message = DbMessage(chat_id=chat_id, sender_id=sender_id, text=text, media=media)
            await db.add_message(db_message)

            return {"success": True, "file_url": presigned_url, "file_path": minio_path,
                "message_id": str(db_message.id)}

        except Exception as e:
            logger.error(f"Ошибка при загрузке файла в MinIO: {e}")
            raise HTTPException(status_code=500, detail="Ошибка при загрузке файла")

    except Exception as e:
        logger.error(f"Ошибка при обработке файла: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await file.close()


@app.post("/chat/{chat_id}/take")
async def handle_take_chat(chat_id: str, manager_id: int):
    """Обработчик взятия чата менеджером"""

    chat = await db.get_chat_by_id(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Чат не найден")

    if chat.status == "closed":
        raise HTTPException(status_code=400, detail="Чат уже закрыт")
    if not chat.manager_requested:
        raise HTTPException(status_code=400, detail="Менеджер не был запрошен для этого чата")
    if chat.manager_id:
        raise HTTPException(status_code=400, detail="Чат уже взят другим менеджером")

    manager = await db.get_user(manager_id)
    if not manager:
        raise HTTPException(status_code=404, detail="Менеджер не найден")

    success = await db.set_chat_manager(chat_id, manager_id)
    if not success:
        raise HTTPException(status_code=500, detail="Не удалось обновить статус чата")

    await ws_manager.send_personal_message({"type": "status_update",
        "payload": {"message": f"Оператор подключился к чату", "chat_id": chat_id, "manager": {"id": manager_id,

        }}}, chat.user_id)

    return {"status": "success", "message": "Чат успешно взят"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Основной эндпоинт для WebSocket соединения клиента"""
    try:

        init_data = websocket.query_params.get("initData")
        if not init_data:
            logger.warning("WebSocket: Отсутствует InitData")
            await websocket.close(code=1008)
            return

        try:

            parsed_data = parse_qs(init_data)

            if 'hash' not in parsed_data or 'user' not in parsed_data:
                logger.warning("WebSocket: Отсутствуют необходимые поля в InitData")
                await websocket.close(code=1008)
                return

            received_hash = parsed_data['hash'][0]
            user_data = json.loads(parsed_data['user'][0])

            check_string = []
            for key, value in parsed_data.items():
                if key != 'hash':
                    if isinstance(value, list):
                        check_string.append(f"{key}={value[0]}")
                    else:
                        check_string.append(f"{key}={value}")
            check_string = '\n'.join(sorted(check_string))

            secret_key = hmac.new("WebAppData".encode(), TELEGRAM_BOT_TOKEN.encode(), hashlib.sha256).digest()

            computed_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()

            if computed_hash != received_hash:
                logger.warning("WebSocket: Неверная подпись InitData")
                await websocket.close(code=1008)
                return

            user_id = user_data['id']
            user_name = user_data.get('username', '')
            if not user_name:
                user_name = user_data.get('first_name', '')
                if 'last_name' in user_data:
                    user_name += f" {user_data['last_name']}"

            logger.debug(f"Попытка WebSocket подключения от user_id={user_id}, user_name={user_name}")

            try:
                user = await get_user_from_query(user_id, user_name)
                logger.info(f"WebSocket: Пользователь {user.user_id} ({user.user_name}) аутентифицирован.")
            except HTTPException as e:
                logger.warning(f"WebSocket: Ошибка аутентификации для user_id={user_id}. Детали: {e.detail}")
                await websocket.close(code=1008)
                return
            except Exception as e:
                logger.error(f"WebSocket: Непредвиденная ошибка при получении пользователя {user_id}: {e}")
                await websocket.close(code=1011)
                return

            await ws_manager.connect(websocket, user.user_id)

            chat = await db.get_active_chat(user.user_id)
            current_chat_id = None
            print(f"chat: {chat}")
            if chat:
                logger.info(
                    f"Найден активный чат {chat.chat_id} для пользователя {user.user_id}, статус: {chat.status}")

                if chat.status == "closed":
                    await db.reai_pending_chat(chat.chat_id)

                current_chat_id = chat.chat_id

                history = await db.get_chat_history(chat.chat_id, for_manager=False)
                history_payload = []

                for msg in history:
                    msg_data = {"text": msg.text, "sender_id": msg.sender_id, "timestamp": msg.timestamp.isoformat(), }

                    if msg.media:
                        msg_data["media"] = msg.media.dict()

                    history_payload.append(msg_data)

                show_buttons = False
                if history and history[-1].sender_id == "ai":
                    show_buttons = True

                await ws_manager.send_personal_message({"type": "init",
                                                        "payload": {"chat_id": chat.chat_id, "history": history_payload,
                                                            "status": chat.status, "show_buttons": show_buttons}},
                    user.user_id)
            else:

                logger.info(
                    f"Активный чат для пользователя {user.user_id} не найден. Будет создан при первом сообщении.")

                await ws_manager.send_personal_message(
                    {"type": "init", "payload": {"chat_id": None, "history": [], "status": "no_chat"}}, user.user_id)

            try:
                while True:
                    data = await websocket.receive_text()
                    logger.debug(f"WebSocket: Получено сообщение от {user.user_id}: {data}")
                    try:
                        message_data = json.loads(data)

                        if "type" not in message_data or "payload" not in message_data:
                            raise ValueError("Invalid message format")

                        if message_data["type"] == "message":
                            text = message_data["payload"].get("text", "")
                            file_info = message_data["payload"].get("file")

                            if file_info:

                                logger.debug(
                                    "Пропускаем отправку файла через WebSocket, так как он уже отправлен через upload_file")

                                last_message = await db.get_last_message(current_chat_id)
                                if last_message and last_message.media:

                                    await ws_manager.send_personal_message({"type": "message",
                                        "payload": {"chat_id": current_chat_id, "sender_id": str(user.user_id),
                                            "text": text, "timestamp": last_message.timestamp.isoformat(),
                                            "media": last_message.media.dict()}}, user.user_id)

                                    if chat.status == "active" and chat.topic_id:
                                        try:

                                            message_text = f"<b>Сообщение от клиента ({user.user_name}):</b>\n{text}"

                                            close_button = InlineKeyboardButton(text="✅ Завершить чат",
                                                                                callback_data=f"closechat_{chat.chat_id}")
                                            keyboard = InlineKeyboardMarkup(inline_keyboard=[[close_button]])

                                            file_url = minio_storage.get_presigned_url(last_message.media.file_id)

                                            if last_message.media.type == "photo":
                                                await tg_bot.send_photo(chat_id=MANAGER_GROUP_CHAT_ID, photo=file_url,
                                                    caption=message_text, message_thread_id=chat.topic_id,
                                                    parse_mode="HTML", reply_markup=keyboard)
                                            elif last_message.media.type == "video":
                                                await tg_bot.send_video(chat_id=MANAGER_GROUP_CHAT_ID, video=file_url,
                                                    caption=message_text, message_thread_id=chat.topic_id,
                                                    parse_mode="HTML", reply_markup=keyboard)
                                            elif last_message.media.type == "voice":
                                                await tg_bot.send_voice(chat_id=MANAGER_GROUP_CHAT_ID, voice=file_url,
                                                    caption=message_text, message_thread_id=chat.topic_id,
                                                    parse_mode="HTML", reply_markup=keyboard)
                                            elif last_message.media.type == "document":
                                                await tg_bot.send_document(chat_id=MANAGER_GROUP_CHAT_ID,
                                                    document=file_url, caption=message_text,
                                                    message_thread_id=chat.topic_id, parse_mode="HTML",
                                                    reply_markup=keyboard)

                                            logger.info(
                                                f"Медиа-сообщение от клиента {user.user_id} отправлено в чат менеджеров")
                                        except Exception as e:
                                            logger.error(f"Ошибка отправки медиа-сообщения в чат менеджеров: {e}")
                            else:

                                if not current_chat_id:

                                    existing_chat = await db.get_active_chat(user.user_id)
                                    if existing_chat:
                                        current_chat_id = existing_chat.chat_id

                                        if existing_chat.status == "closed":
                                            await db.reopen_chat(current_chat_id)
                                    else:

                                        chat = await db.create_chat(user.user_id)
                                        current_chat_id = chat.chat_id

                                    ai_response = await get_ai_response(text)

                                    client_msg = DbMessage(chat_id=current_chat_id, sender_id=str(user.user_id),
                                        text=text)
                                    await db.add_message(client_msg)

                                    if ai_response:

                                        ai_msg = DbMessage(chat_id=current_chat_id, sender_id="ai", text=ai_response)
                                        await db.add_message(ai_msg)

                                        await ws_manager.send_personal_message({"type": "ai_response",
                                            "payload": {"chat_id": current_chat_id, "sender_id": "ai",
                                                "text": ai_response, "timestamp": ai_msg.timestamp.isoformat(),
                                                "show_buttons": True}}, user.user_id)
                                    else:

                                        error_message = "К сожалению, возникла ошибка при обработке вашего запроса. Попробуйте позже или позовите оператора."
                                        await ws_manager.send_personal_message({"type": "error",
                                            "payload": {"chat_id": current_chat_id, "message": error_message,
                                                "show_operator_button": True}}, user.user_id)
                                else:

                                    chat = await db.get_chat_by_id(current_chat_id)
                                    if not chat or chat.status == "closed":
                                        logger.warning(
                                            f"WebSocket: Попытка отправить сообщение в несуществующий или закрытый чат {current_chat_id} от {user.user_id}")

                                        await ws_manager.send_personal_message({"type": "error", "payload": {
                                            "message": "Текущий чат завершен. Пожалуйста, начните новый чат.",
                                            "show_new_chat_button": True, "chat_id": current_chat_id}}, user.user_id)
                                        current_chat_id = None
                                        continue

                                    client_msg = DbMessage(chat_id=current_chat_id, sender_id=str(user.user_id),
                                                           text=text)
                                    await db.add_message(client_msg)

                                    if chat.status == "ai_pending":

                                        ai_response = await get_ai_response(text)

                                        if ai_response:

                                            ai_msg = DbMessage(chat_id=current_chat_id, sender_id="ai",
                                                               text=ai_response)
                                            await db.add_message(ai_msg)

                                            await ws_manager.send_personal_message({"type": "ai_response",
                                                "payload": {"chat_id": current_chat_id, "sender_id": "ai",
                                                    "text": ai_response, "timestamp": ai_msg.timestamp.isoformat(),
                                                    "show_buttons": True}}, user.user_id)
                                        else:

                                            error_message = "К сожалению, возникла ошибка при обработке вашего запроса. Попробуйте позже или позовите оператора."
                                            await ws_manager.send_personal_message({"type": "error",
                                                "payload": {"chat_id": current_chat_id, "message": error_message,
                                                    "show_operator_button": True}}, user.user_id)

                                    elif chat.status == "active" and chat.topic_id:
                                        try:

                                            message_text = f"<b>Сообщение от клиента ({user.user_name}):</b>\n{text}"

                                            close_button = InlineKeyboardButton(text="✅ Завершить чат",
                                                                                callback_data=f"closechat_{chat.chat_id}")
                                            keyboard = InlineKeyboardMarkup(inline_keyboard=[[close_button]])

                                            await tg_bot.send_message(chat_id=MANAGER_GROUP_CHAT_ID, text=message_text,
                                                message_thread_id=chat.topic_id, parse_mode="HTML",
                                                reply_markup=keyboard)
                                            logger.info(
                                                f"Сообщение от клиента {user.user_id} отправлено в чат менеджеров")
                                        except Exception as e:
                                            logger.error(f"Ошибка отправки сообщения в чат менеджеров: {e}")

                        elif message_data["type"] == "start_new_chat":

                            logger.info(f"WebSocket: Клиент {user.user_id} инициировал новый чат.")

                            chat = await db.get_active_chat(user.user_id)
                            await db.reai_pending_chat(chat.chat_id)

                            await ws_manager.send_personal_message({"type": "init",
                                                                    "payload": {"chat_id": chat.chat_id, "history": [],
                                                                                "status": "ai_pending"}}, user.user_id)
                        elif message_data["type"] == "message":
                            text = message_data["payload"].get("text", "")
                            file_info = message_data["payload"].get("file")
                            chat_id = message_data["payload"].get("chat_id")

                            if chat_id != current_chat_id:
                                logger.warning(f"Получено сообщение для старого чата {chat_id}, создаем новый")
                                chat = await db.create_chat(user.user_id)
                                current_chat_id = chat.chat_id



                    except json.JSONDecodeError:
                        logger.warning(f"WebSocket: Получены невалидные JSON данные от {user.user_id}: {data}")
                    except ValueError as e:
                        logger.warning(f"WebSocket: Неверный формат сообщения от {user.user_id}: {e} | Данные: {data}")
                    except Exception as e:
                        logger.error(f"WebSocket: Ошибка обработки сообщения от {user.user_id}: {e}")

                        await ws_manager.send_personal_message({"type": "error",
                                                                "payload": {"message": "Произошла ошибка на сервере.",
                                                                            "chat_id": current_chat_id}}, user.user_id)

            except WebSocketDisconnect:
                logger.info(f"WebSocket: Клиент {user.user_id} отключился.")
                ws_manager.disconnect(user.user_id)
            except Exception as e:
                logger.error(f"WebSocket: Непредвиденная ошибка в соединении с {user.user_id}: {e}")
                ws_manager.disconnect(user.user_id)

                try:
                    await websocket.close(code=1011)
                except RuntimeError:
                    pass

        except json.JSONDecodeError:
            logger.error("WebSocket: Ошибка парсинга JSON данных пользователя")
            await websocket.close(code=1008)
            return
        except Exception as e:
            logger.error(f"WebSocket: Ошибка проверки InitData: {e}")
            await websocket.close(code=1008)
            return

    except Exception as e:
        logger.error(f"WebSocket: Непредвиденная ошибка в соединении: {e}")
        await websocket.close(code=1011)
        return


if __name__ == "__main__":
    import uvicorn

    logger.info("Запуск FastAPI приложения через Uvicorn...")
    uvicorn.run("main:app", host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8000")), reload=True)


async def send_history_to_topic(topic_id: int, history: List[dict]):
    """Отправляет историю чата в топик"""
    try:
        formatted_history = []
        for msg in history:
            if msg["type"] == "user":
                formatted_history.append(f"👤 Клиент: {msg['text']}")
            elif msg["type"] == "system":
                formatted_history.append(f"🤖 Система: {msg['text']}")
            elif msg["type"] == "manager":
                formatted_history.append(f"👨‍💼 Менеджер: {msg['text']}")

        if formatted_history:
            history_text = "История чата:\n\n" + "\n\n".join(formatted_history)
            await tg_bot.send_message(chat_id=MANAGER_GROUP_CHAT_ID, message_thread_id=topic_id, text=history_text)
    except Exception as e:
        logger.error(f"Ошибка при отправке истории в топик {topic_id}: {str(e)}")
