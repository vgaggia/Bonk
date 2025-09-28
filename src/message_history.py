from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from typing import Deque, Dict


@dataclass
class Message:
    role: str  # 'user' or 'assistant'
    content: str
    timestamp: datetime

class MessageHistory:
    def __init__(self, max_messages: int = 10):
        self.max_messages = max_messages
        self.histories: Dict[int, Deque[Message]] = defaultdict(lambda: deque(maxlen=max_messages))

    def add_message(self, user_id: int, role: str, content: str):
        """Add a message to a user's history"""
        message = Message(role=role, content=content, timestamp=datetime.now())
        self.histories[user_id].append(message)

    def get_history(self, user_id: int) -> list[Message]:
        """Get a user's message history"""
        return list(self.histories[user_id])

    def clear_history(self, user_id: int):
        """Clear a user's message history"""
        self.histories[user_id].clear()

    def format_history_for_api(self, user_id: int) -> list[dict]:
        """Format the message history for API consumption"""
        history = self.get_history(user_id)
        return [
            {
                "role": msg.role,
                "content": msg.content
            }
            for msg in history
        ]
