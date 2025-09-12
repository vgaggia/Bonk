"""
Quick test to verify DALL-E aspect ratio mapping
"""
import sys
import os

# Add src directory to path
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

def test_dalle_aspect_ratios():
    """Test that DALL-E aspect ratios map to correct sizes"""
    from src.art.image_generation import _DALLE_ASPECT_RATIO_TO_SIZE
    
    print("🧪 Testing DALL-E Aspect Ratio Mapping...")
    print("=" * 50)
    
    expected_mappings = {
        "1:1": "1024x1024",     # Square
        "16:9": "1792x1024",    # Landscape 
        "9:16": "1024x1792",    # Portrait
        "3:2": "1792x1024",     # Landscape
        "2:3": "1024x1792",     # Portrait
        "21:9": "1792x1024",    # Ultra-wide
        "9:21": "1024x1792"     # Ultra-tall
    }
    
    all_passed = True
    
    for aspect_ratio, expected_size in expected_mappings.items():
        actual_size = _DALLE_ASPECT_RATIO_TO_SIZE.get(aspect_ratio)
        
        if actual_size == expected_size:
            print(f"✅ {aspect_ratio:<5} → {actual_size}")
        else:
            print(f"❌ {aspect_ratio:<5} → Expected: {expected_size}, Got: {actual_size}")
            all_passed = False
    
    print("=" * 50)
    if all_passed:
        print("🎉 All DALL-E aspect ratio mappings are correct!")
    else:
        print("⚠️  Some aspect ratio mappings need fixing!")
    
    return all_passed

if __name__ == "__main__":
    test_dalle_aspect_ratios()
