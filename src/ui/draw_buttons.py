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
        super().__init__(timeout=300.0)  # 5 minutes - matches model selector timeout
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
        if not self.aspect_ratio_view:
            self.aspect_ratio_view = AspectRatioView(self, model="dalle")
        await interaction.response.edit_message(content="Select the aspect ratio for DALL-E 3:", view=self.aspect_ratio_view)

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
        from src.ui.replicate_model_selector import ReplicateModelSelectorView
        model_selector = ReplicateModelSelectorView(self)
        
        embed = discord.Embed(
            title="🚀 Replicate Model Selection",
            description="Search for any model or use the fast FLUX.1 Dev:",
            color=0x00ff00
        )
        
        await interaction.response.edit_message(content=None, embed=embed, view=model_selector)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Image generation canceled.", view=None)
        self.interaction_completed = True
        self.stop()

    async def on_timeout(self):
        if not self.interaction_completed:
            try:
                await self.interaction.edit_original_response(content="Image generation canceled due to timeout", view=None)
            except (discord.errors.NotFound, discord.errors.InteractionResponded):
                logger.warning("Interaction expired or already responded during timeout")
        self.stop()

    async def generate_sd_image(self, interaction, aspect_ratio):
        await self.generate_image(interaction, "Stable Diffusion 3", image_generation.generate_image_sd, aspect_ratio)

    async def generate_replicate_image(self, interaction, aspect_ratio, model_id="black-forest-labs/flux-schnell"):
        # Create a wrapper function that includes the model_id parameter
        async def replicate_with_model(prompt, aspect_ratio_param):
            return await image_generation.generate_image_replicate(prompt, aspect_ratio_param, model_id)
        
        await self.generate_image(interaction, "Replicate", replicate_with_model, aspect_ratio)

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
                try:
                    await interaction.edit_original_response(content=f"> **Error in GPT Image 1: {result}**", view=None)
                except (discord.errors.NotFound, discord.errors.InteractionResponded):
                    logger.warning("Cannot edit interaction response - interaction expired or already responded")
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
            try:
                await interaction.edit_original_response(content=f"> **Error: An unexpected error occurred while generating the image with GPT Image 1.**", view=None)
            except (discord.errors.NotFound, discord.errors.InteractionResponded):
                logger.warning("Cannot edit interaction response - interaction expired or already responded")
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
                try:
                    await interaction.edit_original_response(content=f"> **Error in {model_name}: {result}**", view=None)
                except (discord.errors.NotFound, discord.errors.InteractionResponded):
                    logger.warning("Cannot edit interaction response - interaction expired or already responded")
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
            try:
                await interaction.edit_original_response(content=f"> **Content Moderation Error: {str(e)}**", view=None)
            except (discord.errors.NotFound, discord.errors.InteractionResponded):
                logger.warning("Cannot edit interaction response - interaction expired or already responded")
            self.interaction_completed = True
            self.stop()
        except Exception as e:
            logger.error(f"Unexpected error in generate_{model_name.lower().replace(' ', '_')}_image: {str(e)}")
            try:
                await interaction.edit_original_response(content=f"> **Error: An unexpected error occurred while generating the image with {model_name}.**", view=None)
            except (discord.errors.NotFound, discord.errors.InteractionResponded):
                logger.warning("Cannot edit interaction response - interaction expired or already responded")
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
            max_length=300,
            placeholder="Describe what changes you want to make to the image..."
        )
        self.add_item(self.instruction)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(thinking=True)
            # Call the image edit API and send the new image
            from src.art.image_generation import generate_image_gpt_image_1_edit
            
            logger.info(f"Starting image iteration with instruction: {self.instruction.value}")
            result = await generate_image_gpt_image_1_edit(self.image_path, self.instruction.value, self.aspect_ratio)
            
            if isinstance(result, str):
                logger.error(f"Image iteration failed: {result}")
                await interaction.followup.send(f"❌ Error editing image: {result}", ephemeral=True)
                return
                
            image_data, new_image_path = result
            file = discord.File(io.BytesIO(image_data), filename="image.png")
            embed = discord.Embed(title=f"> **{self.prompt}**")
            embed.description = f"> **Model: GPT Image 1 (Edited)**\n> **Edit: {self.instruction.value}**\n> **Aspect Ratio: {self.aspect_ratio}**"
            embed.set_image(url="attachment://image.png")
            
            # Allow further iteration with a new view
            view = IterateImageView(self.prompt, new_image_path, self.aspect_ratio)
            
            await interaction.followup.send(content="✅ **Image iteration completed!**", file=file, embed=embed, view=view)
            logger.info("Image iteration completed successfully")
            
        except Exception as e:
            logger.error(f"Error in iterate modal submit: {str(e)}")
            try:
                await interaction.followup.send(f"❌ An error occurred while editing the image: {str(e)}", ephemeral=True)
            except Exception as followup_error:
                logger.error(f"Failed to send error message: {str(followup_error)}")

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.error(f"Modal error: {str(error)}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ An error occurred while processing your request.", ephemeral=True)
            else:
                await interaction.followup.send("❌ An error occurred while processing your request.", ephemeral=True)
        except Exception as e:
            logger.error(f"Failed to send error response: {str(e)}")

class IterateImageView(discord.ui.View):
    def __init__(self, prompt, image_path, aspect_ratio):
        super().__init__(timeout=600.0)  # 10 minutes instead of 3 minutes
        self.prompt = prompt
        self.image_path = image_path
        self.aspect_ratio = aspect_ratio

    @discord.ui.button(label="Iterate", style=discord.ButtonStyle.primary)
    async def iterate_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            modal = IterateImageModal(self.prompt, self.image_path, self.aspect_ratio)
            await interaction.response.send_modal(modal)
        except discord.errors.NotFound:
            # Interaction has expired, send a new message instead
            logger.warning("Interaction expired, sending new iterate modal")
            try:
                modal = IterateImageModal(self.prompt, self.image_path, self.aspect_ratio)
                await interaction.followup.send("The original interaction expired, but here's your iterate modal:", view=None, ephemeral=True)
                # We can't send a modal through followup, so we need a different approach
                await interaction.followup.send("The original interaction has expired. Please use the `/draw` command again to generate a new image to iterate on.", ephemeral=True)
            except Exception as e:
                logger.error(f"Failed to handle expired interaction: {str(e)}")
        except Exception as e:
            logger.error(f"Error in iterate button: {str(e)}")
            try:
                await interaction.response.send_message("An error occurred while trying to open the iterate modal. Please try again.", ephemeral=True)
            except:
                await interaction.followup.send("An error occurred while trying to open the iterate modal. Please try again.", ephemeral=True)

    async def on_timeout(self):
        """Handle view timeout by disabling the button"""
        try:
            for item in self.children:
                item.disabled = True
            # We can't edit the original message here as it might be too old
            logger.info("IterateImageView timed out, disabled buttons")
        except Exception as e:
            logger.error(f"Error handling view timeout: {str(e)}")
        self.stop()
