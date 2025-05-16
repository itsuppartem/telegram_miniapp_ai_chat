import aiofiles
import asyncio
import io
import json
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, \
    ForumTopic, FSInputFile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import database as db
from config import TELEGRAM_BOT_TOKEN, MANAGER_GROUP_CHAT_ID, logger, ADMIN_USER_ID
from minio_storage import minio_storage
from models import Chat, Message as DbMessage, MediaContent
from utils import cleanup_chat_files
from websocket_manager import manager as ws_manager

bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"), parse_mode=ParseMode.HTML)
dp = Dispatcher()

message_counts = defaultdict(int)
last_reset = datetime.now(timezone.utc)


async def check_message_rate_limit(chat_id: int) -> bool:
    global last_reset
    if datetime.now(timezone.utc) - last_reset > timedelta(minutes=1):
        message_counts.clear()
        last_reset = datetime.now(timezone.utc)
    if message_counts[chat_id] >= 4:
        return False
    message_counts[chat_id] += 1
    return True


async def send_message_with_rate_limit(chat_id: int, text: str, **kwargs):
    if not await check_message_rate_limit(chat_id):
        logger.warning(f"Превышен лимит сообщений для чата {chat_id}")
        return False
    try:
        await asyncio.sleep(15)
        await bot.send_message(chat_id, text, **kwargs)
        return True
    except Exception as e:
        logger.error(f"Ошибка при отправке сообщения: {e}")
        return False


async def download_file_from_telegram(file_id: str, destination: str) -> bool:
    try:
        file = await bot.get_file(file_id)
        if not file:
            logger.error(f"Не удалось получить информацию о файле {file_id}")
            return False
        file_content = await bot.download_file(file.file_path)
        async with aiofiles.open(destination, 'wb') as f:
            await f.write(file_content.read())
        logger.info(f"Файл успешно скачан и сохранен: {destination}")
        return True
    except Exception as e:
        logger.error(f"Ошибка при скачивании файла {file_id}: {e}")
        return False


async def notify_client_chat_closed(user_id: int, chat_id: str):
    message_data = {"type": "status_update",
        "payload": {"status": "closed", "message": "Чат завершен оператором.", "show_new_chat_button": True,
            "chat_id": chat_id}}
    await ws_manager.send_personal_message(message_data, user_id)


async def send_message_to_client_ws(user_id: int, text: str, chat_id: str, manager_id: int):
    message_data = {"type": "message", "payload": {"sender_id": str(manager_id), "sender_type": "manager", "text": text,
        "timestamp": datetime.now(timezone.utc).isoformat(), "chat_id": chat_id}}
    await ws_manager.send_personal_message(message_data, user_id)


async def create_manager_chat_topic(user: db.User, chat: db.Chat) -> Optional[ForumTopic]:
    if not MANAGER_GROUP_CHAT_ID:
        logger.error("Невозможно создать топик: MANAGER_GROUP_CHAT_ID не задан.")
        return None
    try:
        topic_name = f"[АКТИВЕН] {user.user_name} (ID: {user.user_id})"
        topic = await bot.create_forum_topic(chat_id=MANAGER_GROUP_CHAT_ID, name=topic_name[:100])
        logger.info(f"Создан топик {topic.message_thread_id} для чата {chat.chat_id} с пользователем {user.user_id}")
        return topic
    except TelegramBadRequest as e:
        logger.error(f"Ошибка создания топика для чата {chat.chat_id}: {e}")
    except Exception as e:
        logger.error(f"Непредвиденная ошибка при создании топика: {e}")
    return None


