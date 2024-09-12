import os
import json
import requests
import base64
from pathlib import Path
from dotenv import load_dotenv
from asgiref.sync import sync_to_async
from openai import OpenAI
import io
import logging
import time
from PIL import Image
import aiohttp
import replicate
import asyncio
import random

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Get the current directory
current_dir = os.path.dirname(os.path.abspath(__file__))
# Construct the path to the images folder
IMAGES_DIR = os.path.join(current_dir, 'images')
# Create the images folder if it doesn't exist
os.makedirs(IMAGES_DIR, exist_ok=True)

# Construct the path to the .env file (one level up from current directory)
env_path = os.path.join(os.path.dirname(current_dir), '.env')

# Load the .env file
load_dotenv(dotenv_path=env_path)

# Debug: Print all environment variables
logger.debug("All environment variables:")
for key, value in os.environ.items():
    logger.debug(f"{key}: {value[:5]}{'*' * (len(value) - 5) if len(value) > 5 else ''}")

openai_api_key = os.getenv("OPENAI_API_KEY")
stability_api_key = os.getenv("STABILITY_API_KEY")
replicate_api_token = os.getenv("REPLICATE_API_TOKEN")

def is_valid_openai_key(key):
    return key and key.startswith("sk-") and len(key) > 20

logger.debug(f"OpenAI API Key: {openai_api_key[:5]}{'*' * (len(openai_api_key) - 5) if openai_api_key else 'Not set'}")
logger.debug(f"Stability API Key: {stability_api_key[:5]}{'*' * (len(stability_api_key) - 5) if stability_api_key else 'Not set'}")
logger.debug(f"Replicate API Token: {replicate_api_token[:5]}{'*' * (len(replicate_api_token) - 5) if replicate_api_token else 'Not set'}")

if not openai_api_key:
    raise ValueError("OPENAI_API_KEY is not set in the environment variables")
elif not is_valid_openai_key(openai_api_key):
    raise ValueError(f"Invalid OpenAI API key format. Got: {openai_api_key[:5]}...")

if not stability_api_key:
    raise ValueError("STABILITY_API_KEY is not set in the environment variables")

if not replicate_api_token:
    raise ValueError("REPLICATE_API_TOKEN is not set in the environment variables")

# Initialize OpenAI client
try:
    openai_client = OpenAI(api_key=openai_api_key)
    logger.debug("OpenAI client initialized successfully")
except Exception as e:
    logger.error(f"Error initializing OpenAI client: {str(e)}")
    raise

# Function to generate an image using DALL-E 3
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
        return image_url
    except Exception as e:
        logger.error(f"Error generating image from DALL-E 3: {str(e)}")
        raise

# Function to generate an image using Stable Diffusion (Stability AI)
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
            return io.BytesIO(response.content)
        else:
            raise Exception(f"Error: {response.status_code} {response.text}")

    except Exception as e:
        logger.error(f"Error generating image from Stability AI: {str(e)}")
        raise

# Function to generate an image using Replicate (black-forest-labs/flux-pro)
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
        return output_url

    except Exception as e:
        logger.error(f"Error generating image from Replicate: {str(e)}")
        raise

# Function to download an image from a URL and save it as a file
async def download_image(image_url, save_path):
    try:
        logger.debug(f"Downloading image from URL: {image_url}")
        response = requests.get(image_url)
        response.raise_for_status()  # Raise an exception for bad status codes
        with open(save_path, "wb") as file:
            file.write(response.content)
        logger.debug(f"Image downloaded and saved to: {save_path}")
    except Exception as e:
        logger.error(f"Error downloading image: {str(e)}")
        raise

# New function to download image from URL (for /imagine command)
async def download_image_from_url(url):
    try:
        logger.debug(f"Downloading image from URL: {url}")
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise Exception(f"HTTP error status: {resp.status}")
                image_data = await resp.read()
                
        # Save the image to a temporary file in the src/images folder
        temp_path = os.path.join(IMAGES_DIR, f"temp_image_{int(time.time())}.png")
        with open(temp_path, "wb") as file:
            file.write(image_data)
        logger.debug(f"Image downloaded and saved to: {temp_path}")
        return temp_path
    except Exception as e:
        logger.error(f"Error downloading image from URL: {str(e)}")
        raise

