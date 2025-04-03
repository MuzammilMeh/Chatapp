import socketio

# In src/core/socket_server.py
sio = socketio.AsyncServer(
    async_mode='asgi',
    cors_allowed_origins="*",
    logger=False,  # Disable logging for better performance
    engineio_logger=False,
    ping_timeout=25000,  # Increased timeout
    ping_interval=15000,  # Increased interval to reduce overhead
    max_http_buffer_size=5e6,  # Increased buffer size
    async_handlers=True,
    client_manager_options={
        'async_mode': 'asgi',
        'write_only': True,
        'max_rooms': 3000,  # Increased room limit
        'max_room_members': 2000  # Set based on expected users
    },
    max_payload_size=5000000,  # Increased payload size
    async_handlers_kwargs={
        'max_concurrent': 2000  # Increased concurrent handler limit
    }
)