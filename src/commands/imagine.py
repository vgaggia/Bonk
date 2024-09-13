import discord
import random
from src import log
from src.art import video_generation, utils

logger = log.setup_logger(__name__)

async def handle_imagine(interaction: discord.Interaction, user: discord.Member = None, attachment: discord.Attachment = None):
    await interaction.response.defer(thinking=True)
    
    try:
        image_url = None

        # Check if an attachment was provided with the command
        if attachment:
            image_url = attachment.url
        
        # If no attachment, check if a user was mentioned
        elif user:
            image_url = user.avatar.url if user.avatar else user.default_avatar.url
        
        # If no attachment or user, check if the command is a reply to a message
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
        
        # If still no image, use a random user's avatar
        if not image_url:
            guild_members = interaction.guild.members
            random_user = random.choice(guild_members)
            image_url = random_user.avatar.url if random_user.avatar else random_user.default_avatar.url

        image_path = await utils.download_image_from_url(image_url)
        video_path = await video_generation.image_to_video(image_path)
        file = discord.File(video_path, filename="animated.mp4")
        await interaction.followup.send(content="Here's your animated image:", file=file)

    except Exception as e:
        logger.exception(f"Error in imagine command: {str(e)}")
        await interaction.followup.send(content="An error occurred while processing the image.")