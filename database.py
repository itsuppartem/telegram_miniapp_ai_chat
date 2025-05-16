from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from typing import Optional, List, Dict, Any, Literal

from config import MONGO_CONNECTION_STRING, DATABASE_NAME, logger, ADMIN_USER_ID
from models import User, Chat, Message, Manager

client: AsyncIOMotorClient = None
db: AsyncIOMotorDatabase = None


async def connect_db():
    global client, db
    logger.info("Подключение к MongoDB...")
    try:
        client = AsyncIOMotorClient(MONGO_CONNECTION_STRING)
        db = client[DATABASE_NAME]
        await client.admin.command('ping')
        logger.info("Успешное подключение к MongoDB")

        try:

            await db.chats.create_index("topic_id")

            await db.chats.create_index("chat_id", unique=True)

            await db.chats.create_index("user_id")

            await db.chats.create_index("manager_id")

            await db.chats.create_index("status")
            logger.info("Созданы необходимые индексы для коллекции chats")
        except Exception as e:
            logger.error(f"Ошибка при создании индексов: {e}")

        logger.info("Индексы MongoDB проверены/созданы.")

        if ADMIN_USER_ID and await db.managers.count_documents({}) == 0:
            try:
                admin_id = int(ADMIN_USER_ID)
                await add_manager(admin_id, "Admin")
                logger.info(f"Добавлен первый менеджер с ID: {admin_id}")
            except ValueError:
                logger.error("ADMIN_USER_ID в .env должен быть числом.")
            except Exception as e:
                logger.error(f"Не удалось добавить первого менеджера: {e}")


    except Exception as e:
        logger.error(f"Не удалось подключиться к MongoDB: {e}")
        raise


async def close_db():
    global client
    if client:
        client.close()
        logger.info("Соединение с MongoDB закрыто.")


async def get_user(user_id: int) -> Optional[User]:
    user_data = await db.users.find_one({"user_id": user_id})
    return User(**user_data) if user_data else None


