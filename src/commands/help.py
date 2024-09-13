from src import log

logger = log.setup_logger(__name__)

async def handle_help(interaction):
    await interaction.response.defer()
    await interaction.followup.send(""":star:**BASIC COMMANDS** \n
    - `/chat [message]` Chat with Claude!
    - `/draw [prompt]` Generate an image with the Dalle3, Stable Diffusion, or Replicate model
    - `/imagine [user]` Animate a user's profile picture or an attached image
    - `/3d [user]` Generate a 3D model from a user's profile picture or an attached image
    - `/reset` Clear Claude conversation history
    """)
    logger.info("\x1b[31mSomeone needs help!\x1b[0m")