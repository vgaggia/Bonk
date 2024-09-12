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

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Get the current directory
current_dir = os.path.dirname(os.path.abspath(__file__))
# Go up one level to the parent directory
parent_dir = os.path.dirname(current_dir)
# Construct the path to the .env file
env_path = os.path.join(parent_dir, '.env')

# Load the .env file
load_dotenv(dotenv_path=env_path)

# Debug: Print all environment variables
logger.debug("All environment variables:")
for key, value in os.environ.items():
    logger.debug(f"{key}: {value[:5]}{'*' * (len(value) - 5) if len(value) > 5 else ''}")

openai_api_key = os.getenv("OPENAI_API_KEY")
stability_api_key = os.getenv("STABILITY_API_KEY")

def is_valid_openai_key(key):
    return key and key.startswith("sk-") and len(key) > 20

logger.debug(f"OpenAI API Key: {openai_api_key[:5]}{'*' * (len(openai_api_key) - 5) if openai_api_key else 'Not set'}")
logger.debug(f"Stability API Key: {stability_api_key[:5]}{'*' * (len(stability_api_key) - 5) if stability_api_key else 'Not set'}")

if not openai_api_key:
    raise ValueError("OPENAI_API_KEY is not set in the environment variables")
elif not is_valid_openai_key(openai_api_key):
    raise ValueError(f"Invalid OpenAI API key format. Got: {openai_api_key[:5]}...")

if not stability_api_key:
    raise ValueError("STABILITY_API_KEY is not set in the environment variables")

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
                "model": "sd3-large-turbo",
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

# Modified draw function
async def draw(prompt, model_choice="dalle", aspect_ratio="1:1") -> str:
    DATA_DIR = Path.cwd() / "images"
    DATA_DIR.mkdir(exist_ok=True)

    try:
        if model_choice == "dalle":
            logger.debug(f"Attempting to generate image with DALL-E 3. Prompt: {prompt}")
            image_url = await generate_image_dalle(prompt)
            sanitized_prompt = prompt.replace("/", "_").replace("\\", "_")[:50]  # Limit filename length
            image_filename = f"{sanitized_prompt}_dalle.png"
            image_path = DATA_DIR / image_filename
            await download_image(image_url, image_path)
            logger.debug(f"Image generated and saved successfully. Path: {image_path}")
            return str(image_path)
        elif model_choice == "sd":
            logger.debug(f"Attempting to generate image with Stable Diffusion 3. Prompt: {prompt}, Aspect Ratio: {aspect_ratio}")
            image_data = await generate_image_sd(prompt, aspect_ratio)
            sanitized_prompt = prompt.replace("/", "_").replace("\\", "_")[:50]  # Limit filename length
            image_filename = f"{sanitized_prompt}_sd_{aspect_ratio.replace(':', 'x')}.png"
            image_path = DATA_DIR / image_filename
            with open(image_path, "wb") as file:
                file.write(image_data.getvalue())
            logger.debug(f"Image generated and saved successfully. Path: {image_path}")
            return str(image_path)
        else:
            logger.error(f"Invalid model choice: {model_choice}")
            raise ValueError("Invalid model choice. Choose either 'dalle' or 'sd'.")
    except Exception as e:
        logger.error(f"Error in draw function: {str(e)}")
        raise