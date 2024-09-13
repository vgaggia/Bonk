import discord
from src import log
from src.art import image_generation
from src.ui.aspect_ratio_view import AspectRatioView
from src.ui.generate_video_view import GenerateVideoView

logger = log.setup_logger(__name__)

class DrawButtons(discord.ui.View):
    def __init__(self, prompt, interaction):
        super().__init__(timeout=60.0)
        self.prompt = prompt
        self.interaction = interaction
        self.aspect_ratio_view = None
        self.image_path = None
        self.interaction_completed = False

    async def start(self):
        await self.interaction.followup.send(content="Select the model you want to use:", view=self)

    @discord.ui.button(label="Dall-E 3", style=discord.ButtonStyle.primary)
    async def dalle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_dalle_image(interaction)

    @discord.ui.button(label="Stable Diffusion 3", style=discord.ButtonStyle.secondary)
    async def stable_diffusion_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.aspect_ratio_view:
            self.aspect_ratio_view = AspectRatioView(self)
        await interaction.response.edit_message(content="Select the aspect ratio:", view=self.aspect_ratio_view)

    @discord.ui.button(label="Replicate", style=discord.ButtonStyle.success)
    async def replicate_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.aspect_ratio_view:
            self.aspect_ratio_view = AspectRatioView(self, model="replicate")
        await interaction.response.edit_message(content="Select the aspect ratio for Replicate:", view=self.aspect_ratio_view)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Image generation canceled.", view=None)
        self.interaction_completed = True
        self.stop()

    async def on_timeout(self):
        if not self.interaction_completed:
            try:
                await self.interaction.edit_original_response(content="Image generation canceled due to timeout", view=None)
            except discord.errors.NotFound:
                pass
        self.stop()

    async def generate_dalle_image(self, interaction):
        try:
            await interaction.response.edit_message(content="Generating image with Dall-E 3...", view=None)
            
            self.image_path = await image_generation.generate_image_dalle(self.prompt)
            file = discord.File(self.image_path, filename="image.png")
            embed = discord.Embed(title=f"> **{self.prompt}**")
            embed.description = "> **Model: Dall-E 3**"
            embed.set_image(url="attachment://image.png")
            
            view = GenerateVideoView(self.image_path)
            
            await interaction.edit_original_response(content=None, attachments=[file], embed=embed, view=view)
            self.interaction_completed = True
            self.stop()
        except Exception as e:
            logger.exception(f"Error in generate_dalle_image: {str(e)}")
            await interaction.edit_original_response(content=f"> **Error: An error occurred while generating the image.**", view=None)
            self.interaction_completed = True
            self.stop()