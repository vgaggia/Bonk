
import discord

from src import log
from src.art.replicate_models import get_popular_replicate_models, search_replicate_models
from src.ui.aspect_ratio_view import AspectRatioView

logger = log.setup_logger(__name__)

class ModelSearchModal(discord.ui.Modal, title="Search Replicate Models"):
    def __init__(self, parent_view):
        super().__init__()
        self.parent_view = parent_view
        self.search_query = discord.ui.TextInput(
            label="Search models",
            style=discord.TextStyle.short,
            required=False,
            max_length=100,
            placeholder="e.g. 'flux', 'stable diffusion' or leave empty"
        )
        self.add_item(self.search_query)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        query = self.search_query.value.strip()
        if not query:
            models = await get_popular_replicate_models()
        else:
            models = await search_replicate_models(query)
        
        # Create model selection view
        model_view = ModelSelectionView(self.parent_view, models, query or "Popular Models")
        
        embed = discord.Embed(
            title=f"🔍 Found {len(models)} models",
            description=f"Search: **{query or 'Popular Models'}**\nSelect a model to use:",
            color=0x00ff00
        )
        
        try:
            await interaction.edit_original_response(embed=embed, view=model_view)
        except (discord.errors.NotFound, discord.errors.InteractionResponded):
            logger.warning("Cannot edit interaction response - interaction expired or already responded")

