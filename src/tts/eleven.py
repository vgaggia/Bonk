"""ElevenLabs TTS wrapper used by /tts11.

This module is named `eleven.py` (not `elevenlabs.py`) so it does not shadow
the `elevenlabs` SDK package import.
"""

import inspect
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

from src import log

logger = log.setup_logger(__name__)


MODELS: List[tuple] = [
    ("eleven_flash_v2_5", "Flash v2.5 (~75ms latency)"),
    ("eleven_turbo_v2_5", "Turbo v2.5 (low-latency, balanced)"),
    ("eleven_multilingual_v2", "Multilingual v2 (high quality, 29 langs)"),
    ("eleven_v3", "Eleven v3 (latest)"),
]

DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"
DEFAULT_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"  # George — premade library voice


@dataclass
class VoiceEntry:
    voice_id: str
    name: str
    category: Optional[str] = None


@dataclass
class VoiceSearchResult:
    voices: List[VoiceEntry]
    has_more: bool
    next_page_token: Optional[str]


class ElevenLabsConfigError(RuntimeError):
    """Raised when ELEVENLABS_API_KEY is missing."""


def _get_client():
    """Lazy-construct an AsyncElevenLabs client. Raises if key is missing."""
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        raise ElevenLabsConfigError(
            "ELEVENLABS_API_KEY is not set. Add it to your .env to use /tts11."
        )

    from elevenlabs.client import AsyncElevenLabs

    return AsyncElevenLabs(api_key=api_key)


def default_model_id() -> str:
    return os.getenv("ELEVENLABS_DEFAULT_MODEL_ID", "eleven_turbo_v2_5")


def default_voice_id() -> str:
    return os.getenv("ELEVENLABS_DEFAULT_VOICE_ID", DEFAULT_VOICE_ID)


def default_output_format() -> str:
    return os.getenv("ELEVENLABS_OUTPUT_FORMAT", DEFAULT_OUTPUT_FORMAT)


def _coerce_voice(raw: Any) -> Optional[VoiceEntry]:
    """Best-effort conversion of an SDK voice object/dict into VoiceEntry."""
    if raw is None:
        return None
    voice_id = getattr(raw, "voice_id", None) or (raw.get("voice_id") if isinstance(raw, dict) else None)
    name = getattr(raw, "name", None) or (raw.get("name") if isinstance(raw, dict) else None)
    category = getattr(raw, "category", None) or (
        raw.get("category") if isinstance(raw, dict) else None
    )
    if not voice_id or not name:
        return None
    return VoiceEntry(voice_id=str(voice_id), name=str(name), category=category)


async def search_voices(
    query: Optional[str] = None,
    page_size: int = 25,
    next_page_token: Optional[str] = None,
) -> VoiceSearchResult:
    """Search the user's ElevenLabs voice library.

    Returns all voices visible to the API key (custom + default library).
    page_size is capped at 100 by the API.
    """
    client = _get_client()

    kwargs: dict = {"page_size": min(max(page_size, 1), 100)}
    if query:
        kwargs["search"] = query
    if next_page_token:
        kwargs["next_page_token"] = next_page_token

    raw = await client.voices.search(**kwargs)

    raw_voices = getattr(raw, "voices", None)
    if raw_voices is None and isinstance(raw, dict):
        raw_voices = raw.get("voices", [])
    raw_voices = raw_voices or []

    voices: List[VoiceEntry] = []
    for v in raw_voices:
        entry = _coerce_voice(v)
        if entry is not None:
            voices.append(entry)

    has_more = bool(getattr(raw, "has_more", False) or (
        raw.get("has_more") if isinstance(raw, dict) else False
    ))
    token = getattr(raw, "next_page_token", None) or (
        raw.get("next_page_token") if isinstance(raw, dict) else None
    )

    return VoiceSearchResult(voices=voices, has_more=has_more, next_page_token=token)


async def synthesize_to_file(
    text: str,
    voice_id: str,
    model_id: str,
    output_format: Optional[str] = None,
) -> Path:
    """Generate TTS audio and write it to a temp file. Returns the Path."""
    client = _get_client()
    fmt = output_format or default_output_format()

    suffix = ".mp3" if fmt.startswith("mp3") else ".bin"
    tmp = tempfile.NamedTemporaryFile(prefix="tts11_", suffix=suffix, delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()

    # The SDK varies across versions: convert() may be an async function returning
    # bytes or an iterator, OR an async generator function (returns generator
    # directly when called — not awaitable). Resolve all four shapes.
    result: Any = client.text_to_speech.convert(
        text=text,
        voice_id=voice_id,
        model_id=model_id,
        output_format=fmt,
    )
    if inspect.iscoroutine(result):
        result = await result

    if isinstance(result, (bytes, bytearray)):
        data = bytes(result)
    elif hasattr(result, "__aiter__"):
        chunks: List[bytes] = []
        async for chunk in result:
            if isinstance(chunk, (bytes, bytearray)):
                chunks.append(bytes(chunk))
        data = b"".join(chunks)
    elif hasattr(result, "__iter__"):
        data = b"".join(c for c in result if isinstance(c, (bytes, bytearray)))
    else:
        raise RuntimeError(
            f"Unexpected return type from text_to_speech.convert(): {type(result).__name__}"
        )

    if not data:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise RuntimeError("ElevenLabs returned empty audio data.")

    tmp_path.write_bytes(data)
    logger.info(
        f"ElevenLabs TTS written to {tmp_path} ({len(data)} bytes, "
        f"voice={voice_id}, model={model_id}, fmt={fmt})"
    )
    return tmp_path
