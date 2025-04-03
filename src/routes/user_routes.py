# FastAPI routes for REST endpoints
from fastapi import HTTPException, APIRouter, UploadFile, File
from bson import ObjectId

from core.database import messages_collection, get_db_stats
from core.models import JSONEncoder
import os
from fastapi.responses import FileResponse
import json
import aiofiles
import uuid
from datetime import datetime, UTC
from core.database import groups_collection
from core.socket_server import sio
from pymongo import UpdateMany
import asyncio
from core.message_buffer import message_buffer

user_router = APIRouter(tags=["User"])

# Add these constants at the top of the file
UPLOAD_DIR = "uploads"
MAX_UPLOAD_SIZE = 5 * 1024 * 1024  # 5MB limit
ALLOWED_EXTENSIONS = {
    "image": (".jpg", ".jpeg", ".png", ".gif"),
    "video": (".mp4", ".mov", ".avi"),
    "file": (".pdf", ".doc", ".docx"),
    "audio": (".mp3", ".wav", ".ogg", ".m4a", ".webm"),  # Add audio formats
}

# Create uploads directory if it doesn't exist
os.makedirs(UPLOAD_DIR, exist_ok=True)


@user_router.get("/messages/{user_id}/{other_id}")
async def get_messages(user_id: str, other_id: str, message_type: str = "direct", limit: int = 50):
    """Get chat history for direct messages or group messages"""
    try:
        # Add basic connection management
        for attempt in range(3):  # Try 3 times
            try:
                # Add caching for frequent requests
                cache_key = f"{user_id}:{other_id}:{message_type}:{limit}"
                
                # Create index hint for faster queries
                index_hint = None
                if message_type == "group":
                    query = {"type": "group", "group_id": ObjectId(other_id)}
                    index_hint = "group_messages"
                else:
                    query = {
                        "type": "direct",
                        "$or": [
                            {"from_user_id": user_id, "to_user_id": other_id},
                            {"from_user_id": other_id, "to_user_id": user_id},
                        ],
                    }
                    index_hint = "user_messages"

                # Use projection to limit fields returned
                projection = {
                    "_id": 1,
                    "content": 1,
                    "from_user_id": 1,
                    "timestamp": 1,
                    "type": 1,
                    "media_url": 1,
                    "status": 1
                }

                # Fetch messages with optimized query
                cursor = messages_collection.find(
                    query,
                    projection=projection,
                    # Add timeout to prevent long-running queries
                    max_time_ms=5000
                ).sort("timestamp", -1).limit(limit)
                
                if index_hint:
                    cursor = cursor.hint(index_hint)
                    
                messages = await cursor.to_list(length=None)
                break  # If successful, break the retry loop
            except Exception as e:
                if "Timed out while checking out a connection" in str(e) and attempt < 2:
                    await asyncio.sleep(0.5 * (attempt + 1))  # Progressive backoff
                    continue
                raise  # Re-raise if all attempts failed or different error

        # Batch update read status
        if messages:
            update_ops = []
            if message_type == "group":
                unread_ids = [msg["_id"] for msg in messages if user_id not in msg.get("read_by", [])]
                if unread_ids:
                    update_ops.append(UpdateMany(
                        {"_id": {"$in": unread_ids}},
                        {"$addToSet": {"read_by": user_id}}
                    ))
            else:
                unread_ids = [msg["_id"] for msg in messages 
                            if msg.get("to_user_id") == user_id and msg.get("status") != "read"]
                if unread_ids:
                    update_ops.append(UpdateMany(
                        {"_id": {"$in": unread_ids}},
                        {"$set": {"status": "read"}}
                    ))

            # Execute batch updates if needed
            if update_ops:
                await messages_collection.bulk_write(update_ops)

        return json.loads(json.dumps(messages, cls=JSONEncoder))
        
    except Exception as e:
        print(f"Error getting messages: {str(e)}")
        if "Timed out while checking out a connection" in str(e):
            raise HTTPException(
                status_code=503,
                detail="Server is experiencing high load, please try again"
            )
        raise HTTPException(status_code=500, detail=str(e))


@user_router.get("/")
async def get_chat():
    # Get the absolute path to test_chat.html
    html_path = "/home/muzammil/Downloads/chat_app/src/test_chat.html"
    return FileResponse(html_path)


@user_router.post("/upload/")
async def upload_file(file: UploadFile = File(...)):
    """Handle file uploads for chat messages"""
    try:
        # Check file size
        content = await file.read()
        if len(content) > MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Maximum size is {MAX_UPLOAD_SIZE/1024/1024}MB",
            )

        # Generate unique filename
        ext = os.path.splitext(file.filename)[1].lower()
        filename = f"{uuid.uuid4()}{ext}"
        file_path = os.path.join(UPLOAD_DIR, filename)

        # Determine content type
        content_type = None
        for type_, extensions in ALLOWED_EXTENSIONS.items():
            if ext in extensions:
                content_type = type_
                break

        if not content_type:
            raise HTTPException(status_code=400, detail="Unsupported file type")

        # Save file
        async with aiofiles.open(file_path, "wb") as f:
            await f.write(content)

        # Generate URL for the uploaded file
        file_url = f"/uploads/{filename}"

        return {
            "url": file_url,
            "content_type": content_type,
            "filename": file.filename,
            "size": len(content),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Add this to serve uploaded files
@user_router.get("/uploads/{filename}")
async def get_upload(filename: str):
    file_path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path)


@user_router.get("/health/db")
async def database_health():
    """Check database health and get statistics"""
    stats = await get_db_stats()
    if not stats:
        raise HTTPException(status_code=500, detail="Database health check failed")
    return stats


@user_router.delete("/messages/{message_id}")
async def delete_message(message_id: str, user_id: str, delete_for: str = "me"):
    """Delete a specific message"""
    try:
        message_oid = ObjectId(message_id)
        
        # Get message details first
        message = await messages_collection.find_one({"_id": message_oid})
        if not message:
            raise HTTPException(status_code=404, detail="Message not found")
            
        if delete_for == "me":
            # Add user to deleted_for array
            result = await messages_collection.update_one(
                {"_id": message_oid},
                {"$addToSet": {"deleted_for": user_id}}
            )
        elif delete_for == "everyone":
            # Only allow sender to delete for everyone
            if message["from_user_id"] != user_id:
                raise HTTPException(status_code=403, detail="Only message sender can delete for everyone")
                
            result = await messages_collection.delete_one({"_id": message_oid})
            
            # Notify other users about deletion
            notification = {
                "type": "message_deleted",
                "message_id": message_id,
                "chat_type": message["type"],
                "chat_id": message["group_id"] if message["type"] == "group" else message["to_user_id"],
                "timestamp": datetime.now(UTC).isoformat()
            }
            
            if message["type"] == "group":
                group = await groups_collection.find_one({"_id": ObjectId(message["group_id"])})
                if group:
                    for member_id in group["member_ids"]:
                        if member_id != user_id:
                            await sio.emit('message_deleted', notification, room=f'user_{member_id}')
            else:
                other_user = message["to_user_id"] if message["from_user_id"] == user_id else message["from_user_id"]
                await sio.emit('message_deleted', notification, room=f'user_{other_user}')
                
        return {"message": "Message deleted successfully"}
        
    except Exception as e:
        print(f"Error deleting message: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@user_router.get("/health/message-buffer")
async def message_buffer_health():
    """Get message buffer statistics"""
    try:
        stats = message_buffer.get_stats()
        return {
            "status": "healthy",
            "stats": stats
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))