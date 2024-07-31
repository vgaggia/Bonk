import os
import json
import requests
import base64
from pathlib import Path
from dotenv import load_dotenv
from asgiref.sync import sync_to_async
from openai import OpenAI
import io

load_dotenv()

openai_api_key = os.getenv("OPENAI_API_KEY")
stability_api_key = os.getenv("STABILITY_API_KEY")

if not openai_api_key:
    raise ValueError("OPENAI_API_KEY is not set in the environment variables")

if not stability_api_key:
    raise ValueError("STABILITY_API_KEY is not set in the environment variables")

openai_client = OpenAI(api_key=openai_api_key)

# Function to generate an image using DALL-E 3
def generate_image_dalle(prompt):
    try:
        response = openai_client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",
            quality="standard",
            n=1,
        )
        image_url = response.data[0].url
        return image_url
    except Exception as e:
        raise Exception(f"Error generating image from DALL-E: {str(e)}")

# Function to generate an image using Stable Diffusion (Stability AI)
def generate_image_sd(prompt, aspect_ratio="1:1"):
    try:
        # Map aspect ratios to dimensions
        aspect_ratio_dimensions = {
            "1:1": (1024, 1024),
            "4:3": (1024, 768),
            "3:4": (768, 1024),
            "16:9": (1024, 576)
        }
        
        width, height = aspect_ratio_dimensions.get(aspect_ratio, (1024, 1024))
        
        response = requests.post(
            "https://api.stability.ai/v1/generation/stable-diffusion-xl-1024-v1-0/text-to-image",
            headers={
                "Authorization": f"Bearer {stability_api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            },
            json={
                "text_prompts": [{"text": prompt}],
                "cfg_scale": 7,
                "height": height,
                "width": width,
                "samples": 1,
                "steps": 30,
            },
        )

        if response.status_code == 200:
            data = response.json()
            image_base64 = data["artifacts"][0]["base64"]
            return io.BytesIO(base64.b64decode(image_base64))
        else:
            raise Exception(f"Error: {response.status_code} {response.text}")

    except Exception as e:
        raise Exception(f"Error generating image from Stability AI: {str(e)}")

# Function to download an image from a URL and save it as a file
def download_image(image_url, save_path):
    response = requests.get(image_url)
    with open(save_path, "wb") as file:
        file.write(response.content)

# Modified draw function
async def draw(prompt, model_choice="dalle", aspect_ratio="1:1") -> str:
    DATA_DIR = Path.cwd() / "images"
    DATA_DIR.mkdir(exist_ok=True)

    if model_choice == "dalle":
        try:
            image_url = generate_image_dalle(prompt)
            sanitized_prompt = prompt.replace("/", "_").replace("\\", "_")[:50]  # Limit filename length
            image_filename = f"{sanitized_prompt}_dalle.png"
            image_path = DATA_DIR / image_filename
            download_image(image_url, image_path)
            return str(image_path)
        except Exception as e:
            raise Exception(f"Error generating image from DALL-E: {str(e)}")
    elif model_choice == "sd":
        try:
            image_data = generate_image_sd(prompt, aspect_ratio)
            sanitized_prompt = prompt.replace("/", "_").replace("\\", "_")[:50]  # Limit filename length
            image_filename = f"{sanitized_prompt}_sd_{aspect_ratio.replace(':', 'x')}.png"
            image_path = DATA_DIR / image_filename
            with open(image_path, "wb") as file:
                file.write(image_data.getvalue())
            return str(image_path)
        except Exception as e:
            raise Exception(f"Error generating image from Stability AI: {str(e)}")
    else:
        raise ValueError("Invalid model choice. Choose either 'dalle' or 'sd'.")