import os
import requests
import io
from PIL import Image
import replicate
import logging
import hashlib
from openai import OpenAI
from dotenv import load_dotenv
from .error_handler import display_error, ContentModerationError

load_dotenv()
logger = logging.getLogger(__name__)

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
stability_api_key = os.getenv("STABILITY_API_KEY")
replicate_api_token = os.getenv("REPLICATE_API_TOKEN")

IMAGES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'images')
os.makedirs(IMAGES_DIR, exist_ok=True)

async def generate_image_dalle(prompt):
    try:
        logger.debug(f"Generating image with DALL-E 3. Prompt: {prompt}")
        response = openai_client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",
            quality="standard",
            n=1,
        )
        image_url = response.data[0].url
        logger.debug(f"DALL-E 3 image generated successfully. URL: {image_url}")
        
        image_data = requests.get(image_url).content
        image_hash = hashlib.md5(image_data).hexdigest()
        image_filename = f"{image_hash}.png"
        image_path = os.path.join(IMAGES_DIR, image_filename)
        
        with open(image_path, "wb") as file:
            file.write(image_data)
        
        return image_data, image_path
    except Exception as e:
        logger.error(f"Error generating image from DALL-E 3: {str(e)}")
        return display_error(e)

async def generate_image_sd(prompt, aspect_ratio):
    try:
        logger.debug(f"Generating image with Stable Diffusion 3. Prompt: {prompt}, Aspect Ratio: {aspect_ratio}")
        
        response = requests.post(
            "https://api.stability.ai/v2beta/stable-image/generate/sd3",
            headers={
                "Authorization": f"Bearer {stability_api_key}",
                "Accept": "image/*"
            },
            files={
                "none": ""
            },
            data={
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "output_format": "png"
            }
        )

        if response.status_code == 200:
            logger.debug("Stable Diffusion 3 image generated successfully")
            image_data = response.content
            image_hash = hashlib.md5(image_data).hexdigest()
            image_filename = f"{image_hash}.png"
            image_path = os.path.join(IMAGES_DIR, image_filename)
            
            with open(image_path, "wb") as file:
                file.write(image_data)
            
            return image_data, image_path
        elif response.status_code == 400:
            error_data = response.json()
            if 'message' in error_data and 'content moderation' in error_data['message'].lower():
                raise ContentModerationError("The image was flagged by content moderation.")
            else:
                raise Exception(f"Bad Request: {error_data.get('message', 'Unknown error')}")
        else:
            raise Exception(f"Error: {response.status_code} {response.text}")

    except ContentModerationError as e:
        logger.error(f"Content moderation error: {str(e)}")
        return display_error(e)
    except requests.RequestException as e:
        logger.error(f"Network error when generating image from Stability AI: {str(e)}")
        return display_error(e)
    except Exception as e:
        logger.error(f"Error generating image from Stability AI: {str(e)}")
        return display_error(e)

async def generate_image_replicate(prompt, aspect_ratio):
    try:
        logger.debug(f"Generating image with Replicate (black-forest-labs/flux-schnell). Prompt: {prompt}, Aspect Ratio: {aspect_ratio}")
        
        prediction = replicate.run(
            "black-forest-labs/flux-schnell",
            input={
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "steps": 25,
                "guidance": 3,
                "interval": 2,
                "output_format": "webp",
                "output_quality": 100,
                "disable_safety_checker": True,
            }
        )

        if isinstance(prediction, list) and len(prediction) > 0:
            output_url = prediction[0]
        elif isinstance(prediction, str):
            output_url = prediction
        else:
            raise Exception(f"Unexpected output format: {prediction}")

        if not output_url.startswith(('http://', 'https://')):
            if 'error' in output_url.lower() and 'safety' in output_url.lower():
                raise ContentModerationError("The image was flagged by content moderation.")
            raise Exception(f"Invalid image URL returned: {output_url}")

        logger.debug(f"Replicate image generated successfully. URL: {output_url}")
        
        image_data = requests.get(output_url).content
        image_hash = hashlib.md5(image_data).hexdigest()
        image_filename = f"{image_hash}.webp"
        image_path = os.path.join(IMAGES_DIR, image_filename)
        
        with open(image_path, "wb") as file:
            file.write(image_data)
        
        return image_data, image_path

    except ContentModerationError as e:
        logger.error(f"Content moderation error: {str(e)}")
        return display_error(e)
    except replicate.exceptions.ReplicateError as e:
        logger.error(f"Replicate API error: {str(e)}")
        return display_error(e)
    except requests.RequestException as e:
        logger.error(f"Network error when generating image from Replicate: {str(e)}")
        return display_error(e)
    except Exception as e:
        logger.error(f"Error generating image from Replicate: {str(e)}")
        return display_error(e)