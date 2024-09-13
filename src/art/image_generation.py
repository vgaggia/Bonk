import os
import requests
import io
from PIL import Image
import replicate
import logging
from openai import OpenAI
from dotenv import load_dotenv

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
        
        sanitized_prompt = prompt.replace("/", "_").replace("\\", "_")[:50]
        image_filename = f"{sanitized_prompt}_dalle.png"
        image_path = os.path.join(IMAGES_DIR, image_filename)
        
        response = requests.get(image_url)
        with open(image_path, "wb") as file:
            file.write(response.content)
        
        return image_path
    except Exception as e:
        logger.error(f"Error generating image from DALL-E 3: {str(e)}")
        raise

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
            sanitized_prompt = prompt.replace("/", "_").replace("\\", "_")[:50]
            image_filename = f"{sanitized_prompt}_sd_{aspect_ratio.replace(':', 'x')}.png"
            image_path = os.path.join(IMAGES_DIR, image_filename)
            
            with open(image_path, "wb") as file:
                file.write(response.content)
            
            return image_path
        else:
            raise Exception(f"Error: {response.status_code} {response.text}")

    except Exception as e:
        logger.error(f"Error generating image from Stability AI: {str(e)}")
        raise

async def generate_image_replicate(prompt, aspect_ratio):
    try:
        logger.debug(f"Generating image with Replicate (black-forest-labs/flux-pro). Prompt: {prompt}, Aspect Ratio: {aspect_ratio}")
        
        prediction = replicate.run(
            "black-forest-labs/flux-pro",
            input={
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "steps": 25,
                "guidance": 3,
                "interval": 2,
                "output_format": "webp",
                "output_quality": 80,
                "safety_tolerance": 2
            }
        )

        if isinstance(prediction, list) and len(prediction) > 0:
            output_url = prediction[0]
        elif isinstance(prediction, str):
            output_url = prediction
        else:
            raise Exception(f"Unexpected output format: {prediction}")

        if not output_url.startswith(('http://', 'https://')):
            raise Exception(f"Invalid image URL returned: {output_url}")

        logger.debug(f"Replicate image generated successfully. URL: {output_url}")
        
        sanitized_prompt = prompt.replace("/", "_").replace("\\", "_")[:50]
        image_filename = f"{sanitized_prompt}_replicate_{aspect_ratio.replace(':', 'x')}.webp"
        image_path = os.path.join(IMAGES_DIR, image_filename)
        
        response = requests.get(output_url)
        with open(image_path, "wb") as file:
            file.write(response.content)
        
        return image_path

    except Exception as e:
        logger.error(f"Error generating image from Replicate: {str(e)}")
        raise