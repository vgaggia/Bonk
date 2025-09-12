import os
import discord
from src import log
from src.art import video_generation

logger = log.setup_logger(__name__)

class GenerateVideoView(discord.ui.View):
    def __init__(self, image_path):
        super().__init__()
        self.image_path = image_path

    @discord.ui.button(label="Generate Video", style=discord.ButtonStyle.success)
    async def generate_video_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(thinking=True)
        try:
            video_path = await video_generation.image_to_video(self.image_path)
            
            # Verify the file exists
            if not os.path.exists(video_path):
                logger.error(f"Video file not found: {video_path}")
                await interaction.followup.send(content="> **Error: Generated video file not found**")
                return
                
            file = discord.File(video_path, filename="video.mp4")
            await interaction.followup.send(content="Here's your generated video:", file=file)
            
            self.clear_items()
            try:
                await interaction.message.edit(view=self)
            except (discord.errors.NotFound, discord.errors.InteractionResponded):
                logger.warning("Cannot edit message - interaction expired or already responded")
        except Exception as e:
            logger.exception(f"Error in generate_video_button: {str(e)}")
            # Use the improved error handler to get a better error message
            from src.art.error_handler import handle_error
            error_message = handle_error(e)
            await interaction.followup.send(content=f"> **Error generating video: {error_message}**")