class ModelSelectionView(discord.ui.View):
    def __init__(self, parent_view, models, search_query):
        super().__init__(timeout=300.0)  # 5 minutes timeout
        self.parent_view = parent_view
        self.models = models
        self.search_query = search_query
        self.current_page = 0
        self.models_per_page = 5
        
        # Get reference to the original DrawButtons view
        if hasattr(parent_view, 'parent_view'):
            self.original_view = parent_view.parent_view  # From ReplicateModelSelectorView
        else:
            self.original_view = parent_view  # Direct reference
        
        self.update_buttons()
    
    def update_buttons(self):
        self.clear_items()
        
        # Calculate pagination
        start_idx = self.current_page * self.models_per_page
        end_idx = min(start_idx + self.models_per_page, len(self.models))
        page_models = self.models[start_idx:end_idx]
        
        # Add model buttons (max 5 per page to avoid Discord limits)
        for i, model in enumerate(page_models[:5]):  # Limit to 5 buttons max
            button = ModelButton(
                model=model,
                parent_view=self,
                row=i  # Each button gets its own row for better layout
            )
            self.add_item(button)
        
        # Add navigation buttons if needed
        if len(self.models) > self.models_per_page:
            if self.current_page > 0:
                prev_button = discord.ui.Button(
                    label="◀ Previous",
                    style=discord.ButtonStyle.secondary,
                    row=4
                )
                prev_button.callback = self.previous_page
                self.add_item(prev_button)
            
            if end_idx < len(self.models):
                next_button = discord.ui.Button(
                    label="Next ▶",
                    style=discord.ButtonStyle.secondary,
                    row=4
                )
                next_button.callback = self.next_page
                self.add_item(next_button)
        
        # Add back/search buttons
        back_button = discord.ui.Button(
            label="🔍 New Search",
            style=discord.ButtonStyle.primary,
            row=4
        )
        back_button.callback = self.new_search
        self.add_item(back_button)
        
        cancel_button = discord.ui.Button(
            label="❌ Cancel",
            style=discord.ButtonStyle.danger,
            row=4
        )
        cancel_button.callback = self.cancel
        self.add_item(cancel_button)
    
    async def previous_page(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.current_page = max(0, self.current_page - 1)
        self.update_buttons()
        
        embed = self.create_embed()
        try:
            await interaction.edit_original_response(embed=embed, view=self)
        except (discord.errors.NotFound, discord.errors.InteractionResponded):
            logger.warning("Cannot edit interaction response - interaction expired or already responded")
    
    async def next_page(self, interaction: discord.Interaction):
        await interaction.response.defer()
        max_pages = (len(self.models) - 1) // self.models_per_page
        self.current_page = min(max_pages, self.current_page + 1)
        self.update_buttons()
        
        embed = self.create_embed()
        try:
            await interaction.edit_original_response(embed=embed, view=self)
        except (discord.errors.NotFound, discord.errors.InteractionResponded):
            logger.warning("Cannot edit interaction response - interaction expired or already responded")
    
    async def new_search(self, interaction: discord.Interaction):
        modal = ModelSearchModal(self.parent_view)
        try:
            await interaction.response.send_modal(modal)
        except (discord.errors.NotFound, discord.errors.InteractionResponded):
            logger.warning("Cannot send modal - interaction expired or already responded")
    
    async def cancel(self, interaction: discord.Interaction):
        try:
            await interaction.response.edit_message(
                content="Model selection cancelled.",
                embed=None,
                view=None
            )
        except (discord.errors.NotFound, discord.errors.InteractionResponded):
            logger.warning("Cannot edit interaction response - interaction expired or already responded")
        self.stop()
    
    def create_embed(self):
        start_idx = self.current_page * self.models_per_page
        end_idx = min(start_idx + self.models_per_page, len(self.models))
        total_pages = (len(self.models) - 1) // self.models_per_page + 1
        
        embed = discord.Embed(
            title=f"🔍 Found {len(self.models)} models",
            description=f"Search: **{self.search_query}**\nPage {self.current_page + 1} of {total_pages}\nSelect a model to use:",
            color=0x00ff00
        )
        
        # Add model info to embed
        page_models = self.models[start_idx:end_idx]
        for i, model in enumerate(page_models):
            embed.add_field(
                name=f"{i+1}. {model['name']}",
                value=f"**Owner:** {model.get('owner', 'Unknown')}\n**ID:** `{model['id']}`\n{model.get('description', 'No description')[:100]}{'...' if len(model.get('description', '')) > 100 else ''}",
                inline=False
            )
        
        return embed

class ModelButton(discord.ui.Button):
    def __init__(self, model, parent_view, row=0):
        self.model = model
        self.parent_view = parent_view
        
        # Truncate name for button label
        label = model['name'][:80] if len(model['name']) > 80 else model['name']
        
        super().__init__(
            label=label,
            style=discord.ButtonStyle.success,
            row=row
        )
    
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        # Get the original draw buttons view (which has the prompt)
        original_view = self.parent_view.original_view
        
        # Store selected model in the original view
        original_view.selected_model = self.model
        
        # Create aspect ratio view for the selected model
        aspect_ratio_view = AspectRatioView(
            original_view, 
            model="replicate",
            model_info=self.model
        )
        
        embed = discord.Embed(
            title=f"✅ Selected Model: {self.model['name']}",
            description=f"**Owner:** {self.model.get('owner', 'Unknown')}\n**ID:** `{self.model['id']}`\n\n{self.model.get('description', 'No description')}\n\nNow select an aspect ratio:",
            color=0x00ff00
        )
        
        try:
            await interaction.edit_original_response(embed=embed, view=aspect_ratio_view)
        except (discord.errors.NotFound, discord.errors.InteractionResponded):
            logger.warning("Cannot edit interaction response - interaction expired or already responded")

class ReplicateModelSelectorView(discord.ui.View):
    def __init__(self, parent_view):
        super().__init__(timeout=300.0)  # 5 minutes timeout
        self.parent_view = parent_view
        self.selected_model = None
    
    @discord.ui.button(label="🔍 Search Models", style=discord.ButtonStyle.primary)
    async def search_models(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = ModelSearchModal(self)
        try:
            await interaction.response.send_modal(modal)
        except (discord.errors.NotFound, discord.errors.InteractionResponded):
            logger.warning("Cannot send modal - interaction expired or already responded")
    
    @discord.ui.button(label="⚡ FLUX.1 Dev (Fast)", style=discord.ButtonStyle.secondary)
    async def flux_dev_model(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        
        # Use the fast FLUX dev model from prunaai
        flux_model = {
            "id": "prunaai/flux.1-dev",
            "name": "FLUX.1 Dev (Fast)",
            "description": "3x faster FLUX.1 [dev] model optimized with Pruna. High-quality text-to-image generation.",
            "owner": "prunaai",
            "run_count": 17500000
        }
        
        # Store selected model in the original view
        original_view = self.parent_view
        original_view.selected_model = flux_model
        
        # Create aspect ratio view for the selected model
        aspect_ratio_view = AspectRatioView(
            original_view, 
            model="replicate",
            model_info=flux_model
        )
        
        embed = discord.Embed(
            title=f"✅ Selected Model: {flux_model['name']}",
            description=f"**Owner:** {flux_model.get('owner', 'Unknown')}\n**ID:** `{flux_model['id']}`\n\n{flux_model.get('description', 'No description')}\n\nNow select an aspect ratio:",
            color=0x00ff00
        )
        
        try:
            await interaction.edit_original_response(embed=embed, view=aspect_ratio_view)
        except (discord.errors.NotFound, discord.errors.InteractionResponded):
            logger.warning("Cannot edit interaction response - interaction expired or already responded")
    
    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.edit_message(
                content="Model selection cancelled.",
                view=None
            )
        except (discord.errors.NotFound, discord.errors.InteractionResponded):
            logger.warning("Cannot edit interaction response - interaction expired or already responded")
        self.stop()