import discord
import replicate
import os
import requests
from src import log
from src.art.error_handler import display_error
from src.ui.aspect_ratio_view import AspectRatioView

logger = log.setup_logger(__name__)

class VideoLengthSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(value="97", label="Default (4.3s)", description="97 frames - Standard length"),
            discord.SelectOption(value="129", label="Medium (5.7s)", description="129 frames"),
            discord.SelectOption(value="161", label="Long (7.2s)", description="161 frames"),
            discord.SelectOption(value="193", label="Longer (8.6s)", description="193 frames"),
            discord.SelectOption(value="225", label="Very Long (10s)", description="225 frames"),
            discord.SelectOption(value="257", label="Maximum (11.4s)", description="257 frames - Longest possible")
        ]
        super().__init__(placeholder="Select video length", options=options, min_values=1, max_values=1)

class VideoButtons(discord.ui.View):
    def __init__(self, prompt, interaction):
        super().__init__(timeout=60.0)
        self.prompt = prompt
        self.interaction = interaction
        self.aspect_ratio_view = None
        self.interaction_completed = False
        self.video_length = 97  # Default length (documentation default)
        
        # Add length selector
        self.length_select = VideoLengthSelect()
        self.length_select.callback = self.length_callback
        self.add_item(self.length_select)

    async def length_callback(self, interaction: discord.Interaction):
        self.video_length = int(self.length_select.values[0])
        await interaction.response.defer()
        seconds = self.video_length / 22.5  # Convert frames to seconds (observed frame rate)
        await interaction.edit_original_response(content=f"Video length set to {seconds:.1f} seconds. Now select the aspect ratio:", view=self.aspect_ratio_view)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await interaction.edit_original_response(content="Video generation canceled.", view=None)
        self.interaction_completed = True
        self.stop()

    async def start(self):
        if not self.aspect_ratio_view:
            self.aspect_ratio_view = AspectRatioView(self, is_video=True)
        await self.interaction.followup.send(content="First, select the video length, then choose the aspect ratio:", view=self)

    async def generate_video(self, interaction, aspect_ratio):
        try:
            seconds = self.video_length / 22.5  # Convert frames to seconds (observed frame rate)
            await interaction.edit_original_response(content=f"Generating {seconds:.1f} second video (Aspect Ratio: {aspect_ratio})... This may take a few minutes.", view=None)
            
            # Use the Lightricks model with exact documentation defaults
            output = replicate.run(
                "lightricks/ltx-video:8c47da666861d081eeb4d1261853087de23923a268a69b63febdf5dc1dee08e4",
                input={
                    "prompt": self.prompt,
                    "aspect_ratio": aspect_ratio,
                    "model": "0.9.1",
                    "steps": 30,  # Documentation default
                    "length": self.video_length,
                    "target_size": 640,
                    "cfg": 3,
                    "negative_prompt": "low quality, worst quality, deformed, distorted"  # Documentation default
                }
            )

            # Create videos directory if it doesn't exist
            videos_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'images')
            os.makedirs(videos_dir, exist_ok=True)

            # Download and save each video from the returned URLs
            video_files = []
            for index, video_url in enumerate(output):
                try:
                    # Download video from URL
                    response = requests.get(video_url)
                    response.raise_for_status()  # Raise exception for bad status codes
                    
                    # Save the video data
                    video_path = os.path.join(videos_dir, f"output_{index}.mp4")
                    with open(video_path, "wb") as file:
                        file.write(response.content)
                    video_files.append(discord.File(video_path, filename=f"generated_video_{index}.mp4"))
                    logger.info(f"Successfully downloaded video {index + 1} from {video_url}")
                except Exception as e:
                    logger.error(f"Error downloading video {index + 1} from {video_url}: {str(e)}")
                    # Continue with other videos if one fails
                    continue

            if video_files:
                # Send all successfully downloaded videos
                await interaction.channel.send(
                    content=f"Here are your generated videos:\nPrompt: {self.prompt}",
                    files=video_files
                )
            else:
                raise Exception("Failed to download any videos")

            # Clean up temporary files
            for file in video_files:
                try:
                    os.remove(file.fp.name)
                except:
                    pass

            self.interaction_completed = True
            self.stop()

        except Exception as e:
            logger.error(f"Error in generate_video: {str(e)}")
            error_message = display_error(e)
            await interaction.edit_original_response(content=error_message, view=None)
            self.interaction_completed = True
            self.stop()

    async def on_timeout(self):
        if not self.interaction_completed:
            try:
                await self.interaction.edit_original_response(content="Video generation canceled due to timeout", view=None)
            except discord.errors.NotFound:
                pass
        self.stop()

async def handle_video(interaction: discord.Interaction, prompt: str):
    """Handle the video generation command"""
    try:
        view = VideoButtons(prompt, interaction)
        await view.start()
    except Exception as e:
        logger.error(f"Error in video command: {str(e)}")
        error_message = display_error(e)
        await interaction.followup.send(content=error_message)
