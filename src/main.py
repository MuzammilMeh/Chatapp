from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from contextlib import asynccontextmanager

import json

from datetime import datetime, UTC
from bson import ObjectId
import os
import socketio
import time
from core.database import (
    messages_collection, 
    groups_collection, 
    connect_db, 
    close_db, 
    init_indexes,
    get_groups_collection,
    get_messages_collection
)
from core.models import Message, JSONEncoder, MessageType
from core.socket_server import sio
from core.message_buffer import message_buffer
from routes.group_routes import group_router
from routes.user_routes import user_router
import asyncio
import logging

UPLOAD_DIR = "uploads"
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    try:
        # Initialize database connection with retries
        await connect_db()
        logger.info("Successfully connected to MongoDB")
        
        # Start message buffer flush task
        asyncio.create_task(message_buffer.start_periodic_flush())
        logger.info("Started message buffer periodic flush")
        
    except Exception as e:
        logger.error(f"Error during startup: {e}")
        raise
    
    try:
        yield
    finally:
        # Shutdown
        try:
            # Final flush of any remaining messages
            await message_buffer.flush()
            await close_db()
            logger.info("Successfully closed MongoDB connection")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
            raise


# Create FastAPI app with lifespan
app = FastAPI(title="Chat App API", lifespan=lifespan)

app.include_router(
    user_router,
)
app.include_router(group_router)
# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


# Socket.IO event handlers
@sio.event
async def connect(sid, environ, auth):
    """Handle client connection with improved error handling"""
    try:
        logger.info(f"Connection attempt - SID: {sid}, Auth: {auth}")

        if not auth or "user_id" not in auth:
            logger.warning(f"Authentication failed - missing user_id")
            return False

        user_id = auth["user_id"]
        
        # Store user_id -> sid mapping
        await sio.save_session(sid, {"user_id": user_id})
        
        # Join user's personal room
        await sio.enter_room(sid, f"user_{user_id}")
        
        logger.info(f"User {user_id} connected successfully with sid {sid}")
        return True
        
    except Exception as e:
        logger.error(f"Error in connect handler: {str(e)}")
        return False


@sio.event
async def disconnect(sid):
    """Handle client disconnection"""
    session = await sio.get_session(sid)
    if session and "user_id" in session:
        user_id = session["user_id"]
        print(f"User {user_id} disconnected")

        # Leave all rooms
        for room in sio.rooms(sid):
            await sio.leave_room(sid, room)


async def send_unread_notification(user_id: str):
    """Send unread messages count to user"""
    try:
        messages_collection = await get_messages_collection()
        groups_collection = await get_groups_collection()
        
        # Get unread direct messages
        unread_direct = await messages_collection.count_documents(
            {"type": "direct", "to_user_id": user_id, "status": "sent"}
        )

        # Get unread group messages
        user_groups = await groups_collection.find(
            {"member_ids": user_id}, {"_id": 1}
        ).to_list(None)

        group_ids = [str(group["_id"]) for group in user_groups]
        unread_group = await messages_collection.count_documents(
            {
                "type": "group",
                "group_id": {"$in": group_ids},
                "read_by": {"$ne": user_id},
            }
        )

        if unread_direct > 0 or unread_group > 0:
            notification = {
                "type": "unread_notification",
                "content": f"You have {unread_direct} unread direct messages and {unread_group} unread group messages",
                "unread_direct": unread_direct,
                "unread_group": unread_group,
                "timestamp": datetime.now(UTC),
            }
            await sio.emit("notification", notification, room=f"user_{user_id}")

    except Exception as e:
        logger.error(f"Error sending unread notification to {user_id}: {str(e)}")


@sio.event
async def join_group(sid, group_id):
    """Handle user joining a group"""
    try:
        session = await sio.get_session(sid)
        if not session or "user_id" not in session:
            return

        user_id = session["user_id"]
        groups_collection = await get_groups_collection()
        group = await groups_collection.find_one({"_id": ObjectId(group_id)})

        if not group or user_id not in group["member_ids"]:
            return

        # Join the group's room
        await sio.enter_room(sid, f"group_{group_id}")
        logger.info(f"User {user_id} joined group {group_id}")
    except Exception as e:
        logger.error(f"Error in join_group handler: {str(e)}")