async def send_history_to_topic(topic_id: int, chat_id: str):
    if not MANAGER_GROUP_CHAT_ID: return

    history = await db.get_chat_history(chat_id, limit=250, for_manager=True)
    if not history:
        await send_message_with_rate_limit(MANAGER_GROUP_CHAT_ID, "История переписки пуста.",
                                           message_thread_id=topic_id)
        return

    temp_file = None
    try:
        temp_file = f"history_{chat_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.txt"
        with open(temp_file, 'w', encoding='utf-8') as f:
            f.write(f"📜 История переписки для чата {chat_id}:\n\n")
            for msg in history:
                sender = f"👤 Клиент ({msg.sender_id})" if str(
                    msg.sender_id).isdigit() else f"👨‍💼 Оператор ({msg.sender_id})" if str(
                    msg.sender_id).isdigit() and int(msg.sender_id) == history[
                                                                                           0].sender_id else "🤖 AI" if msg.sender_id == "ai" else f"❓ Неизвестный ({msg.sender_id})"
                timestamp_str = msg.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')
                message_text = f"{sender} [{timestamp_str}]:\n{msg.text}\n"
                if msg.media:
                    message_text += f"📎 Прикреплен файл: {msg.media.type}\n"
                    if msg.media.caption:
                        message_text += f"Подпись: {msg.media.caption}\n"
                f.write(message_text + "\n")
        await bot.send_document(chat_id=MANAGER_GROUP_CHAT_ID,
            document=FSInputFile(temp_file, filename=f"history_{chat_id}.txt"),
            caption=f"📜 История переписки для чата {chat_id}", message_thread_id=topic_id)
    except Exception as e:
        logger.error(f"Ошибка при отправке истории чата: {e}")
        await send_message_with_rate_limit(MANAGER_GROUP_CHAT_ID, f"❌ Ошибка при отправке истории чата: {str(e)}",
            message_thread_id=topic_id)
    finally:
        if temp_file and os.path.exists(temp_file):
            try:
                os.remove(temp_file)
                logger.debug(f"Временный файл {temp_file} удален")
            except Exception as e:
                logger.error(f"Ошибка при удалении временного файла {temp_file}: {e}")


@dp.message(CommandStart())
async def send_welcome(message: Message):
    user_id = message.from_user.id
    user_name = message.from_user.full_name
    await db.find_or_create_user(user_id, user_name)
    web_app_url = os.getenv("WEB_APP_URL")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть чат ВАША КОМПАНИЯ", web_app=types.WebAppInfo(url=web_app_url))]])
    await message.answer(f"Здравствуйте, {user_name}! Нажмите кнопку ниже, чтобы начать чат.", reply_markup=keyboard)


@dp.message(Command("addmanager"))
async def add_manager_command(message: Message):
    if str(message.from_user.id) != db.ADMIN_USER_ID:
        return await message.reply("У вас нет прав для выполнения этой команды.")
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        return await message.reply("Использование: /addmanager <user_id> [Имя]")
    manager_id = int(args[1])
    manager_name = " ".join(args[2:]) if len(args) > 2 else None
    if await db.is_manager(manager_id):
        return await message.reply(f"Пользователь {manager_id} уже является менеджером.")
    await db.add_manager(manager_id, manager_name)
    await message.reply(f"Пользователь {manager_id} ({manager_name or 'Без имени'}) успешно добавлен как менеджер.")


