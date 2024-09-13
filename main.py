import importlib
import sys
from src import log
from dotenv import load_dotenv


if __name__ == '__main__':
    from src import bot
    bot.run_discord_bot()