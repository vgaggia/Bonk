import hashlib
import io
import logging
import os

import replicate
import requests
from dotenv import load_dotenv
from openai import OpenAI

from .error_handler import ContentModerationError, display_error

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

# --- DALL-E 3 Integration with Aspect Ratio Support ---
# DALL-E 3 supported sizes mapping from aspect ratios
# DALL-E 3 only supports: 1024x1024, 1024x1792, 1792x1024
_DALLE_ASPECT_RATIO_TO_SIZE = {
    "1:1": "1024x1024",   # Native square
    "16:9": "1792x1024",  # Landscape - close to 16:9 
    "9:16": "1024x1792",  # Portrait - close to 9:16
    "4:5": "1024x1792",   # Portrait - closest available 
    "5:4": "1792x1024",   # Landscape - closest available
    "3:2": "1792x1024",   # Landscape - close to 3:2
    "2:3": "1024x1792",   # Portrait - close to 2:3  
    "21:9": "1792x1024",  # Ultra-wide landscape
    "9:21": "1024x1792"   # Ultra-tall portrait
}

async def generate_image_dalle(prompt, aspect_ratio=None):
    try:
        # Default to square if no aspect ratio provided (for backward compatibility)
        size = "1024x1024" if aspect_ratio is None else _DALLE_ASPECT_RATIO_TO_SIZE.get(aspect_ratio, "1024x1024")
        
        logger.debug(f"Generating image with DALL-E 3. Prompt: {prompt}, Size: {size}")
        response = openai_client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size=size,
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

async def generate_image_replicate(prompt, aspect_ratio, model_id="black-forest-labs/flux-schnell"):
    try:
        logger.debug(f"Generating image with Replicate ({model_id}). Prompt: {prompt}, Aspect Ratio: {aspect_ratio}")
        
        # Default input parameters that work with most models
        input_params = {
            "prompt": prompt,
        }
        
        # Add aspect ratio if supported (mainly for newer models)
        if "flux" in model_id.lower() or "sdxl" in model_id.lower():
            input_params["aspect_ratio"] = aspect_ratio
        
        # Model-specific parameters
        if "flux-schnell" in model_id:
            input_params.update({
                "steps": 25,
                "guidance": 3,
                "interval": 2,
                "output_format": "webp",
                "output_quality": 100,
                "disable_safety_checker": True,
            })
        elif "flux-dev" in model_id:
            input_params.update({
                "guidance": 3.5,
                "num_outputs": 1,
                "output_format": "webp",
                "output_quality": 100
            })
        elif "sdxl" in model_id:
            input_params.update({
                "width": 1024,
                "height": 1024,
                "num_outputs": 1,
                "scheduler": "K_EULER",
                "num_inference_steps": 50,
                "guidance_scale": 7.5
            })
        
        prediction = replicate.run(model_id, input=input_params)
        
        logger.debug(f"Raw prediction from Replicate: {prediction} (type: {type(prediction)})")

        # Handle various Replicate output formats
        output_url = None
        
        if isinstance(prediction, list):
            # List of outputs - take the first one
            if len(prediction) > 0:
                output_url = prediction[0]
            else:
                raise Exception("Empty output list from Replicate")
        elif isinstance(prediction, str):
            # Direct string URL
            output_url = prediction
        else:
            # Try to extract URL from object (FileOutput, etc.)
            output_url = prediction
        
        # Handle FileOutput objects and other objects with URL attributes
        if hasattr(output_url, 'url'):
            output_url = output_url.url
        elif hasattr(output_url, 'read') and hasattr(output_url, '__str__'):
            # FileOutput object - convert to string to get URL
            output_url = str(output_url)
        elif not isinstance(output_url, str):
            # Try to convert to string
            try:
                output_url = str(output_url)
            except Exception as exc:
                raise Exception(
                    f"Cannot extract URL from output: {type(output_url)} - {output_url}"
                ) from exc
        
        # Validate the URL
        if not isinstance(output_url, str):
            raise Exception(f"Invalid output URL format after processing: {type(output_url)}")
        
        if not output_url.startswith(('http://', 'https://')):
            if 'error' in output_url.lower() and 'safety' in output_url.lower():
                raise ContentModerationError("The image was flagged by content moderation.")
            raise Exception(f"Invalid image URL returned: {output_url}")
        
        # Additional validation - make sure it looks like a proper URL
        if not ('.' in output_url and len(output_url) > 10):
            raise Exception(f"Malformed image URL: {output_url}")

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