@dp.message(F.chat.id == MANAGER_GROUP_CHAT_ID, F.message_thread_id)
async def handle_manager_message(message: types.Message):
    manager_id = int(message.from_user.id)
    if not await db.is_manager(manager_id):
        logger.warning(f"Пользователь {manager_id} попытался отправить сообщение в группу менеджеров")
        return
    chat = await db.get_chat_by_topic_id(message.message_thread_id)
    if not chat:
        logger.warning(f"Не найден чат для топика {message.message_thread_id}")
        return
    if not chat.manager_id and chat.manager_requested == True:
        try:
            from main import handle_take_chat
            await handle_take_chat(chat.chat_id, manager_id)
            logger.info(f"Чат {chat.chat_id} автоматически взят менеджером {manager_id} при первом сообщении")
        except Exception as e:
            logger.error(f"Ошибка при автоматическом взятии чата {chat.chat_id}: {e}")
            return
    db_message = DbMessage(chat_id=chat.chat_id, sender_id=str(manager_id), text=message.text or message.caption)
    if message.photo:
        file_id = message.photo[-1].file_id
        file_name = f"{chat.chat_id}_{datetime.now(timezone.utc).timestamp()}.jpg"
        minio_path = f"{chat.chat_id}/{file_name}"
        db_message.media = MediaContent(type="photo", file_id=minio_path, caption=message.caption,
            width=message.photo[-1].width, height=message.photo[-1].height)
    elif message.video:
        file_id = message.video.file_id
        file_name = f"{chat.chat_id}_{datetime.now(timezone.utc).timestamp()}.mp4"
        minio_path = f"{chat.chat_id}/{file_name}"
        db_message.media = MediaContent(type="video", file_id=minio_path, caption=message.caption,
            mime_type=message.video.mime_type, file_size=message.video.file_size, duration=message.video.duration,
            width=message.video.width, height=message.video.height)
    elif message.voice:
        file_id = message.voice.file_id
        file_name = f"{chat.chat_id}_{datetime.now(timezone.utc).timestamp()}.ogg"
        minio_path = f"{chat.chat_id}/{file_name}"
        db_message.media = MediaContent(type="voice", file_id=minio_path, mime_type=message.voice.mime_type,
            file_size=message.voice.file_size, duration=message.voice.duration)
    elif message.video_note:
        file_id = message.video_note.file_id
        file_name = f"{chat.chat_id}_{datetime.now(timezone.utc).timestamp()}.mp4"
        minio_path = f"{chat.chat_id}/{file_name}"
        db_message.media = MediaContent(type="video_note", file_id=minio_path, file_size=message.video_note.file_size,
            duration=message.video_note.duration)
    elif message.document:
        file_id = message.document.file_id
        file_name = f"{chat.chat_id}_{message.document.file_name}"
        minio_path = f"{chat.chat_id}/{file_name}"
        db_message.media = MediaContent(type="document", file_id=minio_path, caption=message.caption,
            mime_type=message.document.mime_type, file_size=message.document.file_size)
    if db_message.media and file_id:
        try:
            file = await bot.get_file(file_id)
            file_content = await bot.download_file(file.file_path)
            file_bytes = file_content.read()
            minio_storage.client.put_object(minio_storage.bucket_name, minio_path, io.BytesIO(file_bytes),
                len(file_bytes), content_type=db_message.media.mime_type)
            logger.info(f"Файл успешно загружен в MinIO: {minio_path}")
        except Exception as e:
            logger.error(f"Ошибка при загрузке файла в MinIO: {e}")
            return
    await db.add_message(db_message)
    message_data = {"type": "message",
        "payload": {"chat_id": chat.chat_id, "sender_id": str(manager_id), "sender_type": "manager",
            "text": db_message.text, "timestamp": db_message.timestamp.isoformat()}}
    if db_message.media:
        message_data["payload"]["media"] = db_message.media.dict()
    await ws_manager.send_personal_message(message_data, chat.user_id)


@dp.callback_query(F.data.startswith("takechat_"))
async def handle_take_chat(callback_query: types.CallbackQuery):
    try:
        manager_id = callback_query.from_user.id
        if not await db.is_manager(manager_id):
            logger.warning(f"Попытка взять чат не-менеджером: {manager_id}")
            await callback_query.answer("У вас нет прав для выполнения этого действия", show_alert=True)
            return
        chat_id = int(callback_query.data.split(":")[1])
        chat = await db.get_chat_by_id(chat_id)
        if not chat:
            logger.error(f"Чат {chat_id} не найден")
            await callback_query.answer("Ошибка: чат не найден", show_alert=True)
            return
        if chat.manager_id and chat.manager_id != manager_id:
            logger.warning(f"Попытка взять чат {chat_id}, который уже обрабатывается другим менеджером")
            await callback_query.answer("Этот чат уже обрабатывается другим менеджером", show_alert=True)
            return
        if chat.status not in ["manager_requested", "active"]:
            logger.warning(f"Попытка взять чат {chat_id} в неверном статусе: {chat.status}")
            await callback_query.answer("Этот чат недоступен для обработки", show_alert=True)
            return
        user = await db.get_user_by_id(chat.user_id)
        if not user:
            logger.error(f"Пользователь {chat.user_id} не найден")
            await callback_query.answer("Ошибка: пользователь не найден", show_alert=True)
            return
        if not chat.topic_id:
            logger.error(f"У чата {chat_id} отсутствует topic_id")
            await callback_query.answer("Ошибка: топик чата не найден", show_alert=True)
            return
        success = await db.update_chat_status(chat.id, "active", manager_id=manager_id)
        if not success:
            logger.error(f"Не удалось обновить статус чата {chat.id}")
            await callback_query.answer("Произошла ошибка при обработке запроса", show_alert=True)
            return
        manager = await db.get_user_by_id(manager_id)
        manager_name = manager.name if manager else "Менеджер"
        try:
            await bot.send_message(chat.user_id,
                f"👋 К вам подключился менеджер {manager_name}. Теперь вы можете общаться с ним.")
        except Exception as e:
            logger.error(f"Не удалось отправить сообщение пользователю {chat.user_id}: {e}")
        try:
            await callback_query.message.edit_text(f"✅ Чат взят менеджером {manager_name}\n"
                                                   f"Клиент: {user.name}\n"
                                                   f"ID чата: {chat.id}", reply_markup=None)
        except Exception as e:
            logger.error(f"Не удалось обновить сообщение в группе менеджеров: {e}")
        await callback_query.answer("Вы успешно взяли чат", show_alert=True)
    except ValueError as e:
        logger.error(f"Ошибка при обработке callback_data: {e}")
        await callback_query.answer("Неверный формат данных", show_alert=True)
    except Exception as e:
        logger.error(f"Ошибка при взятии чата: {e}")
        await callback_query.answer("Произошла непредвиденная ошибка", show_alert=True)


