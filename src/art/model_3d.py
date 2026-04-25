import os

import aiohttp

from src import log
from src.error_handler import ContentModerationError

logger = log.setup_logger(__name__)

stability_api_key = os.getenv("STABILITY_API_KEY")
IMAGES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'images')


async def generate_3d_model(image_path):
    try:
        logger.debug(f"Generating 3D model from image: {image_path}")

        with open(image_path, "rb") as image_file:
            image_data = image_file.read()

        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field('image', image_data, filename='image.png')
            data.add_field('texture_resolution', '1024')
            data.add_field('foreground_ratio', '0.85')
            data.add_field('remesh', 'none')

            async with session.post(
                "https://api.stability.ai/v2beta/3d/stable-fast-3d",
                headers={
                    "Authorization": f"Bearer {stability_api_key}",
                },
                data=data,
            ) as response:
                if response.status == 403:
                    try:
                        error_data = await response.json()
                    except Exception:
                        error_data = {}

                    if "content_moderation" in error_data.get("name", ""):
                        raise ContentModerationError("Content moderation flagged the image")
                    else:
                        text = await response.text()
                        raise Exception(f"Error: {response.status} {text}")
                elif response.status != 200:
                    text = await response.text()
                    raise Exception(f"Error: {response.status} {text}")

                logger.debug("3D model generated successfully")
                model_data = await response.read()

        model_path = os.path.join(IMAGES_DIR, os.path.basename(image_path).replace(".png", ".glb"))
        with open(model_path, "wb") as model_file:
            model_file.write(model_data)
        return model_path

    except Exception as e:
        logger.error(f"Error generating 3D model: {str(e)}")
        raise
