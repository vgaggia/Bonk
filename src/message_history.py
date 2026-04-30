from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from typing import Deque, Dict, Union

# Claude Haiku 4.5 context window. Both /chat and /listen histories use
# this as the rolling-window budget; we leave headroom below for the
# system prompt, current user message, and max output tokens.
HAIKU_CONTEXT_TOKENS = 200_000
DEFAULT_HISTORY_TOKEN_BUDGET = 190_000


def _estimate_tokens(text: str) -> int:
    """Char-based token estimator (~4 chars/token; conservatively undershoots)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


@dataclass
class Message:
    role: str  # 'user' or 'assistant'
    content: str
    timestamp: datetime


class MessageHistory:
    """Per-key rolling-window message store with a shared token budget.

    Oldest messages are evicted when the running token estimate exceeds
    `max_tokens`. Token estimation uses a coarse char/4 heuristic — fast
    and dependency-free; close enough that we stay safely under the
    real model context window.
    """

    def __init__(self, max_tokens: int = DEFAULT_HISTORY_TOKEN_BUDGET):
        self.max_tokens = max_tokens
        # Keys are Discord user IDs (int) or the literal 'voice_shared' (str)
        self.histories: Dict[Union[int, str], Deque[Message]] = defaultdict(deque)
        self.token_counts: Dict[Union[int, str], int] = defaultdict(int)

    def add_message(self, user_id: Union[int, str], role: str, content: str) -> None:
        """Append a message and evict oldest entries until under budget."""
        message = Message(role=role, content=content, timestamp=datetime.now())
        history = self.histories[user_id]
        history.append(message)
        self.token_counts[user_id] += _estimate_tokens(content)

        while self.token_counts[user_id] > self.max_tokens and history:
            evicted = history.popleft()
            self.token_counts[user_id] = max(
                0, self.token_counts[user_id] - _estimate_tokens(evicted.content)
            )

    def get_history(self, user_id: Union[int, str]) -> list[Message]:
        """Get a user's message history."""
        return list(self.histories[user_id])

    def clear_history(self, user_id: Union[int, str]) -> None:
        """Clear a user's message history."""
        self.histories[user_id].clear()
        self.token_counts[user_id] = 0

    def format_history_for_api(self, user_id: Union[int, str]) -> list[dict]:
        """Format the message history for API consumption."""
        history = self.get_history(user_id)
        return [{"role": msg.role, "content": msg.content} for msg in history]