@sio.event
async def leave_group(sid, group_id):
    """Handle user leaving a group"""
    await sio.leave_room(sid, f"group_{group_id}")


@sio.event
async def send_message(sid, data):
    """Handle sending messages with batched database writes"""
    try:
        session = await sio.get_session(sid)
        if not session or "user_id" not in session:
            return

        user_id = session["user_id"]
        groups_collection = await get_groups_collection()

        # Prepare message data
        message_data = {
            "from_user_id": user_id,
            "content": data["content"],
            "timestamp": datetime.now(UTC),
            "type": data["type"],
            "content_type": data.get("content_type", MessageType.TEXT),
        }

        # Handle media content if present
        if "media_url" in data:
            message_data["media_url"] = data["media_url"]
            message_data["media_metadata"] = data.get("media_metadata", {})

            # Validate media file exists
            if data["media_url"]:
                file_path = os.path.join(UPLOAD_DIR, os.path.basename(data["media_url"]))
                if not os.path.exists(file_path):
                    raise Exception("Media file not found")

        message = Message(**message_data)

        if message.type == "group":
            group_id = ObjectId(data["to"])
            group = await groups_collection.find_one({"_id": group_id})

            if not group or user_id not in group["member_ids"]:
                return

            # Prepare message for database
            message_dict = message.dict()
            message_dict["group_id"] = group_id
            message_dict["read_by"] = [user_id]
            
            # Add to buffer for batch processing
            await message_buffer.add_message(message_dict)
            
            # Convert to JSON-serializable format for Socket.IO emission
            emit_message = json.loads(json.dumps(message_dict, cls=JSONEncoder))
            if emit_message.get("media_url"):
                emit_message["media_url"] = f"/uploads/{os.path.basename(emit_message['media_url'])}"

            # Emit immediately to all group members
            await sio.emit("message", emit_message, room=f"group_{str(group_id)}")

        else:
            # Handle direct message
            message_dict = message.dict()
            message_dict["to_user_id"] = data["to"]
            
            # Add to buffer for batch processing
            await message_buffer.add_message(message_dict)
            
            # Convert to JSON-serializable format for Socket.IO emission
            emit_message = json.loads(json.dumps(message_dict, cls=JSONEncoder))
            if emit_message.get("media_url"):
                emit_message["media_url"] = f"/uploads/{os.path.basename(emit_message['media_url'])}"

            # Emit immediately to both sender and recipient
            await sio.emit("message", emit_message, room=f'user_{data["to"]}')
            await sio.emit("message", emit_message, room=f"user_{user_id}")

    except Exception as e:
        logger.error(f"Error processing message: {str(e)}")
        await sio.emit("error", {"message": str(e)}, room=f"user_{user_id}")


@sio.event
async def mark_read(sid, data):
    """Handle marking messages as read"""
    try:
        session = await sio.get_session(sid)
        if not session or "user_id" not in session:
            return

        user_id = session["user_id"]
        messages_collection = await get_messages_collection()
        msg_id = ObjectId(data["message_id"])

        await messages_collection.update_one(
            {"_id": msg_id},
            {"$set": {"status": "read"}, "$addToSet": {"read_by": user_id}},
        )

        # Notify other users about read status
        message = await messages_collection.find_one({"_id": msg_id})
        if message:
            update = {
                "type": "read_receipt",
                "message_id": str(msg_id),
                "read_by": message["read_by"],
            }
            if message["type"] == "group":
                await sio.emit(
                    "message_update", update, room=f'group_{str(message["group_id"])}'
                )
            else:
                await sio.emit(
                    "message_update", update, room=f'user_{message["from_user_id"]}'
                )

    except Exception as e:
        logger.error(f"Error marking message as read: {str(e)}")


