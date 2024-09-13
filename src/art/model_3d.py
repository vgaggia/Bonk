import os
import requests
import discord
from src import log
from src.art import utils
from .error_handler import display_error

logger = log.setup_logger(__name__)

stability_api_key = os.getenv("STABILITY_API_KEY")
IMAGES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'images')

class ContentModerationError(Exception):
    pass

async def handle_3d(interaction: discord.Interaction, user: discord.Member = None, attachment: discord.Attachment = None):
    await interaction.response.defer(thinking=True)
    
    try:
        image_url = None
        
        if attachment:
            image_url = attachment.url
        elif interaction.message and interaction.message.reference:
            replied_message = await interaction.channel.fetch_message(interaction.message.reference.message_id)
            
            if replied_message.attachments:
                image_url = replied_message.attachments[0].url
            elif replied_message.embeds:
                embed = replied_message.embeds[0]
                if embed.image:
                    image_url = embed.image.url
                elif embed.thumbnail:
                    image_url = embed.thumbnail.url
        elif user:
            image_url = user.avatar.url if user.avatar else user.default_avatar.url
        
        if image_url:
            image_path = await utils.download_image_from_url(image_url)
            model_path = await generate_3d_model(image_path)
            file = discord.File(model_path, filename="3d_model.glb")
            await interaction.followup.send(content="Here's your generated 3D model:", file=file)
        else:
            await interaction.followup.send("Please provide a user, attach an image, or reply to a message with an image to generate a 3D model.")

    except ContentModerationError as e:
        logger.warning(f"Content moderation error in 3d command: {str(e)}")
        await interaction.followup.send(content="Your request was flagged by the content moderation system and cannot be processed. Please try a different image.")
    except Exception as e:
        logger.exception(f"Error in 3d command: {str(e)}")
        error_message = display_error(e)
        await interaction.followup.send(content=f"An error occurred while generating the 3D model: {error_message}")

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

        if response.status_code == 403:
            error_data = response.json()
            if "content_moderation" in error_data.get("name", ""):
                raise ContentModerationError("Content moderation flagged the image")
            else:
                raise Exception(f"Error: {response.status_code} {response.text}")
        elif response.status_code != 200:
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