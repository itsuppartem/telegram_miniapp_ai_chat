import os
import shutil

import database as db
from config import logger
from minio_storage import minio_storage


async def cleanup_chat_files(chat_id: str):
    """Очищает все файлы, связанные с чатом"""
    try:

        objects = minio_storage.client.list_objects(minio_storage.bucket_name, prefix=f"{chat_id}/")

        for obj in objects:
            try:
                await minio_storage.delete_file(obj.object_name)
            except Exception as e:
                logger.error(f"Ошибка при удалении файла {obj.object_name} из MinIO: {e}")


    except Exception as e:
        logger.error(f"Ошибка при очистке файлов чата {chat_id}: {e}")
        raise
