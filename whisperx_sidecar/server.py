import logging
import os
import zlib
from pathlib import Path
from typing import Optional
import numpy as np

import fastapi
import uvicorn
from dotenv import load_dotenv

# Load .env from parent directory
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)
    logger_setup = logging.getLogger("whisperx_sidecar")
    logger_setup.info(f"Loaded .env from {env_path}")

logger = logging.getLogger("whisperx_sidecar")
logging.basicConfig(
    level=logging.DEBUG if os.getenv("DEBUG") else logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

try:
    import torch  # type: ignore[import]
    import whisperx  # type: ignore[import]
except ImportError as exc:  # pragma: no cover - sidecar-only
    logger.error("Failed to import torch/whisperx: %s", exc)
    raise


WHISPERX_MODEL = os.getenv("WHISPERX_MODEL", "large-v3")
logger.info(f"Using WHISPERX_MODEL: {WHISPERX_MODEL}")


def _load_model():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    compute_type = "float16" if device == "cuda" else "float32"
    logger.info("Loading whisperx model '%s' on device %s with compute_type %s", 
                WHISPERX_MODEL, device, compute_type)
    
    # Pass transcription options here instead of in transcribe()
    asr_options = {
        "initial_prompt": "Conversational English. The assistant's name is Bonk.",
        "condition_on_previous_text": False,  # Prevent repetition loops
    }
    
    model = whisperx.load_model(
        WHISPERX_MODEL, 
        device, 
        compute_type=compute_type,
        asr_options=asr_options
    )
    return model, device


model, device = _load_model()
app = fastapi.FastAPI(title="WhisperX Sidecar", version="0.1.0")


@app.post("/transcribe")
async def transcribe(audio: fastapi.UploadFile) -> dict:
    """Transcribe an uploaded audio file using whisperx."""
    try:
        contents = await audio.read()
        tmp_path = Path("temp_sidecar_audio.wav")
        tmp_path.write_bytes(contents)
        
        logger.debug(f"Received audio file: {len(contents)} bytes")

        # Load audio using whisperx (handles resampling to 16kHz)
        audio_data = whisperx.load_audio(str(tmp_path))
        logger.debug(f"Loaded audio shape: {audio_data.shape}, dtype: {audio_data.dtype}")
        
        # Calculate audio statistics for debugging
        audio_duration = len(audio_data) / 16000  # whisperx loads at 16kHz
        audio_rms = np.sqrt(np.mean(audio_data ** 2))
        audio_max = np.max(np.abs(audio_data))
        logger.info(f"Audio stats (before norm) - Duration: {audio_duration:.2f}s, RMS: {audio_rms:.4f}, Max: {audio_max:.4f}")
        
        # Check if audio is too quiet (likely silence)
        if audio_max < 0.001:
            logger.warning("Audio appears to be silence (max amplitude < 0.001)")
            # Continue anyway, let the model decide (or it might handle silence well)
        
        # Normalize audio if it's too quiet
        # WhisperX's internal VAD can be aggressive with quiet audio
        # Target peak amplitude around 0.5-0.7 for good recognition
        if audio_max < 0.3:
            # Amplify quiet audio
            target_peak = 0.5
            gain = target_peak / max(audio_max, 1e-6)
            audio_data = audio_data * gain
            logger.info(f"Audio amplified by {gain:.2f}x (was too quiet)")
            
            # Recalculate stats after normalization
            audio_rms = np.sqrt(np.mean(audio_data ** 2))
            audio_max = np.max(np.abs(audio_data))
            logger.info(f"Audio stats (after norm) - RMS: {audio_rms:.4f}, Max: {audio_max:.4f}")
        
        # Transcribe with WhisperX
        # Use batch_size=1 for lowest latency (realtime feel)
        result = model.transcribe(
            audio_data,
            batch_size=1,
            language="en",
            task="transcribe"
        )
        
        # Log the raw result for debugging
        logger.debug(f"Transcription result: {result}")
        
        # Extract text from result
        text = ""
        if isinstance(result, dict):
            if "segments" in result:
                segments = result.get("segments", [])
                text_segments = []
                for seg in segments:
                    seg_text = seg.get("text", "").strip()
                    
                    # Filter specific hallucinations at segment level
                    if seg_text.lower().strip(" .!") in ("thank you", "thanks", "thanks for watching"):
                        continue

                    # Confidence filtering
                    # avg_logprob is usually negative. Closer to 0 is better.
                    # -0.8 corresponds to roughly 45% probability.
                    # -1.0 is roughly 36%.
                    # Lower values indicate uncertainty.
                    avg_logprob = seg.get("avg_logprob")
                    if avg_logprob is not None and avg_logprob < -0.8:
                        logger.debug(f"Dropping low confidence segment ({avg_logprob:.2f}): '{seg_text}'")
                        continue
                        
                    if seg_text:
                        text_segments.append(seg_text)
                text = " ".join(text_segments)
            else:
                text = result.get("text", "")
        
        # Clean up temp file
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            logger.debug("Failed to remove temp_sidecar_audio.wav", exc_info=True)

        if not text or not text.strip():
            logger.info("No speech detected")
            return {"text": ""}

        # --- Robust Hallucination / Repetition Filter ---
        clean_text = text.strip()
        
        if len(clean_text) > 40:
            # 1. Check Unique Word Ratio
            # Hallucination loops often repeat the same few words: "Thank you. Thank you. Thank you."
            words = clean_text.split()
            if len(words) > 10:
                unique_words = set(words)
                unique_ratio = len(unique_words) / len(words)
                
                # If less than 40% of words are unique, it's likely a loop
                if unique_ratio < 0.4:
                    logger.warning(f"Hallucination detected (unique ratio {unique_ratio:.2f}): '{clean_text[:100]}...'")
                    return {"text": ""}

            # 2. Check Compression Ratio
            # Repetitive text compresses extremely well.
            # "test test test" compresses to almost nothing compared to its length.
            compressed = zlib.compress(clean_text.encode('utf-8'))
            compression_ratio = len(compressed) / len(clean_text.encode('utf-8'))
            
            # Normal text usually has ratio > 0.5. Loops often < 0.4.
            if compression_ratio < 0.35:
                logger.warning(f"Hallucination detected (compression ratio {compression_ratio:.2f}): '{clean_text[:100]}...'")
                return {"text": ""}

        logger.info(f"Transcription successful: '{clean_text}'")
        return {"text": clean_text}
        
    except Exception as exc:  # pragma: no cover - sidecar-only
        logger.error("Sidecar transcription error: %s", exc, exc_info=True)
        raise fastapi.HTTPException(status_code=500, detail=str(exc))


if __name__ == "__main__":  # pragma: no cover - manual launch
    port = int(os.getenv("WHISPERX_HTTP_PORT", "5001"))
    uvicorn.run(app, host="127.0.0.1", port=port)