@dp.callback_query(F.data.startswith("closechat_"))
async def handle_close_chat(callback: CallbackQuery):
    manager_id = callback.from_user.id
    if not await db.is_manager(manager_id):
        await callback.answer("У вас нет прав для этого действия.", show_alert=True)
        return
    try:
        chat_id = callback.data.split("_")[1]
    except IndexError:
        logger.error(f"Некорректный callback_data для closechat: {callback.data}")
        await callback.answer("Ошибка обработки запроса.", show_alert=True)
        return
    await callback.answer("Завершаю чат...")
    chat = await db.get_chat_by_id(chat_id)
    if not chat:
        logger.warning(f"Менеджер {manager_id} попытался закрыть несуществующий чат {chat_id}")
        await callback.message.reply("Ошибка: Чат не найден.")
        return
    if chat.status == "closed":
        logger.warning(f"Менеджер {manager_id} попытался закрыть уже закрытый чат {chat_id}")
        await callback.message.reply("Этот чат уже закрыт.")
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception as e:
            logger.debug(f"Не удалось убрать кнопку у сообщения {callback.message.message_id}: {e}")
        return
    topic_id = chat.topic_id
    success = await db.update_chat_status(chat_id, "closed", keep_topic_id=True)
    if not success:
        logger.error(f"Не удалось обновить статус чата {chat_id} на 'closed' в БД")
        await callback.message.reply("Ошибка базы данных при закрытии чата.")
        return
    await db.reset_chat_manager(chat_id)
    await notify_client_chat_closed(chat.user_id, chat.chat_id)
    await cleanup_chat_files(chat.chat_id)
    if topic_id and MANAGER_GROUP_CHAT_ID:
        try:
            current_name = callback.message.reply_to_message.forum_topic_created.name if callback.message.reply_to_message and callback.message.reply_to_message.forum_topic_created else 'Чат'
            if not current_name.startswith('[ЗАКРЫТ]'):
                await bot.edit_forum_topic(MANAGER_GROUP_CHAT_ID, topic_id,
                    name=current_name.replace('[АКТИВЕН] ', '[ЗАКРЫТ] '))
            logger.info(f"Изменено название топика {topic_id} для чата {chat_id} на закрытый")
            await bot.send_message(MANAGER_GROUP_CHAT_ID, f"✅ Чат завершен менеджером {callback.from_user.full_name}.",
                message_thread_id=topic_id)
        except Exception as e:
            logger.error(f"Ошибка при изменении названия топика {topic_id}: {e}")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception as e:
        logger.debug(f"Не удалось убрать кнопку у сообщения {callback.message.message_id}: {e}")
    logger.info(f"Чат {chat_id} успешно завершен менеджером {manager_id}.")
    await callback.message.reply(f"✅ Чат {chat_id} завершен.")