# Modified draw function
async def draw(prompt, model_choice="dalle", aspect_ratio="1:1") -> str:
    try:
        if model_choice == "dalle":
            logger.debug(f"Attempting to generate image with DALL-E 3. Prompt: {prompt}")
            image_url = await generate_image_dalle(prompt)
            sanitized_prompt = prompt.replace("/", "_").replace("\\", "_")[:50]  # Limit filename length
            image_filename = f"{sanitized_prompt}_dalle.png"
            image_path = os.path.join(IMAGES_DIR, image_filename)
            await download_image(image_url, image_path)
            logger.debug(f"Image generated and saved successfully. Path: {image_path}")
            return image_path
        elif model_choice == "sd":
            logger.debug(f"Attempting to generate image with Stable Diffusion 3. Prompt: {prompt}, Aspect Ratio: {aspect_ratio}")
            image_data = await generate_image_sd(prompt, aspect_ratio)
            sanitized_prompt = prompt.replace("/", "_").replace("\\", "_")[:50]  # Limit filename length
            image_filename = f"{sanitized_prompt}_sd_{aspect_ratio.replace(':', 'x')}.png"
            image_path = os.path.join(IMAGES_DIR, image_filename)
            with open(image_path, "wb") as file:
                file.write(image_data.getvalue())
            logger.debug(f"Image generated and saved successfully. Path: {image_path}")
            return image_path
        elif model_choice == "replicate":
            logger.debug(f"Attempting to generate image with Replicate. Prompt: {prompt}, Aspect Ratio: {aspect_ratio}")
            image_url = await generate_image_replicate(prompt, aspect_ratio)
            sanitized_prompt = prompt.replace("/", "_").replace("\\", "_")[:50]  # Limit filename length
            image_filename = f"{sanitized_prompt}_replicate_{aspect_ratio.replace(':', 'x')}.webp"
            image_path = os.path.join(IMAGES_DIR, image_filename)
            await download_image(image_url, image_path)
            logger.debug(f"Image generated and saved successfully. Path: {image_path}")
            return image_path
        else:
            logger.error(f"Invalid model choice: {model_choice}")
            raise ValueError("Invalid model choice. Choose either 'dalle', 'sd', or 'replicate'.")
    except Exception as e:
        logger.error(f"Error in draw function: {str(e)}")
        raise

# Modified generate_video function
async def generate_video(image_path):
    try:
        logger.debug(f"Generating video from image: {image_path}")
        
        # Open the image and resize it to a supported dimension
        with Image.open(image_path) as img:
            # Convert RGBA images to RGB
            if img.mode == 'RGBA':
                img = img.convert('RGB')
            
            # Choose the dimension based on the aspect ratio
            aspect_ratio = img.width / img.height
            if aspect_ratio > 1:  # Landscape
                new_size = (1024, 576)
            elif aspect_ratio < 1:  # Portrait
                new_size = (576, 1024)
            else:  # Square
                new_size = (768, 768)
            
            resized_img = img.resize(new_size, Image.LANCZOS)
            
            # Save the resized image to a temporary file
            temp_path = image_path.replace(".png", "_resized.png")
            resized_img.save(temp_path)
        
        with open(temp_path, "rb") as image_file:
            response = requests.post(
                "https://api.stability.ai/v2beta/image-to-video",
                headers={
                    "Authorization": f"Bearer {stability_api_key}"
                },
                files={
                    "image": image_file
                },
                data={
                    "motion_bucket_id": 128,
                    "seed": 0,
                    "cfg_scale": 1.8,
                    "motion_bucket_id": 127
                }
            )

        # Remove the temporary resized image
        os.remove(temp_path)

        if response.status_code != 200:
            raise Exception(f"Error: {response.status_code} {response.text}")

        generation_id = response.json().get('id')
        logger.debug(f"Video generation started. Generation ID: {generation_id}")

        # Poll for results
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
                time.sleep(10)  # Wait for 10 seconds before polling again
            elif result_response.status_code == 200:
                logger.debug("Video generated successfully")
                video_data = result_response.content
                video_path = os.path.join(IMAGES_DIR, os.path.basename(image_path).replace(".png", ".mp4"))
                with open(video_path, "wb") as video_file:
                    video_file.write(video_data)
                return video_path
            else:
                raise Exception(f"Error fetching video: {result_response.status_code} {result_response.text}")

    except Exception as e:
        logger.error(f"Error generating video: {str(e)}")
        raise