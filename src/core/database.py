from motor.motor_asyncio import AsyncIOMotorClient
import os
from typing import Optional
from pymongo import IndexModel, ASCENDING, DESCENDING, ReadPreference, WriteConcern
from dotenv import load_dotenv
from pymongo.server_api import ServerApi
import logging
import dns.resolver
from pymongo.errors import ConfigurationError, ConnectionFailure
import time
import asyncio
from contextlib import asynccontextmanager

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# MongoDB Atlas connection
MONGO_URL = os.getenv("MONGO_URL")
if not MONGO_URL:
    raise ValueError("No MONGO_URL environment variable set")

# Configure DNS resolver
dns.resolver.default_resolver = dns.resolver.Resolver(configure=True)
dns.resolver.default_resolver.nameservers = ['8.8.8.8', '8.8.4.4']  # Use Google's DNS servers

# Global variables
client: Optional[AsyncIOMotorClient] = None
db = None
messages_collection = None
groups_collection = None
is_initialized = False

async def get_db():
    """Get database instance, initializing if necessary"""
    global is_initialized
    if not is_initialized:
        await init_db()
    return db

async def get_messages_collection():
    """Get messages collection, initializing if necessary"""
    global messages_collection
    if not is_initialized:
        await init_db()
    if messages_collection is None:
        raise ConnectionError("Database not properly initialized")
    return messages_collection

async def get_groups_collection():
    """Get groups collection, initializing if necessary"""
    global groups_collection
    if not is_initialized:
        await init_db()
    if groups_collection is None:
        raise ConnectionError("Database not properly initialized")
    return groups_collection

async def init_db():
    global client, db, messages_collection, groups_collection, is_initialized
    
    if is_initialized:
        return
    
    try:
        # Create client with the current event loop
        client = AsyncIOMotorClient(
            MONGO_URL,
            server_api=ServerApi('1'),
            maxPoolSize=50,
            minPoolSize=10,
            maxIdleTimeMS=30000,
            connectTimeoutMS=20000,
            serverSelectionTimeoutMS=20000,
            socketTimeoutMS=30000,
            waitQueueTimeoutMS=20000,
            waitQueueMultiple=3,
            retryWrites=True,
            retryReads=True,
            maxConnecting=5,
            appName='chatapp',
            heartbeatFrequencyMS=10000,
            localThresholdMS=15000,
            compressors='zlib',
            zlibCompressionLevel=3,
            directConnection=False
        )
        
        # Test the connection
        await client.admin.command('ping')
        logger.info("Successfully connected to MongoDB")
        
        # Initialize database and collections
        db = client.get_database('chatapp')
        messages_collection = db.get_collection(
            'messages',
            read_preference=ReadPreference.PRIMARY_PREFERRED,
            write_concern=WriteConcern(w=1, wtimeout=20000)
        )
        groups_collection = db.get_collection(
            'groups',
            read_preference=ReadPreference.PRIMARY_PREFERRED,
            write_concern=WriteConcern(w=1, wtimeout=20000)
        )
        
        # Initialize indexes
        await init_indexes()
        is_initialized = True
        logger.info("Database initialization completed successfully")
        
    except Exception as e:
        logger.error(f"Error initializing MongoDB client: {str(e)}")
        if client:
            client.close()
            client = None
        db = None
        messages_collection = None
        groups_collection = None
        is_initialized = False
        raise

async def init_indexes():
    """Initialize database indexes"""
    try:
        # Indexes for messages collection
        message_indexes = [
            IndexModel([("from_user_id", ASCENDING), ("to_user_id", ASCENDING)], 
                      name="user_messages"),
            IndexModel([("group_id", ASCENDING)], 
                      name="group_messages"),
            IndexModel([("timestamp", DESCENDING)], 
                      name="message_timestamp"),
            IndexModel([("type", ASCENDING), ("status", ASCENDING)], 
                      name="message_type_status"),
            IndexModel([("read_by", ASCENDING)], 
                      name="message_read_by"),
        ]
        await messages_collection.create_indexes(message_indexes)

        # Indexes for groups collection
        group_indexes = [
            IndexModel([("member_ids", ASCENDING)], 
                      name="group_members"),
            IndexModel([("created_at", DESCENDING)], 
                      name="group_creation_time"),
            IndexModel([("name", ASCENDING)], 
                      name="group_name")
        ]
        await groups_collection.create_indexes(group_indexes)
        logger.info("Database indexes created successfully")
        
    except Exception as e:
        logger.error(f"Error creating indexes: {str(e)}")
        raise

async def get_db_stats():
    """Get database statistics"""
    try:
        return {
            "messages_count": await messages_collection.count_documents({}),
            "groups_count": await groups_collection.count_documents({}),
            "indexes": {
                "messages": await messages_collection.index_information(),
                "groups": await groups_collection.index_information()
            }
        }
    except Exception as e:
        logger.error(f"Error getting database stats: {str(e)}")
        return None

async def connect_db():
    """Initialize database connection with retries"""
    max_retries = 3
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            await init_db()
            return
        except Exception as e:
            if attempt == max_retries - 1:
                logger.error(f"Failed to connect to database after {max_retries} attempts")
                raise
            logger.warning(f"Database connection attempt {attempt + 1} failed: {str(e)}")
            await asyncio.sleep(retry_delay * (attempt + 1))

async def close_db():
    """Close database connection"""
    global client, db, messages_collection, groups_collection, is_initialized
    if client:
        client.close()
        client = None
        db = None
        messages_collection = None
        groups_collection = None
        is_initialized = False
        logger.info("MongoDB connection closed")