async def notify_managers_new_request(user: db.User, chat: db.Chat, first_message: str, topic_id: int) -> bool:
    if not MANAGER_GROUP_CHAT_ID:
        logger.error("MANAGER_GROUP_CHAT_ID не установлен")
        return False
    try:
        logger.info(f"Отправка уведомления менеджерам о новом запросе от пользователя {user.user_id}")
        try:
            await bot.edit_forum_topic(MANAGER_GROUP_CHAT_ID, topic_id,
                name=f"[АКТИВЕН] {user.user_name} (ID: {user.user_id})")
            logger.info(f"Изменено название топика {topic_id} на активный")
        except Exception as e:
            logger.warning(f"Не удалось изменить название топика {topic_id}: {e}")
        await send_history_to_topic(topic_id, chat.chat_id)
        message = (f"🔔 Новый запрос от клиента!\n\n"
                   f"📝 Тема: {f'[АКТИВЕН] {user.user_name} (ID: {user.user_id})'}\n"
                   f"👤 Клиент: @{user.user_name}\n" + (f"📱 Телефон: {user.phone}\n" if user.phone else "") + (
                       f"📌 Источник: {user.source}\n" if user.source else "") + f"🌐 Язык: {user.language}\n"
                                                                                f"💰 Валюта: {user.currency}\n"
                                                                                f"💬 Сообщение: {first_message}\n\n"
                                                                                f"🆔 ID чата: {chat.chat_id}\n"
                                                                                f"⏰ Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="✅ Завершить чат", callback_data=f"closechat_{chat.chat_id}")]])
        await bot.send_message(chat_id=MANAGER_GROUP_CHAT_ID, text=message, reply_markup=keyboard,
            message_thread_id=topic_id)
        return True
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления менеджерам: {e}")
        return False


async def handle_request_manager(message: types.Message):
    try:
        chat = await db.get_chat_by_id(message.chat.id)
        if not chat or chat.status == "closed":
            logger.error(f"Чат {message.chat.id} не найден или закрыт")
            await message.reply("Ошибка: чат не найден или уже закрыт.")
            return
        user = await db.get_user_by_id(message.from_user.id)
        if not user:
            logger.error(f"Пользователь {message.from_user.id} не найден")
            await message.reply("Ошибка: пользователь не найден.")
            return
        topic = await create_manager_chat_topic(user, chat)
        if not topic:
            logger.error(f"Не удалось создать топик для чата {chat.id}")
            await message.reply("Произошла ошибка при создании чата с менеджером. Попробуйте позже.")
            return
        success = await db.update_chat_status(chat.id, "manager_requested", topic_id=topic.message_thread_id)
        if not success:
            logger.error(f"Не удалось обновить статус чата {chat.id} в БД")
            try:
                await bot.delete_forum_topic(MANAGER_GROUP_CHAT_ID, topic.message_thread_id)
            except Exception as e:
                logger.error(f"Не удалось удалить топик {topic.message_thread_id} после ошибки обновления БД: {e}")
            await message.reply("Произошла ошибка при обработке запроса. Попробуйте позже.")
            return
        success = await notify_managers_new_request(user, chat, message.text, topic.message_thread_id)
        if not success:
            logger.error(f"Не удалось отправить уведомление менеджерам для чата {chat.id}")
            await message.reply("Произошла ошибка при уведомлении менеджеров. Попробуйте позже.")
            return
        await message.reply("✅ Запрос отправлен менеджеру. Пожалуйста, ожидайте ответа.")
    except Exception as e:
        logger.error(f"Ошибка при обработке запроса менеджера: {e}")
        await message.reply("Произошла непредвиденная ошибка. Попробуйте позже.")


async def check_and_send_reminders():
    try:
        active_chats = await db.get_active_chats()
        if not active_chats:
            return
        for chat in active_chats:
            if chat.status == "active" and chat.topic_id:
                last_message = await db.get_last_message(chat.chat_id)
                if not last_message:
                    continue
                if str(last_message.sender_id).isdigit() and await db.is_manager(int(last_message.sender_id)):
                    time_since_last_message = datetime.now(timezone.utc) - last_message.timestamp.replace(
                        tzinfo=timezone.utc)
                    if time_since_last_message > timedelta(hours=12):
                        await send_message_with_rate_limit(MANAGER_GROUP_CHAT_ID,
                            "⏰ Напоминание: диалог не завершен. Пожалуйста, завершите диалог или получите ответ от клиента.",
                            message_thread_id=chat.topic_id)
                        logger.info(f"Отправлено напоминание для чата {chat.chat_id} в топик {chat.topic_id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке напоминаний: {e}")


async def run_bot():
    logger.info("Запуск Telegram бота...")

    async def reminder_checker():
        while True:
            now = datetime.now(timezone.utc)
            if now.hour == 9 and now.minute == 0:
                await check_and_send_reminders()
            await asyncio.sleep(60)

    asyncio.create_task(reminder_checker())
    await dp.start_polling(bot)
