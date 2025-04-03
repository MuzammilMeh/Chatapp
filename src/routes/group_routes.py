from fastapi import HTTPException, APIRouter
from bson import ObjectId
from datetime import datetime, UTC
from core.models import GroupCreate
from core.database import get_groups_collection, get_messages_collection
from core.models import JSONEncoder
from core.socket_server import sio  
import json
from typing import Optional
import logging

logger = logging.getLogger(__name__)
group_router = APIRouter(tags=["Group"])

@group_router.post("/group/{group_id}/{user_id}")
async def add_user_to_group(group_id: str, user_id: str):
    """Add a user to a group"""
    try:
        groups_collection = await get_groups_collection()
        await groups_collection.update_one(
            {"_id": ObjectId(group_id)},
            {"$addToSet": {"member_ids": user_id}}
        )
        return {"message": "User added to group"}
    except Exception as e:
        logger.error(f"Error adding user to group: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@group_router.post("/groups/")
async def create_group(group_data: GroupCreate):
    """Create a new group chat"""
    try:
        groups_collection = await get_groups_collection()
        
        group_data.member_ids = list(set(group_data.member_ids))
        
        group = {
            "name": group_data.name,
            "member_ids": group_data.member_ids,
            "created_by": group_data.created_by,
            "created_at": datetime.now(UTC)
        }
        
        result = await groups_collection.insert_one(group)
        group["_id"] = result.inserted_id
        
        # Notify all group members about the new group
        notification = {
            "type": "group_notification",
            "content": f"You have been added to group: {group_data.name}",
            "group_id": str(result.inserted_id),
            "timestamp": datetime.now(UTC).isoformat()
        }
        
        for member_id in group_data.member_ids:
            await sio.emit('notification', notification, room=f'user_{member_id}')
        
        return json.loads(json.dumps(group, cls=JSONEncoder))
    except Exception as e:
        logger.error(f"Error creating group: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@group_router.get("/groups/{user_id}")    
async def get_user_groups(user_id: str):
    """Get all groups for a user"""
    try:
        groups_collection = await get_groups_collection()
        groups = await groups_collection.find({"member_ids": user_id}).to_list(length=None)
        return json.loads(json.dumps(groups, cls=JSONEncoder))
    except Exception as e:
        logger.error(f"Error getting user groups: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@group_router.get("/groups/search/{name}")
async def search_groups(name: str):
    """Get group by name"""
    try:
        groups_collection = await get_groups_collection()
        name_pattern = {"$regex": name, "$options": "i"}
        groups = await groups_collection.find({"name": name_pattern}).to_list(length=None)
        
        if not groups:
            raise HTTPException(status_code=404, detail="Group not found")
        
        return json.loads(json.dumps(groups, cls=JSONEncoder))
    except Exception as e:
        logger.error(f"Error searching groups: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@group_router.delete("/groups/{group_id}")
async def delete_group(group_id: str):
    """Delete the group and its associated messages"""
    try:
        groups_collection = await get_groups_collection()
        messages_collection = await get_messages_collection()
        
        group_oid = ObjectId(group_id)
        group = await groups_collection.find_one({"_id": group_oid})
        
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")
        
        # Delete group and its messages
        await groups_collection.delete_one({"_id": group_oid})
        await messages_collection.delete_many({"type": "group", "to": group_id})
        
        return {"message": "Group deleted successfully"}
    except Exception as e:
        logger.error(f"Error deleting group: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@group_router.post("/groups/{group_id}/remove")
async def remove_from_group(group_id: str, user_id: str, admin_id: Optional[str] = None):
    """Remove a user from a group"""
    try:
        groups_collection = await get_groups_collection()
        
        group_oid = ObjectId(group_id)
        group = await groups_collection.find_one({"_id": group_oid})
        
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")
        
        if user_id not in group['member_ids']:
            raise HTTPException(status_code=404, detail="User is not in this group")
            
        if admin_id:
            if admin_id != group['created_by']:
                raise HTTPException(status_code=403, detail="Only group creator can remove members")
        else:
            if user_id == group['created_by']:
                raise HTTPException(
                    status_code=400, 
                    detail="Group creator cannot leave the group. Please delete the group instead."
                )
            admin_id = user_id
        
        result = await groups_collection.update_one(
            {"_id": group_oid},
            {"$pull": {"member_ids": user_id}}
        )
        
        if result.modified_count == 0:
            raise HTTPException(status_code=400, detail="Failed to remove user from group")
        
        notification = {
            "type": "group_notification",
            "content": (f"User has been removed from the group '{group['name']}'" if admin_id != user_id 
                       else f"User has left the group '{group['name']}'"),
            "group_id": group_id,
            "timestamp": datetime.now(UTC).isoformat()
        }
        
        for member_id in group['member_ids']:
            if member_id != user_id:
                await sio.emit('notification', notification, room=f'user_{member_id}')
        
        return {
            "message": "Successfully removed from group",
            "group_id": group_id,
            "removed_user_id": user_id,
            "removed_by": admin_id
        }
        
    except Exception as e:
        logger.error(f"Error removing user from group: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))