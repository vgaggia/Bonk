import os
import aiohttp
import logging
import time

logger = logging.getLogger(__name__)

IMAGES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'images')

async def download_image_from_url(url):
    try:
        logger.debug(f"Downloading image from URL: {url}")
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise Exception(f"HTTP error status: {resp.status}")
                image_data = await resp.read()
                
        temp_path = os.path.join(IMAGES_DIR, f"temp_image_{int(time.time())}.png")
        with open(temp_path, "wb") as file:
            file.write(image_data)
        logger.debug(f"Image downloaded and saved to: {temp_path}")
        return temp_path
    except Exception as e:
        logger.error(f"Error downloading image from URL: {str(e)}")
        raise