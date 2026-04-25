import asyncio
import hashlib
import io
import logging
import os

import aiohttp
from PIL import Image

logger = logging.getLogger(__name__)

stability_api_key = os.getenv("STABILITY_API_KEY")
IMAGES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'images')


async def image_to_video(image_path):
    try:
        logger.debug(f"Converting image to video: {image_path}")

        # Run blocking IO (file read/PIL) in executor? For now, keep it simple as it's fast.
        # Ideally: await loop.run_in_executor(None, process_image, image_path)

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

        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field('image', img_byte_arr, filename='image.png', content_type='image/png')
            data.add_field('motion_bucket_id', '127')
            data.add_field('seed', '0')
            data.add_field('cfg_scale', '1.8')

            async with session.post(
                "https://api.stability.ai/v2beta/image-to-video",
                headers={"Authorization": f"Bearer {stability_api_key}"},
                data=data,
            ) as response:
                if response.status != 200:
                    text = await response.text()
                    raise Exception(f"Error: {response.status} {text}")

                json_resp = await response.json()
                generation_id = json_resp.get('id')
                logger.debug(f"Video generation started. Generation ID: {generation_id}")

            while True:
                async with session.get(
                    f"https://api.stability.ai/v2beta/image-to-video/result/{generation_id}",
                    headers={"Authorization": f"Bearer {stability_api_key}", "Accept": "video/*"},
                ) as result_response:
                    if result_response.status == 202:
                        logger.debug("Video still processing, waiting...")
                        await asyncio.sleep(10)  # Async sleep!
                    elif result_response.status == 200:
                        logger.debug("Video generated successfully")
                        video_data = await result_response.read()

                        video_hash = hashlib.md5(video_data).hexdigest()

                        video_path = os.path.join(IMAGES_DIR, f"{video_hash}.mp4")
                        with open(video_path, "wb") as video_file:
                            video_file.write(video_data)
                        return video_path
                    else:
                        text = await result_response.text()
                        raise Exception(f"Error fetching video: {result_response.status} {text}")

    except Exception as e:
        logger.error(f"Error generating video: {str(e)}")
        raise e
