import os
import json
import requests
from pathlib import Path
from dotenv import load_dotenv
from asgiref.sync import sync_to_async
import openai
import io

load_dotenv()

openai.api_key = os.getenv("OPENAI_API_KEY")
stability_api_key = os.getenv("STABILITY_API_KEY")

# Function to generate an image using DALL-E 3
def generate_image_dalle(prompt):
    try:
        response = openai.Image.create(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",
            quality="hd",
            n=1,
        )
        image_url = response.data[0].url
        return image_url
    except Exception as e:
        raise Exception(f"Error generating image from DALL-E: {str(e)}")

# Function to generate an image using Stable Diffusion (Stability AI)
def generate_image_sd(prompt):
    try:
        response = requests.post(
            f"https://api.stability.ai/v2beta/stable-image/generate/sd3",
            headers={
                "authorization": f"Bearer {stability_api_key}",
                "accept": "image/*"
            },
            files={"none": ''},
            data={
                "prompt": prompt,
                "output_format": "png",
            },
        )

        if response.status_code == 200:
            return io.BytesIO(response.content)
        else:
            error_data = response.json()
            error_name = error_data.get("name")
            error_message = "\n".join(error_data.get("errors", []))

            if error_name == "bad_request":
                raise Exception(f"Bad Request: {error_message}")
            elif error_name == "content_moderation":
                raise Exception("Your request was flagged by the content moderation system and denied.")
            elif error_name == "rate_limit_exceeded":
                raise Exception("You have exceeded the rate limit. Please try again later.")
            elif error_name == "internal_error":
                raise Exception("An unexpected server error occurred. Please try again later.")
            else:
                raise Exception(f"Error generating image from Stability AI: {error_message}")

    except Exception as e:
        raise Exception(f"Error generating image from Stability AI: {str(e)}")

# Function to download an image from a URL and save it as a file
def download_image(image_url, save_path):
    response = requests.get(image_url)
    with open(save_path, "wb") as file:
        file.write(response.content)

# Modified draw function with default model choice
async def draw(prompt, model_choice="dalle") -> str:
    DATA_DIR = Path.cwd()
    DATA_DIR.mkdir(exist_ok=True)

    if model_choice == "dalle":
        try:
            image_url = generate_image_dalle(prompt)
            sanitized_prompt = prompt.replace("/", "_").replace("\\", "_")
            image_filename = f"{sanitized_prompt}_dalle.png"
            image_path = DATA_DIR / "images" / image_filename
            download_image(image_url, image_path)
            return str(image_path)
        except Exception as e:
            raise Exception(f"Error generating image from DALL-E: {str(e)}")
    elif model_choice == "sd":
        try:
            image_data = generate_image_sd(prompt)
            sanitized_prompt = prompt.replace("/", "_").replace("\\", "_")
            image_filename = f"{sanitized_prompt}_sd.png"
            image_path = DATA_DIR / "images" / image_filename
            with open(image_path, "wb") as file:
                file.write(image_data.getvalue())
            return str(image_path)
        except Exception as e:
            raise Exception(f"Error generating image from Stability AI: {str(e)}")
    else:
        raise ValueError("Invalid model choice. Choose either 'dalle' or 'sd'.")