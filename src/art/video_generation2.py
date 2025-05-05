import os
import requests
import io
from PIL import Image
import time
import hashlib
import logging
from .error_handler import display_error

logger = logging.getLogger(__name__)

stability_api_key = os.getenv("STABILITY_API_KEY")
IMAGES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'images')

async def image_to_video(image_path):
    try:
        logger.debug(f"Converting image to video: {image_path}")
        
        with open(image_path, "rb") as image_file:
            image_data = image_file.read()
        
        with Image.open(io.BytesIO(image_data)) as img:
            if img.mode == 'RGBA':
                img = img.convert('RGB')
            
            aspect_ratio = img.width / img.height
            if aspect_ratio > 1:
                new_size = (1024, 576)
            elif aspect_ratio < 1:
                new_size = (576, 1024)
            else:
                new_size = (768, 768)
            
            resized_img = img.resize(new_size, Image.LANCZOS)
            
            img_byte_arr = io.BytesIO()
            resized_img.save(img_byte_arr, format='PNG')
            img_byte_arr = img_byte_arr.getvalue()

        response = requests.post(
            "https://api.stability.ai/v2beta/image-to-video",
            headers={
                "Authorization": f"Bearer {stability_api_key}"
            },
            files={
                "image": ("image.png", img_byte_arr, "image/png")
            },
            data={
                "motion_bucket_id": 128,
                "seed": 0,
                "cfg_scale": 1.8,
                "motion_bucket_id": 127
            }
        )

        if response.status_code != 200:
            raise Exception(f"Error: {response.status_code} {response.text}")

        generation_id = response.json().get('id')
        logger.debug(f"Video generation started. Generation ID: {generation_id}")

        while True:
            result_response = requests.get(
                f"https://api.stability.ai/v2beta/image-to-video/result/{generation_id}",
                headers={
                    "Authorization": f"Bearer {stability_api_key}",
                    "Accept": "video/*"
                }
            )

            if result_response.status_code == 202:
                logger.debug("Video still processing, waiting...")
                time.sleep(10)
            elif result_response.status_code == 200:
                logger.debug("Video generated successfully")
                video_data = result_response.content
                
                video_hash = hashlib.md5(video_data).hexdigest()
                
                video_path = os.path.join(IMAGES_DIR, f"{video_hash}.mp4")
                with open(video_path, "wb") as video_file:
                    video_file.write(video_data)
                return video_path
            else:
                raise Exception(f"Error fetching video: {result_response.status_code} {result_response.text}")

    except Exception as e:
        logger.error(f"Error generating video: {str(e)}")
        return display_error(e)