@sio.event
async def edit_message(sid, data):
    """Handle editing messages"""
    try:
        session = await sio.get_session(sid)
        if not session or "user_id" not in session:
            await sio.emit("error", {"message": "Not authenticated"}, room=sid)
            return

        user_id = session["user_id"]
        messages_collection = await get_messages_collection()
        msg_id = ObjectId(data["message_id"])
        new_content = data["content"]

        # Get the message to check ownership
        message = await messages_collection.find_one({"_id": msg_id})
        if not message:
            await sio.emit("error", {"message": "Message not found"}, room=sid)
            return

        # Only allow editing if user is the message sender
        if message["from_user_id"] != user_id:
            await sio.emit(
                "error", {"message": "Not authorized to edit this message"}, room=sid
            )
            return

        # Update the message
        result = await messages_collection.update_one(
            {"_id": msg_id},
            {"$set": {"content": new_content}}
        )
        
        if result.modified_count > 0:
            # Prepare update notification
            update = {
                "type": "message_edit",
                "message_id": str(msg_id),
                "new_content": new_content,
                "edited_at": datetime.now(UTC).isoformat(),
            }

            # Notify relevant users
            if message["type"] == "group":
                await sio.emit(
                    "message_update", update, room=f'group_{str(message["group_id"])}'
                )
            else:
                await sio.emit(
                    "message_update", update, room=f'user_{message["from_user_id"]}'
                )
                await sio.emit(
                    "message_update", update, room=f'user_{message["to_user_id"]}'
                )

    except Exception as e:
        logger.error(f"Error editing message: {str(e)}")
        await sio.emit("error", {"message": str(e)}, room=sid)


@sio.event
async def delete_message(sid, data):
    """Handle deleting messages"""
    try:
        session = await sio.get_session(sid)
        if not session or "user_id" not in session:
            await sio.emit("error", {"message": "Not authenticated"}, room=sid)
            return

        user_id = session["user_id"]
        messages_collection = await get_messages_collection()
        msg_id = ObjectId(data["message_id"])
        delete_for = data.get("delete_for", "everyone")

        # Get the message to check ownership
        message = await messages_collection.find_one({"_id": msg_id})
        if not message:
            await sio.emit("error", {"message": "Message not found"}, room=sid)
            return

        if delete_for == "everyone":
            # Only allow deletion if user is the message sender
            if message["from_user_id"] != user_id:
                await sio.emit(
                    "error", {"message": "Not authorized to delete this message"}, room=sid
                )
                return

            # Delete the message
            await messages_collection.delete_one({"_id": msg_id})

            # Prepare deletion notification
            notification = {
                "type": "message_deleted",
                "message_id": str(msg_id),
                "chat_type": message["type"],
                "deleted_at": datetime.now(UTC).isoformat()
            }

            # Notify relevant users
            if message["type"] == "group":
                group_id = str(message["group_id"])
                await sio.emit("message_deleted", notification, room=f"group_{group_id}")
            else:
                # Notify both sender and receiver for direct messages
                other_user = message["to_user_id"] if message["from_user_id"] == user_id else message["from_user_id"]
                await sio.emit("message_deleted", notification, room=f"user_{other_user}")
                await sio.emit("message_deleted", notification, room=f"user_{user_id}")

        else:  # delete_for == "me"
            # Add user to deleted_for array
            await messages_collection.update_one(
                {"_id": msg_id},
                {"$addToSet": {"deleted_for": user_id}}
            )
            # Only notify the user who deleted for themselves
            await sio.emit("message_deleted", {
                "type": "message_hidden",
                "message_id": str(msg_id)
            }, room=f"user_{user_id}")

        await sio.emit("success", {"message": "Message deleted successfully"}, room=sid)

    except Exception as e:
        logger.error(f"Error deleting message: {str(e)}")
        await sio.emit("error", {"message": str(e)}, room=sid)


# Mount Socket.IO app first
socket_app = socketio.ASGIApp(
    socketio_server=sio, other_asgi_app=app, socketio_path="socket.io"
)

# Use the combined app
app = socket_app

if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8001,
        reload=False,  # Disable reload in production
        workers=4,    # Increased workers (2 * num_cores + 1)
        loop="uvloop",
        limit_concurrency=5000,  # Increased concurrency limit
        backlog=8192,  # Increased connection backlog
        timeout_keep_alive=30,  # Increased keep-alive timeout
        access_log=False  # Disable access logging for better performance
    )
