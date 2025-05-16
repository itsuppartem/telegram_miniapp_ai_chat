import uuid
from datetime import datetime, timezone
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Literal, Union, Dict, List, Any


class User(BaseModel):
    user_id: int
    user_name: Optional[str] = "Unknown"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    language: str = "rus"
    currency: str = "EUR"
    is_active: bool = True
    updated_at: Optional[datetime] = None
    disabled_at: Optional[datetime] = None
    orders_history: Optional[List[Any]] = None
    source: Optional[str] = None
    phone: Optional[str] = None

    def get_user_id(self) -> int:
        return self.user_id

    model_config = ConfigDict(populate_by_name=True)


class Chat(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    chat_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: int
    manager_id: Optional[int] = None
    status: Literal["ai_pending", "active", "closed"] = "ai_pending"
    topic_id: Optional[int] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    closed_at: Optional[datetime] = None
    reopened_at: Optional[datetime] = None
    manager_requested: bool = False

    model_config = ConfigDict(populate_by_name=True)


class MediaContent(BaseModel):
    type: Literal["photo", "video", "voice", "video_note", "document"]
    file_id: str
    caption: Optional[str] = None
    mime_type: Optional[str] = None
    file_size: Optional[int] = None
    duration: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None


class Message(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    chat_id: str
    sender_id: str
    text: Optional[str] = None
    media: Optional[MediaContent] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(populate_by_name=True)


class Manager(BaseModel):
    user_id: int
    name: Optional[str] = None


class WebSocketMessage(BaseModel):
    type: Literal["message", "status_update", "error", "init", "ai_response"]
    payload: dict


class UserInfo(BaseModel):
    user_id: int
    user_name: Optional[str] = None
