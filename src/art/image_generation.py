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
def truncate_prompt(self, prompt, max_length=250):
    if len(prompt) <= max_length:
        return prompt
    return prompt[:max_length] + "..."

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
        
        # Prepare multipart/form-data request
        files = {
            'none': ''  # Empty file as required by API
        }
        
        data = {
            'prompt': prompt,
            'aspect_ratio': aspect_ratio,
            'mode': 'text-to-image',
            'model': 'sd3.5-large',
            'output_format': 'png',
            'stability-client-id': 'BonkBot',
            'stability-client-user-id': 'DiscordUser',
            'stability-client-version': '1.0.0'
        }
        
        response = requests.post(
            "https://api.stability.ai/v2beta/stable-image/generate/sd3",
            headers={
                "Authorization": f"Bearer {stability_api_key}",
                "Accept": "image/*"
            },
            files=files,
            data=data
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
        else:
            # Enhanced error handling
            try:
                error_details = response.json()
                error_message = error_details.get('message', 'Unknown error')
                logger.error(f"SD3 Generation Error: {response.status_code} - {error_message}")
                raise Exception(f"Error generating image: {error_message}")
            except (ValueError, KeyError):
                logger.error(f"SD3 Generation Error: {response.status_code} - {response.text}")
                raise Exception(f"Unexpected error: {response.status_code}")

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

# --- GPT Image 1 Integration ---
# Aspect ratio mapping for OpenAI Images API
_ASPECT_RATIO_TO_SIZE = {
    "1:1": "1024x1024",
    "16:9": "1536x864",
    "9:16": "864x1536",
    "4:5": "1024x1280",
    "5:4": "1280x1024",
    "3:2": "1536x1024",
    "2:3": "1024x1536",
    "21:9": "1536x672",
    "9:21": "672x1536"
}

async def generate_image_gpt_image_1(prompt, aspect_ratio):
    try:
        logger.debug(f"Generating image with GPT Image 1. Prompt: {prompt}, Aspect Ratio: {aspect_ratio}")
        size = _ASPECT_RATIO_TO_SIZE.get(aspect_ratio, "1024x1024")
        
        # Direct API call with better logging
        logger.info(f"Calling OpenAI API with model=gpt-image-1, size={size}")
        try:
            response = openai_client.images.generate(
                model="gpt-image-1",
                prompt=prompt,
                size=size,
                n=1,
            )
            logger.info(f"OpenAI API response received: {response}")
        except Exception as api_error:
            logger.error(f"OpenAI API call failed: {str(api_error)}")
            raise Exception(f"Image generation failed: {str(api_error)}")

        # Handle both URL and base64 responses
        if not response or not hasattr(response, 'data') or not response.data or not response.data[0]:
            logger.error("OpenAI API returned invalid response")
            raise Exception("No response received from OpenAI API")

        image_url = getattr(response.data[0], "url", None)
        b64_json = getattr(response.data[0], "b64_json", None)

        if image_url:
            logger.debug(f"GPT Image 1 image generated successfully. URL: {image_url}")
            try:
                img_response = requests.get(image_url, timeout=10)
                img_response.raise_for_status()
                image_data = img_response.content
            except requests.exceptions.RequestException as e:
                logger.error(f"Error downloading image from URL {image_url}: {str(e)}")
                raise Exception(f"Error downloading the generated image: {str(e)}")
        elif b64_json:
            logger.debug("GPT Image 1 image generated as base64, decoding and saving as PNG.")
            import base64
            image_data = base64.b64decode(b64_json)
        else:
            logger.error("No image URL or base64 data found in OpenAI API response")
            raise Exception("No image URL or base64 data in OpenAI API response")

        image_hash = hashlib.md5(image_data).hexdigest()
        image_filename = f"{image_hash}.png"
        image_path = os.path.join(IMAGES_DIR, image_filename)

        with open(image_path, "wb") as file:
            file.write(image_data)

        return image_data, image_path
    except Exception as e:
        logger.error(f"Error generating image from GPT Image 1: {str(e)}")
        return display_error(e)

# --- GPT Image 1 Edit Integration ---
async def generate_image_gpt_image_1_edit(image_path, instruction, aspect_ratio):
    try:
        logger.debug(f"Editing image with GPT Image 1. Edit: {instruction}, Aspect Ratio: {aspect_ratio}")
        size = _ASPECT_RATIO_TO_SIZE.get(aspect_ratio, "1024x1024")
        
        # Read the image file
        with open(image_path, "rb") as img_file:
            image_bytes = img_file.read()

        # Set up a BytesIO with a .name attribute for mimetype detection
        image_file = io.BytesIO(image_bytes)
        image_file.name = "image.png"

        # Direct API call with better logging
        logger.info(f"Calling OpenAI Edit API with model=gpt-image-1, size={size}")
        try:
            response = openai_client.images.edit(
                model="gpt-image-1",
                image=image_file,
                prompt=instruction,
                size=size,
                n=1,
            )
            logger.info(f"OpenAI Edit API response received: {response}")
        except Exception as api_error:
            logger.error(f"OpenAI Edit API call failed: {str(api_error)}")
            raise Exception(f"Image editing failed: {str(api_error)}")

        # Handle both URL and base64 responses
        if not response or not hasattr(response, 'data') or not response.data or not response.data[0]:
            logger.error("OpenAI Edit API returned invalid response")
            raise Exception("No response received from OpenAI Edit API")

        image_url = getattr(response.data[0], "url", None)
        b64_json = getattr(response.data[0], "b64_json", None)

        if image_url:
            logger.debug(f"GPT Image 1 image edit generated successfully. URL: {image_url}")
            try:
                img_response = requests.get(image_url, timeout=10)
                img_response.raise_for_status()
                image_data = img_response.content
            except requests.exceptions.RequestException as e:
                logger.error(f"Error downloading edited image from URL {image_url}: {str(e)}")
                raise Exception(f"Error downloading the edited image: {str(e)}")
        elif b64_json:
            logger.debug("GPT Image 1 image edit generated as base64, decoding and saving as PNG.")
            import base64
            image_data = base64.b64decode(b64_json)
        else:
            logger.error("No image URL or base64 data found in OpenAI Edit API response")
            raise Exception("No image URL or base64 data in OpenAI Edit API response")

        image_hash = hashlib.md5(image_data).hexdigest()
        image_filename = f"{image_hash}.png"
        new_image_path = os.path.join(IMAGES_DIR, image_filename)

        with open(new_image_path, "wb") as file:
            file.write(image_data)

        return image_data, new_image_path
    except Exception as e:
        logger.error(f"Error editing image with GPT Image 1: {str(e)}")
        return display_error(e)
