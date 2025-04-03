# Real-Time Chat Application

A high-performance, scalable real-time chat application built with FastAPI, Socket.IO, and MongoDB Atlas. This application supports both direct messaging and group chats with features like message buffering, circuit breaking, and automatic scaling.

## Features

- **Real-time Communication**
  - WebSocket-based messaging using Socket.IO
  - Support for direct messages and group chats
  - Real-time message delivery and read receipts
  - Typing indicators and online status

- **Message Handling**
  - Efficient message buffering system
  - Batch processing for database operations
  - Circuit breaker pattern for fault tolerance
  - Automatic scaling of batch sizes based on performance

- **Media Support**
  - Image, video, and file sharing
  - Audio messages
  - Emoji support
  - Automatic media type detection

- **Group Features**
  - Create and manage group chats
  - Add/remove members
  - Group notifications
  - Member activity tracking

- **Performance Optimizations**
  - MongoDB connection pooling
  - Optimized database indexes
  - Message batching and buffering
  - Efficient query patterns

## Technical Stack

- **Backend**
  - FastAPI (ASGI web framework)
  - Python-SocketIO (WebSocket handling)
  - Motor (Async MongoDB driver)
  - Pydantic (Data validation)

- **Database**
  - MongoDB Atlas
  - Optimized indexes for chat operations
  - Write concern optimization for free tier

- **Performance Features**
  - Connection pooling
  - Circuit breaker pattern
  - Message buffering
  - Automatic scaling

## Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd chat_app
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Set up environment variables:
```bash
cp .env.example .env
# Edit .env with your MongoDB Atlas connection string and other settings
```

## Configuration

### MongoDB Atlas Settings
```python
MONGO_URL=your_mongodb_atlas_connection_string
```

### Application Settings
```python
# Message Buffer Configuration
BATCH_SIZE=25
FLUSH_INTERVAL=1.0
MAX_RETRIES=2

# MongoDB Connection Pool
MAX_POOL_SIZE=50
MIN_POOL_SIZE=10
```

## Running the Application

1. Start the server:
```bash
python src/main.py
```

2. The application will be available at:
- HTTP: `http://localhost:8001`
- WebSocket: `ws://localhost:8001/socket.io`

## API Endpoints

### User Routes
- `GET /messages/{user_id}/{other_id}` - Get chat history
- `POST /upload/` - Upload media files
- `GET /health/db` - Check database health
- `GET /health/message-buffer` - Check message buffer status

### Group Routes
- `POST /groups/` - Create a new group
- `GET /groups/{user_id}` - Get user's groups
- `POST /group/{group_id}/{user_id}` - Add user to group
- `DELETE /groups/{group_id}` - Delete group
- `POST /groups/{group_id}/remove` - Remove user from group

## Socket.IO Events

### Client Events
- `connect` - Connect to Socket.IO server
- `join_group` - Join a group chat
- `send_message` - Send a message
- `mark_read` - Mark message as read
- `edit_message` - Edit a message
- `delete_message` - Delete a message

### Server Events
- `message` - New message received
- `notification` - System notification
- `message_update` - Message status update
- `message_deleted` - Message deletion notification

## Performance Considerations

- Optimized for MongoDB Atlas free tier
- Efficient message buffering and batching
- Circuit breaker for fault tolerance
- Automatic scaling based on performance
- Connection pool management
- DNS resolution optimization

## Error Handling

- Comprehensive error logging
- Circuit breaker pattern
- Automatic retries with backoff
- Connection pool management
- Graceful degradation

## Monitoring

- Database health checks
- Message buffer statistics
- Connection pool monitoring
- Circuit breaker status
- Performance metrics

## License

[Your License Here]

## Contributing

[Your Contributing Guidelines Here] 