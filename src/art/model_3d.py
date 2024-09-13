import os
import requests
import logging

logger = logging.getLogger(__name__)

stability_api_key = os.getenv("STABILITY_API_KEY")
IMAGES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'images')

async def generate_3d_model(image_path):
    try:
        logger.debug(f"Generating 3D model from image: {image_path}")
        
        with open(image_path, "rb") as image_file:
            response = requests.post(
                "https://api.stability.ai/v2beta/3d/stable-fast-3d",
                headers={
                    "Authorization": f"Bearer {stability_api_key}",
                },
                files={
                    "image": image_file
                },
                data={
                    "texture_resolution": "1024",
                    "foreground_ratio": 0.85,
                    "remesh": "none"
                }
            )

        if response.status_code != 200:
            raise Exception(f"Error: {response.status_code} {response.text}")

        logger.debug("3D model generated successfully")
        model_data = response.content
        model_path = os.path.join(IMAGES_DIR, os.path.basename(image_path).replace(".png", ".glb"))
        with open(model_path, "wb") as model_file:
            model_file.write(model_data)
        return model_path

    except Exception as e:
        logger.error(f"Error generating 3D model: {str(e)}")
        raise