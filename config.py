import os
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MONGO_CONNECTION_STRING_RAW = os.getenv("MONGO_CONNECTION_STRING")
DATABASE_NAME = os.getenv("DATABASE_NAME")
MANAGER_GROUP_CHAT_ID_RAW = os.getenv("MANAGER_GROUP_CHAT_ID")
AI_MODEL_API_KEY = os.getenv("AI_MODEL_API_KEY")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")

if not TELEGRAM_BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN не найден в .env")
    exit()

if not MONGO_CONNECTION_STRING_RAW:
    logger.error("MONGO_CONNECTION_STRING не найден в .env")
    exit()

MONGO_CONNECTION_STRING = MONGO_CONNECTION_STRING_RAW.split('#')[0].strip()

if not MANAGER_GROUP_CHAT_ID_RAW:
    logger.error("MANAGER_GROUP_CHAT_ID не найден в .env")
    exit()

try:

    MANAGER_GROUP_CHAT_ID = int(MANAGER_GROUP_CHAT_ID_RAW.split('#')[0].strip())
except ValueError:
    logger.error("MANAGER_GROUP_CHAT_ID должен быть числом.")
    exit()

logger.add("logs/app.log", rotation="10 MB", retention="7 days", level="INFO")
