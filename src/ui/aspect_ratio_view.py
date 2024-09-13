import discord
from src import log
from src.art import image_generation
from src.ui.generate_video_view import GenerateVideoView

logger = log.setup_logger(__name__)

class AspectRatioView(discord.ui.View):
    def __init__(self, parent_view, model="sd"):
        super().__init__(timeout=60.0)
        self.parent_view = parent_view
        self.model = model

    @discord.ui.button(label="16:9", style=discord.ButtonStyle.secondary)
    async def ratio_16_9(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_image(interaction, "16:9")

    @discord.ui.button(label="1:1", style=discord.ButtonStyle.secondary)
    async def ratio_1_1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_image(interaction, "1:1")

    @discord.ui.button(label="21:9", style=discord.ButtonStyle.secondary)
    async def ratio_21_9(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_image(interaction, "21:9")

    @discord.ui.button(label="2:3", style=discord.ButtonStyle.secondary)
    async def ratio_2_3(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_image(interaction, "2:3")

    @discord.ui.button(label="3:2", style=discord.ButtonStyle.secondary)
    async def ratio_3_2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_image(interaction, "3:2")

    @discord.ui.button(label="4:5", style=discord.ButtonStyle.secondary)
    async def ratio_4_5(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_image(interaction, "4:5")

    @discord.ui.button(label="5:4", style=discord.ButtonStyle.secondary)
    async def ratio_5_4(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_image(interaction, "5:4")

    @discord.ui.button(label="9:16", style=discord.ButtonStyle.secondary)
    async def ratio_9_16(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_image(interaction, "9:16")

    @discord.ui.button(label="9:21", style=discord.ButtonStyle.secondary)
    async def ratio_9_21(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_image(interaction, "9:21")

    async def generate_image(self, interaction, aspect_ratio):
        try:
            model_name = "Stable Diffusion 3" if self.model == "sd" else "Replicate"
            await interaction.response.defer(thinking=True)
            await interaction.followup.send(f"Generating image with {model_name} (Aspect Ratio: {aspect_ratio})... This may take a minute or two.")
            
            if self.model == "sd":
                self.parent_view.image_path = await image_generation.generate_image_sd(self.parent_view.prompt, aspect_ratio)
            else:
                self.parent_view.image_path = await image_generation.generate_image_replicate(self.parent_view.prompt, aspect_ratio)

            file = discord.File(self.parent_view.image_path, filename="image.png")
            embed = discord.Embed(title=f"> **{self.parent_view.prompt}**")
            embed.description = f"> **Model: {model_name}**\n> **Aspect Ratio: {aspect_ratio}**"
            embed.set_image(url="attachment://image.png")
            
            view = GenerateVideoView(self.parent_view.image_path)
            
            await interaction.edit_original_response(content=None, attachments=[file], embed=embed, view=view)
            self.parent_view.interaction_completed = True
            self.parent_view.stop()
        except Exception as e:
            logger.exception(f"Error in generate_image: {str(e)}")
            await interaction.edit_original_response(content=f"> **Error: An error occurred while generating the image.**", view=None)
            self.parent_view.interaction_completed = True
            self.parent_view.stop()

    async def on_timeout(self):
        if not self.parent_view.interaction_completed:
            try:
                await self.parent_view.interaction.edit_original_response(content="Image generation canceled due to timeout", view=None)
            except discord.errors.NotFound:
                pass
        self.stop()