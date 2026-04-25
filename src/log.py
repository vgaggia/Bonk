import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


class ColorFormatter(logging.Formatter):
    """Simple color formatter for console output"""

    COLORS = {
        'DEBUG': '\033[37m',  # White
        'INFO': '\033[94m',  # Blue
        'WARNING': '\033[93m',  # Yellow
        'ERROR': '\033[91m',  # Red
        'CRITICAL': '\033[41m',  # Red background
        'RESET': '\033[0m',  # Reset
    }

    def format(self, record):
        # Add color to levelname
        color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
        record.levelname = f"{color}{record.levelname:<8}{self.COLORS['RESET']}"

        # Format the message
        message = super().format(record)

        # Color error traces in red
        if record.exc_info:
            message += f"\n{self.COLORS['ERROR']}{self.formatException(record.exc_info)}{self.COLORS['RESET']}"

        return message


def setup_logger(module_name: str) -> logging.Logger:
    """Configure logging with console and file output

    Args:
        module_name: Name of the module requesting the logger
    Returns:
        logging.Logger: Configured logger instance
    """
    # Create logger
    logger = logging.getLogger(module_name.replace('.py', ''))
    logger.setLevel(logging.INFO)

    # Clear any existing handlers
    logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler()
    console_format = '%(asctime)s %(levelname)s %(name)s -> %(message)s'
    console_handler.setFormatter(ColorFormatter(console_format))
    logger.addHandler(console_handler)

    # File handler (if enabled)
    if os.getenv('LOGGING', '').lower() == 'true':
        try:
            # Setup log directory in project root
            log_dir = Path(__file__).parent.parent / 'logs'
            log_dir.mkdir(exist_ok=True)
            log_file = log_dir / 'discord_bot.log'

            # Create rotating file handler
            file_handler = RotatingFileHandler(
                filename=log_file,
                maxBytes=10 * 1024 * 1024,  # 10MB
                backupCount=3,
                encoding='utf-8',
            )

            # Plain formatter for file output
            file_format = '%(asctime)s %(levelname)-8s %(name)s -> %(message)s'
            file_handler.setFormatter(logging.Formatter(file_format))
            logger.addHandler(file_handler)

        except Exception as e:
            logger.error(f"Failed to setup file logging: {e}")

    return logger
