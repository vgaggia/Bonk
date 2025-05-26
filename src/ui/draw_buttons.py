import io
import discord
from src import log
from src.art import image_generation
from src.ui.aspect_ratio_view import AspectRatioView
from src.ui.generate_video_view import GenerateVideoView
from src.art.error_handler import ContentModerationError

logger = log.setup_logger(__name__)

class DrawButtons(discord.ui.View):
    def __init__(self, prompt, interaction):
        super().__init__(timeout=60.0)
        self.prompt = prompt
        self.interaction = interaction
        self.aspect_ratio_view = None
        self.image_path = None
        self.aspect_ratio = None  # Store the selected aspect ratio
        self.interaction_completed = False

    async def start(self):
        await self.interaction.followup.send(content="Select the model you want to use:", view=self)

    @discord.ui.button(label="Dall-E 3", style=discord.ButtonStyle.primary)
    async def dalle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.generate_dalle_image(interaction)

    @discord.ui.button(label="GPT Image 1", style=discord.ButtonStyle.primary)
    async def gpt_image_1_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.aspect_ratio_view:
            self.aspect_ratio_view = AspectRatioView(self, model="gpt-image-1")
        await interaction.response.edit_message(content="Select the aspect ratio for GPT Image 1:", view=self.aspect_ratio_view)

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
        await self.generate_image(interaction, "Dall-E 3", image_generation.generate_image_dalle)

    async def generate_sd_image(self, interaction, aspect_ratio):
        await self.generate_image(interaction, "Stable Diffusion 3", image_generation.generate_image_sd, aspect_ratio)

    async def generate_replicate_image(self, interaction, aspect_ratio):
        await self.generate_image(interaction, "Replicate", image_generation.generate_image_replicate, aspect_ratio)

    async def generate_gpt_image_1_image(self, interaction, aspect_ratio):
        try:
            # Store the aspect ratio
            self.aspect_ratio = aspect_ratio
            # Edit the message to show we're generating
            await interaction.edit_original_response(content=f"Generating image with GPT Image 1 (Aspect Ratio: {aspect_ratio})... This may take a minute or two.", view=None)
            # Call the generation function
            result = await image_generation.generate_image_gpt_image_1(self.prompt, aspect_ratio)
            
            if isinstance(result, str):
                # This is an error message
                logger.error(f"Error in GPT Image 1: {result}")
                
                # Display the actual error message to the user
                error_message = result if result != "An unexpected error occurred (Or content moderation was triggered)" else "Error connecting to GPT Image 1 API. Please check server logs for details."
                
                await interaction.edit_original_response(content=f"> **Error in GPT Image 1: {error_message}**", view=None)
                self.interaction_completed = True
                self.stop()
            else:
                # This is a tuple containing image_data and image_path
                image_data, self.image_path = result
                file = discord.File(io.BytesIO(image_data), filename="image.png")
                embed = discord.Embed(title=f"> **{self.prompt}**")
                embed.description = f"> **Model: GPT Image 1**\n> **Aspect Ratio: {aspect_ratio}**"
                embed.set_image(url="attachment://image.png")
                
                # Use IterateImageView for GPT Image 1
                view = IterateImageView(self.prompt, self.image_path, aspect_ratio)
                
                await interaction.followup.send(content=None, file=file, embed=embed, view=view)
                self.interaction_completed = True
                self.stop()
        except Exception as e:
            logger.error(f"Error in generate_gpt_image_1_image: {str(e)}")
            await interaction.edit_original_response(content=f"> **Error: An unexpected error occurred while generating the image with GPT Image 1.**", view=None)
            self.interaction_completed = True
            self.stop()

    async def generate_image(self, interaction, model_name, generate_function, aspect_ratio=None):
        try:
            await interaction.response.edit_message(content=f"Generating image with {model_name}...", view=None)
            
            if aspect_ratio:
                result = await generate_function(self.prompt, aspect_ratio)
            else:
                result = await generate_function(self.prompt)
            
            logger.debug(f"Result from {model_name}: {result}")
            
            if isinstance(result, str):
                # This is an error message
                logger.error(f"Error in {model_name}: {result}")
                await interaction.edit_original_response(content=f"> **Error in {model_name}: {result}**", view=None)
                self.interaction_completed = True
                self.stop()
            else:
                # This is a tuple containing image_data and image_path
                image_data, self.image_path = result
                file = discord.File(io.BytesIO(image_data), filename="image.png")
                embed = discord.Embed(title=f"> **{self.prompt}**")
                embed.description = f"> **Model: {model_name}**"
                if aspect_ratio:
                    embed.description += f"\n> **Aspect Ratio: {aspect_ratio}**"
                embed.set_image(url="attachment://image.png")

                # Use IterateImageView for GPT Image 1, otherwise GenerateVideoView
                if model_name == "GPT Image 1":
                    view = IterateImageView(self.prompt, self.image_path, aspect_ratio)
                else:
                    view = GenerateVideoView(self.image_path)

                await interaction.edit_original_response(content=None, attachments=[file], embed=embed, view=view)
                self.interaction_completed = True
                self.stop()
        except ContentModerationError as e:
            logger.error(f"Content moderation error in {model_name}: {str(e)}")
            await interaction.edit_original_response(content=f"> **Content Moderation Error: {str(e)}**", view=None)
            self.interaction_completed = True
            self.stop()
        except Exception as e:
            logger.error(f"Unexpected error in generate_{model_name.lower().replace(' ', '_')}_image: {str(e)}")
            await interaction.edit_original_response(content=f"> **Error: An unexpected error occurred while generating the image with {model_name}.**", view=None)
            self.interaction_completed = True
            self.stop()

# --- IterateImageView and Modal for GPT Image 1 ---
class IterateImageModal(discord.ui.Modal, title="Iterate on Image"):
    def __init__(self, prompt, image_path, aspect_ratio):
        super().__init__()
        self.prompt = prompt
        self.image_path = image_path
        self.aspect_ratio = aspect_ratio
        self.instruction = discord.ui.TextInput(
            label="Edit prompt (e.g. swap cape for hoody)",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=300
        )
        self.add_item(self.instruction)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        # Call the image edit API and send the new image
        from src.art.image_generation import generate_image_gpt_image_1_edit
        result = await generate_image_gpt_image_1_edit(self.image_path, self.instruction.value, self.aspect_ratio)
        if isinstance(result, str):
            await interaction.followup.send(f"Error editing image: {result}", ephemeral=True)
            return
        image_data, new_image_path = result
        file = discord.File(io.BytesIO(image_data), filename="image.png")
        embed = discord.Embed(title=f"> **{self.prompt}**")
        embed.description = f"> **Model: GPT Image 1 (Edited)**\n> **Edit: {self.instruction.value}**\n> **Aspect Ratio: {self.aspect_ratio}**"
        embed.set_image(url="attachment://image.png")
        # Allow further iteration
        view = IterateImageView(self.prompt, new_image_path, self.aspect_ratio)
        await interaction.followup.send(content=None, file=file, embed=embed, view=view)

class IterateImageView(discord.ui.View):
    def __init__(self, prompt, image_path, aspect_ratio):
        super().__init__(timeout=180.0)
        self.prompt = prompt
        self.image_path = image_path
        self.aspect_ratio = aspect_ratio

    @discord.ui.button(label="Iterate", style=discord.ButtonStyle.primary)
    async def iterate_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = IterateImageModal(self.prompt, self.image_path, self.aspect_ratio)
        await interaction.response.send_modal(modal)
