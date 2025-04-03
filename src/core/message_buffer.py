from typing import List, Dict
import asyncio
from datetime import datetime, UTC
from core.database import get_messages_collection
import logging
from motor.motor_asyncio import AsyncIOMotorClient
import backoff
from pymongo.errors import (
    ConnectionFailure, 
    OperationFailure, 
    ServerSelectionTimeoutError,
    NetworkTimeout
)
from pymongo.write_concern import WriteConcern

class CircuitBreaker:
    def __init__(self, failure_threshold=5, reset_timeout=30):
        self.failure_count = 0
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.last_failure_time = None
        self.is_open = False
        
    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = datetime.now(UTC)
        if self.failure_count >= self.failure_threshold:
            self.is_open = True
            
    def record_success(self):
        self.failure_count = 0
        self.is_open = False
        
    def can_proceed(self) -> bool:
        if not self.is_open:
            return True
        
        if (datetime.now(UTC) - self.last_failure_time).total_seconds() > self.reset_timeout:
            self.failure_count = 0
            self.is_open = False
            return True
            
        return False

class MessageBuffer:
    def __init__(self, batch_size=10, flush_interval=2.0, max_retries=2):
        self.messages: List[Dict] = []
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.lock = asyncio.Lock()
        self.last_flush = datetime.now(UTC)
        self.max_retries = max_retries
        self.circuit_breaker = CircuitBreaker(failure_threshold=3, reset_timeout=60)
        self.write_concern = WriteConcern(w=1, wtimeout=20000)
        self.stats = {
            "total_messages": 0,
            "successful_flushes": 0,
            "failed_flushes": 0,
            "retry_count": 0,
            "circuit_breaker_trips": 0
        }
        
    async def add_message(self, message: Dict) -> None:
        async with self.lock:
            self.messages.append(message)
            self.stats["total_messages"] += 1
            
            # Check if we should flush based on batch size
            if len(self.messages) >= self.batch_size:
                await self.flush()

    @backoff.on_exception(
        backoff.expo,
        (ConnectionFailure, OperationFailure, ServerSelectionTimeoutError, NetworkTimeout),
        max_tries=2,
        max_time=10
    )                
    async def _perform_bulk_insert(self, messages_to_flush: List[Dict]) -> bool:
        if not self.circuit_breaker.can_proceed():
            logging.warning("Circuit breaker is open, skipping database operation")
            return False
            
        try:
            messages_collection = await get_messages_collection()
            # Smaller chunks for free tier
            chunk_size = 5
            for i in range(0, len(messages_to_flush), chunk_size):
                chunk = messages_to_flush[i:i + chunk_size]
                # Use proper WriteConcern instance
                await messages_collection.with_options(
                    write_concern=self.write_concern
                ).insert_many(
                    chunk,
                    ordered=False
                )
            self.circuit_breaker.record_success()
            return True
        except Exception as e:
            logging.error(f"Bulk insert error: {str(e)}")
            self.circuit_breaker.record_failure()
            self.stats["circuit_breaker_trips"] += 1
            raise
                
    async def flush(self) -> None:
        async with self.lock:
            if not self.messages:
                return
                
            try:
                # Create a copy of messages to flush and clear buffer
                messages_to_flush = self.messages.copy()
                self.messages.clear()
                
                # Attempt bulk insert with retries
                success = await self._perform_bulk_insert(messages_to_flush)
                
                if success:
                    self.last_flush = datetime.now(UTC)
                    self.stats["successful_flushes"] += 1
                else:
                    self.stats["failed_flushes"] += 1
                    # On complete failure, add messages back to buffer
                    self.messages.extend(messages_to_flush)
                    
            except Exception as e:
                self.stats["failed_flushes"] += 1
                logging.error(f"Error flushing messages: {str(e)}")
                # On error, add messages back to buffer
                self.messages.extend(messages_to_flush)
                # Reduce batch size temporarily if we're having issues
                self.batch_size = max(10, self.batch_size // 2)

    async def start_periodic_flush(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.flush_interval)
                if self.circuit_breaker.can_proceed():
                    await self.flush()
                    # Gradually increase batch size back to original if we're successful
                    if self.stats["successful_flushes"] > 5:
                        self.batch_size = min(50, self.batch_size + 5)
            except Exception as e:
                logging.error(f"Error in periodic flush: {str(e)}")

    def get_stats(self) -> Dict:
        """Get current buffer statistics"""
        return {
            **self.stats,
            "current_buffer_size": len(self.messages),
            "current_batch_size": self.batch_size,
            "last_flush": self.last_flush.isoformat(),
            "circuit_breaker_status": "open" if self.circuit_breaker.is_open else "closed",
            "failure_count": self.circuit_breaker.failure_count
        }

# Create global message buffer instance with more conservative settings for free tier
message_buffer = MessageBuffer(
    batch_size=10,        # Reduced batch size for free tier
    flush_interval=2.0,   # Increased interval to reduce load
    max_retries=2         # Reduced retries
) 