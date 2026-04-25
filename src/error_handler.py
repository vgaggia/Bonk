import logging

import anthropic
import discord
import requests
from openai import OpenAIError

logger = logging.getLogger(__name__)


class ContentModerationError(Exception):
    pass


class APIError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


async def handle_interaction_error(interaction: discord.Interaction, error: Exception) -> None:
    """Handle errors for Discord interactions (async-safe)."""
    error_message = handle_error(error)
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(error_message, ephemeral=True)
        else:
            await interaction.followup.send(error_message, ephemeral=True)
    except Exception as send_err:
        logger.error(f"Failed to send interaction error message: {send_err}")


def handle_error(error, error_type=None):
    """Centralized error handling for all API and general errors"""
    # OpenAI specific errors
    if isinstance(error, OpenAIError):
        if error.status_code == 401:
            if "Invalid Authentication" in str(error):
                logger.error("OpenAI API: Invalid Authentication")
                return "There was an authentication error with the AI service. Please contact the administrator."
            elif "Incorrect API key provided" in str(error):
                logger.error("OpenAI API: Incorrect API key provided")
                return "There was an issue with the AI service credentials. Please contact the administrator."
            elif "You must be a member of an organization to use the API" in str(error):
                logger.error("OpenAI API: User not part of an organization")
                return "Your account is not properly set up to use this service. Please contact the administrator."
        elif error.status_code == 403:
            logger.error("OpenAI API: Access forbidden")
            return (
                "Access to this service is currently restricted. Please contact the administrator."
            )
        elif error.status_code == 429:
            if "Rate limit reached" in str(error):
                logger.warning("OpenAI API: Rate limit reached")
                return "The AI service is currently busy. Please try again in a few moments."
            elif "You exceeded your current quota" in str(error):
                logger.error("OpenAI API: Quota exceeded")
                return "The AI service quota has been exceeded. Please contact the administrator."
        elif error.status_code in (500, 503):
            logger.error(f"OpenAI API: Service error {error.status_code}")
            return "The AI service is currently unavailable. Please try again later."

    # StabilityAI specific errors
    elif isinstance(error, requests.exceptions.HTTPError):
        if error.response is not None:
            status_code = error.response.status_code
            try:
                error_data = error.response.json()
            except ValueError:
                error_data = {}

            if status_code == 400:
                logger.error(
                    f"StabilityAI API: Invalid parameters - {error_data.get('errors', [])}"
                )
                return "Invalid request parameters. Please check your input and try again."
            elif status_code == 403 and error_data.get('name') == 'content_moderation':
                logger.warning("StabilityAI API: Content moderation flag")
                return "Your request was flagged by the content moderation system. Please try different input."
            elif status_code == 413:
                logger.error("StabilityAI API: Request too large")
                return "The request was too large. Please try with a smaller input."
            elif status_code == 422:
                logger.error(f"StabilityAI API: Request rejected - {error_data.get('errors', [])}")
                return "Your request was rejected. Please check your input and try again."
            elif status_code == 429:
                logger.warning("StabilityAI API: Rate limit exceeded")
                return "Too many requests. Please wait a moment and try again."
            elif status_code >= 500:
                logger.error(f"StabilityAI API: Service error {status_code}")
                return "The AI service is currently unavailable. Please try again later."

    # Anthropic specific errors
    elif isinstance(error, anthropic.APIError):
        if error.status_code == 401:
            logger.error("Anthropic API: Authentication error")
            return "There was an authentication error with the AI service. Please contact the administrator."
        elif error.status_code == 429:
            logger.warning("Anthropic API: Rate limit exceeded")
            return "The AI service is currently busy. Please try again in a few moments."
        elif error.status_code >= 500:
            logger.error(f"Anthropic API: Service error {error.status_code}")
            return "The AI service is currently unavailable. Please try again later."

    # Discord specific errors
    elif isinstance(error, discord.errors.NotFound):
        logger.warning(f"Discord interaction not found: {str(error)}")
        return "This interaction has expired. Please try your command again."
    elif isinstance(error, discord.errors.Forbidden):
        logger.error(f"Discord permissions error: {str(error)}")
        return "I don't have permission to perform this action. Please check my role permissions."

    # Custom errors
    elif isinstance(error, ContentModerationError):
        logger.warning(f"Content moderation error: {str(error)}")
        return "Your request was flagged by the content moderation system and cannot be processed. Please try a different input."
    elif isinstance(error, APIError):
        logger.error(f"API error ({error.status_code}): {str(error)}")
        return f"An API error occurred: {str(error)}"

    # Type-specific errors
    elif error_type:
        logger.error(f"Error in {error_type}: {str(error)}")
        return f"An error occurred while processing your {error_type.replace('_', ' ')}. Please try again later."

    # Fallback for unexpected errors
    logger.error(f"Unexpected error: {str(error)}", exc_info=True)
    return "An unexpected error occurred. Please try again later."
