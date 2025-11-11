"""
Audio mixing utilities for seamless TTS insertion during music playback.
Handles audio segment extraction, mixing, and duration calculation.
"""

import os
import subprocess
from typing import Optional

from src import log

logger = log.setup_logger(__name__)


def get_audio_duration(file_path: str) -> Optional[float]:
    """
    Get the duration of an audio file in seconds using ffprobe.

    Args:
        file_path: Path to the audio file

    Returns:
        Duration in seconds, or None if unable to determine
    """
    try:
        from src.voice import ffmpeg_executable

        # Use ffprobe (comes with FFmpeg) to get duration
        ffprobe_path = ffmpeg_executable().replace('ffmpeg', 'ffprobe')

        result = subprocess.run(
            [
                ffprobe_path,
                '-v',
                'error',
                '-show_entries',
                'format=duration',
                '-of',
                'default=noprint_wrappers=1:nokey=1',
                file_path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode == 0:
            duration = float(result.stdout.strip())
            return duration
        else:
            logger.error(f"ffprobe error: {result.stderr}")
            return None

    except Exception as e:
        logger.error(f"Error getting audio duration: {e}")
        return None


def extract_segment(input_file: str, output_file: str, start_time: float, duration: float) -> bool:
    """
    Extract a segment from an audio file.

    Args:
        input_file: Source audio file
        output_file: Output file path
        start_time: Start time in seconds
        duration: Duration of segment in seconds

    Returns:
        True if successful, False otherwise
    """
    try:
        from src.voice import ffmpeg_executable

        cmd = [
            ffmpeg_executable(),
            '-y',  # Overwrite output file
            '-ss',
            str(start_time),  # Start time (before input for faster seek)
            '-i',
            input_file,  # Input file
            '-t',
            str(duration),  # Duration
            '-acodec',
            'libmp3lame',  # Re-encode to mp3 (more reliable than copy with seeking)
            '-b:a',
            '192k',  # Bitrate
            '-ar',
            '48000',  # Sample rate (Discord standard)
            output_file,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode == 0:
            logger.info(f"Extracted {duration}s segment from {start_time}s")
            return True
        else:
            logger.error(f"FFmpeg extract error: {result.stderr}")
            return False

    except Exception as e:
        logger.error(f"Error extracting audio segment: {e}")
        return False


def mix_audio(
    music_file: str,
    tts_file: str,
    output_file: str,
    music_volume: float = 0.3,
    tts_volume: float = 1.0,
) -> bool:
    """
    Mix two audio files with specified volume levels.

    Args:
        music_file: Background music file
        tts_file: TTS audio file to overlay
        output_file: Output mixed file path
        music_volume: Volume level for music (0.0-1.0)
        tts_volume: Volume level for TTS (0.0-1.0)

    Returns:
        True if successful, False otherwise
    """
    try:
        from src.voice import ffmpeg_executable

        # Build filter complex for mixing with volume adjustment
        filter_complex = (
            f"[0:a]volume={music_volume}[music];"
            f"[1:a]volume={tts_volume}[tts];"
            f"[music][tts]amix=inputs=2:duration=first:dropout_transition=2"
        )

        cmd = [
            ffmpeg_executable(),
            '-y',  # Overwrite output file
            '-i',
            music_file,  # Input 1: music
            '-i',
            tts_file,  # Input 2: TTS
            '-filter_complex',
            filter_complex,
            '-ac',
            '2',  # Stereo output
            '-ar',
            '48000',  # 48kHz sample rate (Discord standard)
            output_file,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

        if result.returncode == 0:
            logger.info(f"Mixed audio: music({music_volume}) + TTS({tts_volume})")
            return True
        else:
            logger.error(f"FFmpeg mix error: {result.stderr}")
            return False

    except Exception as e:
        logger.error(f"Error mixing audio: {e}")
        return False


def create_mixed_tts_segment(
    music_file: str,
    tts_file: str,
    current_position: float,
    segment_duration: float = 30.0,
    output_file: str = "temp_mixed_tts.mp3",
) -> Optional[str]:
    """
    High-level function to create a mixed segment for TTS insertion.

    Args:
        music_file: Current music file
        tts_file: TTS audio file
        current_position: Current playback position in seconds
        segment_duration: Length of segment to extract (default 30s)
        output_file: Output file path

    Returns:
        Path to mixed file if successful, None otherwise
    """
    temp_segment = "temp_music_segment.mp3"

    try:
        # Step 1: Extract music segment
        if not extract_segment(music_file, temp_segment, current_position, segment_duration):
            return None

        # Step 2: Mix music segment with TTS
        if not mix_audio(temp_segment, tts_file, output_file):
            return None

        # Clean up temporary segment
        if os.path.exists(temp_segment):
            os.remove(temp_segment)

        return output_file

    except Exception as e:
        logger.error(f"Error creating mixed TTS segment: {e}")

        # Clean up temp files on error
        if os.path.exists(temp_segment):
            os.remove(temp_segment)
        if os.path.exists(output_file):
            os.remove(output_file)

        return None
