import json
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, \
    ForumTopic, FSInputFile
from datetime import datetime
from fastapi import WebSocket
from typing import Dict

from config import logger
from minio_storage import minio_storage

bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, WebSocket] = {}
        logger.info("Менеджер WebSocket соединений инициализирован.")

    async def connect(self, websocket: WebSocket, user_id: int):
        await websocket.accept()
        self.active_connections[user_id] = websocket
        logger.info(f"Клиент {user_id} подключился через WebSocket.")

    def disconnect(self, user_id: int):
        if user_id in self.active_connections:
            del self.active_connections[user_id]
            logger.info(f"Клиент {user_id} отключился от WebSocket.")

    def _serialize_datetime(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return obj

    async def send_personal_message(self, message: dict, user_id: int):
        if user_id in self.active_connections:
            websocket = self.active_connections[user_id]
            try:
                serialized_message = json.dumps(message, default=self._serialize_datetime)
                await websocket.send_text(serialized_message)
                logger.debug(f"Сообщение отправлено клиенту {user_id} через WebSocket: {message}")
            except Exception as e:
                logger.error(f"Ошибка отправки сообщения клиенту {user_id} через WebSocket: {e}")
                await self._send_telegram_message(user_id, message)
        else:
            logger.warning(f"Попытка отправки сообщения отключенному клиенту {user_id}")
            await self._send_telegram_message(user_id, message)

    async def _send_telegram_message(self, user_id: int, message: dict):
        try:
            if message["type"] == "message":
                text = f"🔔 У вас новое сообщение в чате!\n\n"
                if message["payload"].get("text"):
                    text += f"{message['payload']['text']}\n\n"
                text += "Откройте чат, чтобы ответить."
            elif message["type"] == "status_update":
                text = f"🔔 {message['payload']['message']}\n\nОткройте чат, чтобы продолжить."
            else:
                text = f"🔔 У вас новое уведомление в чате!\n\nОткройте чат, чтобы продолжить."

            web_app_url = os.getenv("WEB_APP_URL")
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Открыть чат ВАША КОМПАНИЯ", web_app=types.WebAppInfo(url=web_app_url))]])

            if message["type"] == "message" and "media" in message["payload"]:
                media = message["payload"]["media"]
                file_url = minio_storage.get_presigned_url(media["file_id"])

                if media["type"] == "photo":
                    await bot.send_photo(chat_id=user_id, photo=file_url, caption=text, reply_markup=keyboard)
                elif media["type"] == "video":
                    await bot.send_video(chat_id=user_id, video=file_url, caption=text, reply_markup=keyboard)
                elif media["type"] == "voice":
                    await bot.send_voice(chat_id=user_id, voice=file_url, caption=text, reply_markup=keyboard)
                elif media["type"] == "video_note":
                    await bot.send_video_note(chat_id=user_id, video_note=file_url)
                    await bot.send_message(chat_id=user_id, text=text, reply_markup=keyboard)
                elif media["type"] == "document":
                    await bot.send_document(chat_id=user_id, document=file_url, caption=text, reply_markup=keyboard)
            else:
                await bot.send_message(chat_id=user_id, text=text, parse_mode="HTML", reply_markup=keyboard)

            logger.info(f"Сообщение отправлено клиенту {user_id} через Telegram")
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения клиенту {user_id} через Telegram: {e}")

    async def broadcast(self, message: str):
        for user_id, connection in self.active_connections.items():
            try:
                await connection.send_text(message)
            except Exception as e:
                logger.error(f"Ошибка широковещательной отправки клиенту {user_id}: {e}")


manager = ConnectionManager()
