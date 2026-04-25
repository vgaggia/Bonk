from src.error_handler import ContentModerationError  # noqa: F401 - re-export for convenience


def handle_error(error):
    error_type = type(error).__name__
    error_message = str(error)

    # Check if the error message contains specific content moderation indicators
    if any(
        phrase in error_message.lower()
        for phrase in ['safety system', 'content policy', 'moderation', 'safety violations']
    ):
        return "Content blocked: Your request was flagged by the safety system. Please try with different content."

    if error_type == "APIConnectionError":
        return "There was an issue connecting to the AI service. Please check your internet connection and try again."
    elif error_type == "APIError":
        return f"An error occurred with the AI service: {error_message}"
    elif error_type == "InvalidRequestError":
        return f"Invalid request: {error_message}"
    elif error_type == "AuthenticationError":
        return "Authentication failed. Please check your API key and try again."
    elif error_type == "RateLimitError":
        return "Rate limit exceeded. Please try again later."
    elif error_type == "InvalidAPIKeyError":
        return "Invalid API key. Please check your API key and try again."
    elif error_type == "ContentModerationError":
        return "Content blocked: Your request was flagged by the content moderation system and cannot be processed"
    else:
        # Check for specific status codes in error messages
        if "403" in error_message:
            return "Access forbidden: The request was blocked by the service provider"
        elif "400" in error_message and "safety" in error_message.lower():
            return "Content blocked: Your request was rejected by the safety system"
        else:
            return f"An unexpected error occurred: {error_message}"


def display_error(error):
    error_message = handle_error(error)
    print(f"Error: {error_message}")
    return error_message
