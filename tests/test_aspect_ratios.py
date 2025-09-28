"""
Tests for DALL-E aspect ratio mapping.

Uses direct assertions for pytest compatibility.
"""

from src.art.image_generation import _DALLE_ASPECT_RATIO_TO_SIZE


def test_dalle_aspect_ratios():
    expected_mappings = {
        "1:1": "1024x1024",  # Square
        "16:9": "1792x1024",  # Landscape
        "9:16": "1024x1792",  # Portrait
        "3:2": "1792x1024",  # Landscape
        "2:3": "1024x1792",  # Portrait
        "21:9": "1792x1024",  # Ultra-wide
        "9:21": "1024x1792",  # Ultra-tall
    }

    for aspect_ratio, expected_size in expected_mappings.items():
        assert (
            _DALLE_ASPECT_RATIO_TO_SIZE.get(aspect_ratio) == expected_size
        ), f"Mapping for {aspect_ratio} should be {expected_size}"