async def find_or_create_user(user_id: int, user_name: Optional[str] = None) -> User:
    """Находит или создает пользователя с указанным ID"""
    user = await get_user(user_id)
    if not user:
        logger.info(f"Создание нового пользователя: ID {user_id}")
        new_user = User(user_id=user_id, user_name=user_name or f"User_{user_id}",
            created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
        await db.users.insert_one(new_user.dict())
        return new_user
    elif user_name and user.user_name != user_name:
        await db.users.update_one({"user_id": user_id},
            {"$set": {"user_name": user_name, "updated_at": datetime.now(timezone.utc)}})
        user.user_name = user_name
    return user


async def create_chat(user_id: int) -> Chat:
    user = await get_user(user_id)
    if not user:
        raise ValueError(f"Пользователь с ID {user_id} не найден")

    new_chat = Chat(user_id=user.user_id, status="ai_pending")
    await db.chats.insert_one(new_chat.dict(by_alias=True))
    logger.info(f"Создан новый чат {new_chat.chat_id} для пользователя {user_id}")
    return new_chat


async def get_active_chat(user_id: int) -> Optional[Chat]:
    chat_data = await db.chats.find_one({"user_id": user_id}, sort=[("created_at", -1)])
    return Chat(**chat_data) if chat_data else None


async def get_chat_by_id(chat_id: str) -> Optional[Chat]:
    chat_data = await db.chats.find_one({"chat_id": chat_id})

    return Chat.parse_obj(chat_data) if chat_data else None


async def get_chat_by_topic_id(topic_id: int) -> Optional[Chat]:
    chat_data = await db.chats.find_one({"topic_id": topic_id})
    return Chat.parse_obj(chat_data) if chat_data else None


async def add_message(message: Message) -> None:
    await db.chat_messages.insert_one(message.dict(by_alias=True))
    logger.debug(f"Сообщение добавлено в чат {message.chat_id}")


async def get_chat_history(chat_id: str, limit: int = 50, for_manager: bool = False) -> List[Message]:
    if for_manager:

        messages_cursor = db.chat_messages.find({"chat_id": chat_id}).sort("timestamp", 1).limit(limit)
    else:

        chat = await get_chat_by_id(chat_id)
        if chat and chat.reopened_at:
            messages_cursor = db.chat_messages.find({"chat_id": chat_id, "timestamp": {"$gte": chat.reopened_at}}).sort(
                "timestamp", 1).limit(limit)
        else:
            messages_cursor = db.chat_messages.find({"chat_id": chat_id}).sort("timestamp", 1).limit(limit)

    messages_data = await messages_cursor.to_list(length=limit)
    return [Message.parse_obj(msg) for msg in messages_data]


async def get_chat_messages(chat_id: str) -> List[Message]:
    """Получает все сообщения чата"""
    messages_cursor = db.chat_messages.find({"chat_id": chat_id})
    messages_data = await messages_cursor.to_list(length=None)
    return [Message.parse_obj(msg) for msg in messages_data]


async def update_chat_status(chat_id: str, status: Literal["active", "closed"], manager_id: Optional[int] = None,
                             topic_id: Optional[int] = None, keep_topic_id: bool = False) -> bool:
    update_data: Dict[str, Any] = {"status": status}
    if status == "closed":
        update_data["closed_at"] = datetime.utcnow()
        if not keep_topic_id:
            update_data["topic_id"] = None
            update_data["manager_id"] = None
    if manager_id is not None:
        update_data["manager_id"] = manager_id
    if topic_id is not None:
        update_data["topic_id"] = topic_id

    result = await db.chats.update_one({"chat_id": chat_id}, {"$set": update_data})
    if result.modified_count > 0:
        logger.info(
            f"Статус чата {chat_id} обновлен на {status}. Manager_id: {manager_id}, Topic_id: {topic_id}, keep_topic_id: {keep_topic_id}")
        return True
    return False


async def set_manager_requested(chat_id: str, topic_id: Optional[int] = None) -> bool:
    update_data = {"manager_requested": True, "status": "active"}
    if topic_id is not None:
        update_data["topic_id"] = topic_id

    result = await db.chats.update_one({"chat_id": chat_id}, {"$set": update_data})
    return result.modified_count > 0


async def set_chat_manager(chat_id: str, manager_id: int) -> bool:
    result = await db.chats.update_one({"chat_id": chat_id}, {"$set": {"manager_id": manager_id}})
    return result.modified_count > 0


async def reset_chat_manager(chat_id: str) -> bool:
    result = await db.chats.update_one({"chat_id": chat_id}, {"$set": {"manager_id": None}})
    return result.modified_count > 0


async def is_manager(user_id: int) -> bool:
    manager = await db.managers.find_one({"user_id": user_id})
    return manager is not None


async def add_manager(user_id: int, name: Optional[str] = None) -> None:
    manager = Manager(user_id=user_id, name=name)
    try:
        await db.managers.insert_one(manager.dict())
        logger.info(f"Менеджер {user_id} ({name}) добавлен.")
    except Exception as e:
        logger.warning(f"Не удалось добавить менеджера {user_id}: {e}")


async def get_all_managers() -> List[Manager]:
    managers_cursor = db.managers.find({})
    managers_data = await managers_cursor.to_list(length=None)
    return [Manager(**m) for m in managers_data]


async def delete_media_file(file_id: str) -> bool:
    """Удаляет медиа-файл из базы данных"""
    try:
        result = await db.media_files.delete_one({"file_id": file_id})
        return result.deleted_count > 0
    except Exception as e:
        logger.error(f"Ошибка при удалении медиа-файла {file_id}: {e}")
        return False


async def reset_manager_requested(chat_id: str) -> bool:
    """Сбрасывает флаг запроса менеджера и topic_id"""
    result = await db.chats.update_one({"chat_id": chat_id}, {"$set": {"manager_requested": False, "topic_id": None}})
    return result.modified_count > 0


async def reopen_chat(chat_id: str, old_topic_id: Optional[int] = None) -> bool:
    """Переоткрывает закрытый чат"""

    chat = await get_chat_by_id(chat_id)
    if not chat:
        return False

    update_data = {"status": "active", "closed_at": None, "manager_requested": True if old_topic_id else False,
        "reopened_at": datetime.utcnow()}

    if old_topic_id:
        update_data["topic_id"] = old_topic_id
    elif chat.topic_id:
        update_data["topic_id"] = chat.topic_id

    result = await db.chats.update_one({"chat_id": chat_id}, {"$set": update_data})

    logger.info(
        f"Переоткрытие чата {chat_id}. Old topic_id: {old_topic_id}, Existing topic_id: {chat.topic_id}, Final topic_id: {update_data.get('topic_id')}")
    return result.modified_count > 0


async def reai_pending_chat(chat_id: str, old_topic_id: Optional[int] = None) -> bool:
    """Переоткрывает закрытый чат"""

    chat = await get_chat_by_id(chat_id)
    if not chat:
        return False

    update_data = {"status": "ai_pending", "closed_at": None, "manager_requested": False,
        "reopened_at": datetime.utcnow()}

    if old_topic_id:
        update_data["topic_id"] = old_topic_id
    elif chat.topic_id:
        update_data["topic_id"] = chat.topic_id

    result = await db.chats.update_one({"chat_id": chat_id}, {"$set": update_data})

    logger.info(
        f"Перенейронивание чата {chat_id}. Old topic_id: {old_topic_id}, Existing topic_id: {chat.topic_id}, Final topic_id: {update_data.get('topic_id')}")
    return result.modified_count > 0


async def get_active_chats() -> List[Chat]:
    """Получает все активные чаты"""
    chats_cursor = db.chats.find({"status": "active"})
    chats_data = await chats_cursor.to_list(length=None)
    return [Chat.parse_obj(chat) for chat in chats_data]


async def get_last_message(chat_id: str) -> Optional[Message]:
    """Получает последнее сообщение в чате"""
    message_data = await db.chat_messages.find_one({"chat_id": chat_id}, sort=[("timestamp", -1)])
    return Message.parse_obj(message_data) if message_data else None
