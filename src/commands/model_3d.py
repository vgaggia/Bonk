import discord
from src import log
from src.art import model_3d, utils
from src.art.error_handler import display_error

logger = log.setup_logger(__name__)

async def handle_3d(interaction: discord.Interaction, user: discord.Member = None, attachment: discord.Attachment = None):
    try:
        image_url = None
        
        # Check if an attachment was provided with the command
        if attachment:
            image_url = attachment.url
        
        # If no attachment, check if the command is a reply to a message
        elif interaction.message and interaction.message.reference:
            replied_message = await interaction.channel.fetch_message(interaction.message.reference.message_id)
            
            # Check for attachments in the replied message
            if replied_message.attachments:
                image_url = replied_message.attachments[0].url
            # Check for embeds in the replied message
            elif replied_message.embeds:
                embed = replied_message.embeds[0]
                if embed.image:
                    image_url = embed.image.url
                elif embed.thumbnail:
                    image_url = embed.thumbnail.url
        
        # If still no image, use user avatar if provided
        elif user:
            image_url = user.avatar.url if user.avatar else user.default_avatar.url
        
        # If we have an image URL, process it
        if image_url:
            await interaction.followup.send("Generating 3D model... This may take a moment.")
            image_path = await utils.download_image_from_url(image_url)
            model_path = await model_3d.generate_3d_model(image_path)
            file = discord.File(model_path, filename="3d_model.glb")
            await interaction.followup.send(content="Here's your generated 3D model:", file=file)
        else:
            await interaction.followup.send("Please provide a user, attach an image, or reply to a message with an image to generate a 3D model.")

    except Exception as e:
        logger.exception(f"Error in 3d command: {str(e)}")
        error_message = display_error(e)
        await interaction.followup.send(content=error_message)