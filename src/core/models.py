from datetime import datetime, UTC
from typing import List, Optional, Union, Dict
from pydantic import BaseModel, Field
from bson import ObjectId
import json
from enum import Enum

class MessageType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    FILE = "file"
    EMOJI = "emoji"
    VOICE = "voice"

class MessageBase(BaseModel):
    content: Union[str, Dict]  # Can be string for text/emoji or dict for media metadata
    type: str = "direct"  # direct or group
    content_type: MessageType = MessageType.TEXT
    media_url: Optional[str] = None
    media_metadata: Optional[Dict] = None  # For storing file size, dimensions, duration, etc.

class Message(MessageBase):
    from_user_id: str
    timestamp: datetime
    id: str = Field(default_factory=lambda: str(ObjectId()))
    status: str = "sent"  # sent, delivered, read
    read_by: List[str] = []  # List of user IDs who have read the message
    deleted_for: List[str] = []  # List of user IDs who have deleted this message
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            ObjectId: str
        }

class GroupCreate(BaseModel):
    name: str
    member_ids: List[str]
    created_by: str

class Group(GroupCreate):
    id: str = Field(default_factory=lambda: str(ObjectId()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    messages: List[Message] = []

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            ObjectId: str
        }

class JSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, ObjectId):
            return str(o)
        if isinstance(o, datetime):
            return o.isoformat()
        return json.JSONEncoder.default(self, o)
