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
            file = discord.File(video_path, filename="video.mp4")
            await interaction.followup.send(content="Here's your generated video:", file=file)
            
            self.clear_items()
            await interaction.message.edit(view=self)
        except Exception as e:
            logger.exception(f"Error in generate_video_button: {str(e)}")
            await interaction.followup.send(content="An error occurred while generating the video.")