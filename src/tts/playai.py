import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

from src import log

logger = log.setup_logger(__name__)

# Simple in-memory cache for voices
_VOICE_CACHE: List[Dict[str, Any]] = []


PLAYAI_BASE_URL = "https://api.play.ht/api/v2"


def _auth_headers() -> Dict[str, str]:
    api_key = os.getenv("PLAYAI_API_KEY")
    user_id = os.getenv("USER_ID")

    if not api_key or not user_id:
        # Do not raise here; let caller handle a friendly error message
        logger.warning("PLAYAI_API_KEY or USER_ID is not set in environment.")

    return {
        "Authorization": f"Bearer {api_key}" if api_key else "",
        "X-User-Id": user_id or "",
        "Accept": "application/json",
    }


async def list_voices(search: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch available PlayAI voices. Optionally filter by a search term.

    Returns a list of voice dicts as returned by the API. On failure, returns an empty list.
    """
    url = f"{PLAYAI_BASE_URL}/voices"
    params = {}
    if search:
        # The API may support query filtering; if not, we'll filter client-side below
        params["search"] = search

    headers = _auth_headers()

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params, timeout=30) as resp:
                if resp.status != 200:
                    txt = await resp.text()
                    logger.error(f"PlayAI voices fetch failed: {resp.status} {txt}")
                    return []
                data = await resp.json()
                # Some APIs wrap voices in a key, try to be defensive
                voices = data.get("voices", data) if isinstance(data, dict) else data
                if search and isinstance(voices, list) and "search" not in params:
                    term = search.lower()
                    voices = [v for v in voices if term in str(v.get("name", "")).lower() or term in str(v.get("id", "")).lower()]
                return voices or []
    except Exception as e:
        logger.error(f"Error fetching PlayAI voices: {e}")
        return []


def get_cached_voices() -> List[Dict[str, Any]]:
    return list(_VOICE_CACHE)


async def preload_voices() -> None:
    """Fetch and cache voices at startup to reduce API calls during interaction."""
    global _VOICE_CACHE
    try:
        voices = await list_voices()
        _VOICE_CACHE = voices or []
        logger.info(f"Preloaded {len(_VOICE_CACHE)} PlayAI voices")
    except Exception as e:
        logger.error(f"Failed to preload PlayAI voices: {e}")


async def refresh_voices_cache() -> List[Dict[str, Any]]:
    """Refresh cache on demand and return the new list."""
    global _VOICE_CACHE
    voices = await list_voices()
    _VOICE_CACHE = voices or []
    logger.info(f"Refreshed PlayAI voices: {len(_VOICE_CACHE)} entries")
    return list(_VOICE_CACHE)


async def synthesize_to_file(
    text: str,
    voice_id: str,
    output_path: Path,
    *,
    format: str = "mp3",
    voice_engine: Optional[str] = None,
    quality: Optional[str] = None,
) -> Optional[Path]:
    """Synthesize TTS using PlayAI and write audio bytes to output_path.

    Returns the Path on success, or None on failure.
    """
    # Prefer the streaming endpoint for immediate bytes
    url = f"{PLAYAI_BASE_URL}/tts/stream"
    headers = _auth_headers()

    # For streaming audio, Accept should be audio/*
    stream_headers = {**headers, "Accept": "audio/mpeg"}

    payload: Dict[str, Any] = {
        "text": text,
        "voice": voice_id,
        "output_format": format,
    }
    if voice_engine:
        payload["voice_engine"] = voice_engine
    if quality:
        payload["quality"] = quality

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=stream_headers, json=payload, timeout=120) as resp:
                if resp.status != 200:
                    txt = await resp.text()
                    logger.error(f"PlayAI TTS failed: {resp.status} {txt}")
                    return None

                # Write streamed bytes to file
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with output_path.open("wb") as f:
                    async for chunk in resp.content.iter_chunked(4096):
                        f.write(chunk)

                return output_path
    except Exception as e:
        logger.error(f"Error during PlayAI TTS: {e}")
        return None
