import traceback

def handle_error(error):
    error_type = type(error).__name__
    error_message = str(error)
    
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
    else:
        return f"An unexpected error occurred: {error_message}\n\nStack trace:\n{traceback.format_exc()}"

def display_error(error):
    error_message = handle_error(error)
    print(f"Error: {error_message}")
    return error_message