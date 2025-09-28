import logging

import requests
from openai import OpenAIError

logger = logging.getLogger(__name__)

class ContentModerationError(Exception):
    pass

def handle_error(error, error_type=None):
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
        elif error.status_code == 403 and "Country, region, or territory not supported" in str(error):
            logger.error("OpenAI API: Unsupported region")
            return "This service is not available in your region."
        elif error.status_code == 429:
            if "Rate limit reached" in str(error):
                logger.warning("OpenAI API: Rate limit reached")
                return "The AI service is currently busy. Please try again in a few moments."
            elif "You exceeded your current quota" in str(error):
                logger.error("OpenAI API: Quota exceeded")
                return "The AI service quota has been exceeded. Please contact the administrator."
        elif error.status_code == 500:
            logger.error("OpenAI API: Internal server error")
            return "The AI service encountered an internal error. Please try again later."
        elif error.status_code == 503:
            logger.warning("OpenAI API: Service overloaded")
            return "The AI service is currently overloaded. Please try again later."

    # StabilityAI specific errors
    elif isinstance(error, requests.exceptions.HTTPError):
        if error.response is not None:
            status_code = error.response.status_code
            try:
                error_data = error.response.json()
            except ValueError:
                error_data = {}

            if status_code == 400:
                logger.error(f"StabilityAI API: Invalid parameters - {error_data.get('errors', [])}")
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
            elif status_code == 500:
                logger.error("StabilityAI API: Internal server error")
                return "The AI service encountered an internal error. Please try again later."

    # Generic error handling
    if isinstance(error, ContentModerationError):
        logger.warning(f"Content moderation error: {str(error)}")
        return "Your request was flagged by the content moderation system and cannot be processed. Please try a different input."
    elif error_type == "image_generation":
        logger.error(f"Error generating image: {str(error)}")
        return "An error occurred while generating the image. Please try again later."
    elif error_type == "video_generation":
        logger.error(f"Error generating video: {str(error)}")
        return "An error occurred while generating the video. Please try again later."
    elif error_type == "3d_model":
        logger.error(f"Error generating 3D model: {str(error)}")
        return "An error occurred while generating the 3D model. Please try again later."
    else:
        logger.error(f"Unexpected error: {str(error)}")
        return "An unexpected error occurred. Please try again later."