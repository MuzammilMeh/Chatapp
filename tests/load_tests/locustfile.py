import json
import random
from locust import HttpUser, task, between
import socketio
import urllib.parse
import time
from datetime import datetime

# List of predefined group IDs that we'll create during setup
TEST_GROUPS = []

class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)

class ChatUser(HttpUser):
    wait_time = between(0.1, 0.3)  # Back to original timing
    
    def on_start(self):
        self.user_id = f"test_user_{random.randint(1000, 9999)}"
        
        # Simpler connection throttling
        if not hasattr(ChatUser, '_last_connection_time'):
            ChatUser._last_connection_time = 0
        
        current_time = time.time()
        time_since_last = current_time - ChatUser._last_connection_time
        if time_since_last < 0.05:  # 50ms minimum between connections
            time.sleep(0.05 - time_since_last)
        ChatUser._last_connection_time = time.time()
        
        # Simplified Socket.IO client settings
        self.sio = socketio.Client(
            logger=False,
            engineio_logger=False,
            reconnection=True,
            reconnection_attempts=3,
            reconnection_delay=1,
            reconnection_delay_max=5,
            randomization_factor=0.5,
            request_timeout=5000,
            ssl_verify=True,
            handle_sigint=False
        )
        
        self.setup_socket_handlers()
        
        # Simple retry logic
        max_retries = 3
        for attempt in range(max_retries):
            if self.connect_socket():
                break
            time.sleep(1)
        
        # Create initial test group
        if not TEST_GROUPS:
            self.create_test_group()

    def setup_socket_handlers(self):
        @self.sio.event
        def connect():
            try:
                print(f"User {self.user_id} connected successfully")
                self.environment.runner.stats.log_request("socketio", "connect", 0, 0)
            except Exception as e:
                print(f"Error in connect handler: {str(e)}")
        
        @self.sio.event
        def disconnect():
            try:
                print(f"User {self.user_id} disconnected")
                # Log as a request instead of an error since disconnection is a normal event
                self.environment.runner.stats.log_request(
                    "socketio",
                    "disconnect",
                    0,  # response time
                    0   # response length
                )
            except Exception as e:
                print(f"Error in disconnect handler: {str(e)}")
                self.environment.runner.stats.log_error(
                    "socketio_disconnect_error",
                    f"Error in disconnect handler: {str(e)}"
                )
        
        @self.sio.event
        def connect_error(error):
            try:
                error_msg = str(error) if error else "Unknown connection error"
                print(f"Connection error for user {self.user_id}: {error_msg}")
                self.environment.runner.stats.log_error(
                    "socketio_connect_error",
                    error_msg
                )
            except Exception as e:
                print(f"Error in connect_error handler: {str(e)}")

    def connect_socket(self):
        try:
            if self.sio.connected:
                return True
            
            parsed_host = urllib.parse.urlparse(self.host)
            host = parsed_host.netloc or parsed_host.path.strip('/')
            socket_url = f"http://{host}"
            print(socket_url)
            
            self.sio.connect(
                socket_url,
                auth={"user_id": self.user_id},
                wait_timeout=5,
                transports=['websocket'],
                headers={
                    "User-Agent": "Python-SocketIO-Locust-Test"
                },
                socketio_path="socket.io"
            )
            
            time.sleep(0.1)
            return self.sio.connected
            
        except Exception as e:
            print(f"Connection error for user {self.user_id}: {str(e)}")
            if self.sio and hasattr(self.sio, 'connected') and self.sio.connected:
                try:
                    self.sio.disconnect()
                except:
                    pass
            return False

    def send_socket_message(self, event_name, data):
        if not self.sio or not self.sio.connected:
            if not self.connect_socket():
                return
        
        try:
            data['event_id'] = str(random.randint(1, 1000000))
            start_time = time.time()
            
            # Simplified callback
            def callback(*args):
                total_time = int((time.time() - start_time) * 1000)
                self.environment.runner.stats.log_request(
                    "socketio",
                    "emit",
                    total_time,
                    0
                )
            
            self.sio.emit(event_name, data, callback=callback)
            
        except Exception as e:
            print(f"Error sending message: {str(e)}")
            self.environment.runner.stats.log_error("socketio_emit", str(e))

    @task(5)
    def send_direct_message(self):
        message = {
            "type": "direct",
            "to": f"test_user_{random.randint(1000, 9999)}",
            "content": f"Test message {random.randint(1, 1000)}",
            "content_type": "text"
        }
        self.send_socket_message("send_message", message)
        
    @task(5)
    def send_group_message(self):
        if not TEST_GROUPS:
            self.create_test_group()
            return
            
        group_id = random.choice(TEST_GROUPS)
        message = {
            "type": "group",
            "to": group_id,
            "content": f"Group test message {random.randint(1, 1000)}",
            "content_type": "text"
        }
        self.send_socket_message("send_message", message)
    
    # @task(2)
    # def fetch_message_history(self):
    #     other_user = f"test_user_{random.randint(1000, 9999)}"
    #     with self.client.get(
    #         f"/messages/{self.user_id}/{other_user}",
    #         name="/messages/[user_id]/[other_id]",
    #         catch_response=True
    #     ) as response:
    #         if response.status_code == 200:
    #             response.success()
    #         elif response.status_code == 503:
    #             # Don't mark connection pool timeouts as failures
    #             response.success()
    #         else:
    #             response.failure(f"Failed with status {response.status_code}")

    def create_test_group(self):
        try:
            group_data = {
                "name": f"Test Group {random.randint(1000, 9999)}",
                "member_ids": [self.user_id],
                "created_by": self.user_id
            }
            response = self.client.post("/groups/", json=group_data)
            if response.ok:
                group = response.json()
                TEST_GROUPS.append(group["_id"])
                print(f"Created test group with ID: {group['_id']}")
        except Exception as e:
            print(f"Error creating test group: {str(e)}")

    def on_stop(self):
        if self.sio:
            try:
                self.sio.disconnect()
            except Exception as e:
                print(f"Error disconnecting Socket.IO for user {self.user_id}: {str(e)}")

# To run this test:
# locust -f locustfile.py --host=http://localhost:8001 