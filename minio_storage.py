import io
import os
from datetime import timedelta
from minio import Minio
from minio.error import S3Error

from config import logger


class MinioStorage:
    def __init__(self, endpoint, access_key, secret_key, secure=False):
        try:
            self.client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
            self.bucket_name = "vroom-chat"
            self._ensure_bucket_exists()
            logger.info(f"MinIO клиент успешно инициализирован для {endpoint}")
        except Exception as e:
            logger.error(f"Ошибка инициализации MinIO клиента: {e}")
            raise

    def _ensure_bucket_exists(self):
        try:
            if not self.client.bucket_exists(self.bucket_name):
                self.client.make_bucket(self.bucket_name)
                logger.info(f"Создан бакет {self.bucket_name}")
            else:
                logger.info(f"Бакет {self.bucket_name} уже существует")
        except S3Error as e:
            logger.error(f"Ошибка при создании бакета: {e}")
            raise

    async def upload_file(self, file_path: str, object_name: str) -> str:
        try:
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"Файл {file_path} не найден")

            file_size = os.path.getsize(file_path)
            logger.info(f"Размер файла {file_path}: {file_size} байт")

            content_type = "application/octet-stream"
            ext = os.path.splitext(file_path)[1].lower()
            if ext == '.png':
                content_type = 'image/png'
            elif ext == '.jpg' or ext == '.jpeg':
                content_type = 'image/jpeg'
            elif ext == '.gif':
                content_type = 'image/gif'
            elif ext == '.pdf':
                content_type = 'application/pdf'
            elif ext == '.doc':
                content_type = 'application/msword'
            elif ext == '.docx':
                content_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'

            self.client.fput_object(self.bucket_name, object_name, file_path, content_type=content_type)

            try:
                self.client.stat_object(self.bucket_name, object_name)
            except Exception as e:
                logger.error(f"Файл {object_name} не найден в MinIO после загрузки: {e}")
                raise Exception("Файл не был успешно загружен в MinIO")

            return f"/api/media/{object_name}"

        except S3Error as e:
            logger.error(f"Ошибка при загрузке файла в MinIO: {e}")
            raise
        except Exception as e:
            logger.error(f"Неожиданная ошибка при загрузке файла: {e}")
            raise

    async def download_file(self, object_name: str, destination_path: str) -> bool:
        try:
            try:
                self.client.stat_object(self.bucket_name, object_name)
            except S3Error as e:
                logger.error(f"Объект {object_name} не найден в MinIO: {e}")
                return False

            os.makedirs(os.path.dirname(destination_path), exist_ok=True)

            self.client.fget_object(self.bucket_name, object_name, destination_path)
            logger.info(f"Файл {object_name} успешно скачан в {destination_path}")
            return True
        except S3Error as e:
            logger.error(f"Ошибка при скачивании файла из MinIO: {e}")
            return False
        except Exception as e:
            logger.error(f"Непредвиденная ошибка при скачивании файла: {e}")
            return False

    def get_presigned_url(self, object_name: str, expires: int = 3600) -> str:
        try:
            try:
                self.client.stat_object(self.bucket_name, object_name)
            except S3Error as e:
                logger.error(f"Объект {object_name} не найден в MinIO: {e}")
                raise

            url = self.client.presigned_get_object(self.bucket_name, object_name, expires=timedelta(seconds=expires))
            logger.info(f"Сгенерирована временная ссылка для {object_name}")
            return url
        except S3Error as e:
            logger.error(f"Ошибка при генерации временной ссылки: {e}")
            raise
        except Exception as e:
            logger.error(f"Непредвиденная ошибка при генерации ссылки: {e}")
            raise

    async def delete_file(self, object_name: str) -> bool:
        try:
            try:
                self.client.stat_object(self.bucket_name, object_name)
            except S3Error as e:
                logger.error(f"Объект {object_name} не найден в MinIO: {e}")
                return False

            self.client.remove_object(self.bucket_name, object_name)
            logger.info(f"Файл {object_name} успешно удален из MinIO")
            return True
        except S3Error as e:
            logger.error(f"Ошибка при удалении файла из MinIO: {e}")
            return False
        except Exception as e:
            logger.error(f"Непредвиденная ошибка при удалении файла: {e}")
            return False


minio_storage = MinioStorage(endpoint=os.getenv("MINIO_ENDPOINT"), access_key=os.getenv("MINIO_ACCESS_KEY"),
    secret_key=os.getenv("MINIO_SECRET_KEY"), secure=True)
