import io
import discord
from src import log
from src.art import image_generation
from src.ui.generate_video_view import GenerateVideoView

logger = log.setup_logger(__name__)

class AspectRatioView(discord.ui.View):
    def __init__(self, parent_view, model="sd", is_video=False, model_info=None):
        super().__init__(timeout=300.0)  # 5 minutes - longer timeout for model selection flow
        self.parent_view = parent_view
        self.model = model
        self.is_video = is_video
        self.model_info = model_info  # For Replicate model selection

    @discord.ui.button(label="16:9", style=discord.ButtonStyle.secondary)
    async def ratio_16_9(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.handle_selection(interaction, "16:9")

    @discord.ui.button(label="1:1", style=discord.ButtonStyle.secondary)
    async def ratio_1_1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.handle_selection(interaction, "1:1")

    @discord.ui.button(label="21:9", style=discord.ButtonStyle.secondary)
    async def ratio_21_9(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.handle_selection(interaction, "21:9")

    @discord.ui.button(label="2:3", style=discord.ButtonStyle.secondary)
    async def ratio_2_3(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.handle_selection(interaction, "2:3")

    @discord.ui.button(label="3:2", style=discord.ButtonStyle.secondary)
    async def ratio_3_2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.handle_selection(interaction, "3:2")

    @discord.ui.button(label="4:5", style=discord.ButtonStyle.secondary)
    async def ratio_4_5(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.handle_selection(interaction, "4:5")

    @discord.ui.button(label="5:4", style=discord.ButtonStyle.secondary)
    async def ratio_5_4(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.handle_selection(interaction, "5:4")

    @discord.ui.button(label="9:16", style=discord.ButtonStyle.secondary)
    async def ratio_9_16(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.handle_selection(interaction, "9:16")

    @discord.ui.button(label="9:21", style=discord.ButtonStyle.secondary)
    async def ratio_9_21(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.handle_selection(interaction, "9:21")

    def truncate_prompt(self, prompt, max_length=250):
        if len(prompt) <= max_length:
            return prompt
        return prompt[:max_length] + "..."

    async def handle_selection(self, interaction, aspect_ratio):
        """Handle aspect ratio selection for both image and video generation"""
        try:
            if self.is_video:
                await self.parent_view.generate_video(interaction, aspect_ratio)
            else:
                await self.generate_image(interaction, aspect_ratio)
        except Exception as e:
            logger.error(f"Error in handle_selection: {str(e)}")
            try:
                await interaction.edit_original_response(content=f"> **Error: {str(e)}**", view=None)
            except (discord.errors.NotFound, discord.errors.InteractionResponded):
                logger.warning("Cannot edit interaction response - interaction expired or already responded")
            self.parent_view.interaction_completed = True
            self.parent_view.stop()

    async def generate_image(self, interaction, aspect_ratio):
        try:
            if self.model == "sd":
                model_name = "Stable Diffusion 3"
                await interaction.edit_original_response(content=f"Generating image with {model_name} (Aspect Ratio: {aspect_ratio})... This may take a minute or two.", view=None)
                result = await image_generation.generate_image_sd(self.parent_view.prompt, aspect_ratio)
            elif self.model == "dalle":
                model_name = "DALL-E 3"
                await interaction.edit_original_response(content=f"Generating image with {model_name} (Aspect Ratio: {aspect_ratio})... This may take a minute or two.", view=None)
                result = await image_generation.generate_image_dalle(self.parent_view.prompt, aspect_ratio)
            elif self.model == "replicate":
                if self.model_info:
                    model_name = f"Replicate ({self.model_info['name']})"
                    model_id = self.model_info['id']
                else:
                    model_name = "Replicate"
                    model_id = "black-forest-labs/flux-schnell"  # Default fallback
                
                await interaction.edit_original_response(content=f"Generating image with {model_name} (Aspect Ratio: {aspect_ratio})... This may take a minute or two.", view=None)
                result = await image_generation.generate_image_replicate(self.parent_view.prompt, aspect_ratio, model_id)
            elif self.model == "gpt-image-1":
                model_name = "GPT Image 1"
                # Don't edit the response here, let the parent view handle it
                self.parent_view.aspect_ratio = aspect_ratio  # Store the aspect ratio
                await self.parent_view.generate_gpt_image_1_image(interaction, aspect_ratio)
                return
            else:
                raise Exception(f"Unknown model: {self.model}")

            if isinstance(result, str):
                # This is an error message
                logger.error(f"Error in {model_name}: {result}")
                try:
                    await interaction.edit_original_response(content=f"> **Error in {model_name}: {result}**", view=None)
                except (discord.errors.NotFound, discord.errors.InteractionResponded):
                    logger.warning("Cannot edit interaction response - interaction expired or already responded")
                self.parent_view.interaction_completed = True
                self.parent_view.stop()
            else:
                # This is a tuple containing image_data and image_path
                image_data, self.parent_view.image_path = result
                file = discord.File(io.BytesIO(image_data), filename="image.png")
                embed = discord.Embed(title=f"> **{self.parent_view.prompt}**")
                embed.description = f"> **Model: {model_name}**\n> **Aspect Ratio: {aspect_ratio}**"
                embed.set_image(url="attachment://image.png")
                
                view = GenerateVideoView(self.parent_view.image_path)
                
                await interaction.edit_original_response(content=None, attachments=[file], embed=embed, view=view)
                self.parent_view.interaction_completed = True
                self.parent_view.stop()
        except Exception as e:
            logger.exception(f"Error in generate_image: {str(e)}")
            try:
                await interaction.edit_original_response(content=f"> **Error: An error occurred while generating the image.**", view=None)
            except (discord.errors.NotFound, discord.errors.InteractionResponded):
                logger.warning("Cannot edit interaction response - interaction expired or already responded")
            self.parent_view.interaction_completed = True
            self.parent_view.stop()

    async def on_timeout(self):
        if not getattr(self.parent_view, 'interaction_completed', False):
            try:
                # Try to get the latest interaction from the parent view
                interaction = getattr(self.parent_view, 'interaction', None)
                if interaction:
                    await interaction.edit_original_response(
                        content="⏰ **Aspect ratio selection timed out.** Please use `/draw` again to generate a new image.", 
                        view=None,
                        embed=None
                    )
            except (discord.errors.NotFound, discord.errors.InteractionResponded):
                logger.warning("Cannot update interaction - it may have expired or been replaced")
            except Exception as e:
                logger.warning(f"Error handling timeout: {e}")
        self.stop()
