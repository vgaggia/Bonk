import logging
import os

from src.error_handler import APIError

logger = logging.getLogger(__name__)

async def health_check():
    """Perform basic health checks on API connections and environment"""
    issues = []
    
    # Check required environment variables
    required_vars = [
        "DISCORD_BOT_TOKEN",
        "ANTHROPIC_API_KEY"
    ]
    
    optional_vars = {
        "OPENAI_API_KEY": "GPT-4o functionality",
        "STABILITY_API_KEY": "Image generation", 
        "REPLICATE_API_TOKEN": "Advanced image features",
        "LUMALABS_API_KEY": "Video generation"
    }
    
    for var in required_vars:
        if not os.getenv(var):
            issues.append(f"❌ Missing required environment variable: {var}")
    
    for var, feature in optional_vars.items():
        if not os.getenv(var):
            logger.warning(f"⚠️ Missing optional environment variable: {var} (disables {feature})")
    
    # Test Anthropic API connection
    try:
        from src.responses import CLAUDE_MODEL, anthropic_client
        # Simple test request
        anthropic_client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=10,
            messages=[{"role": "user", "content": "test"}]
        )
        logger.info("✅ Anthropic API connection successful")
    except Exception as e:
        issues.append(f"❌ Anthropic API connection failed: {str(e)}")
    
    if issues:
        error_msg = "Health check failed:\n" + "\n".join(issues)
        logger.error(error_msg)
        raise APIError(error_msg)
    else:
        logger.info("✅ All health checks passed")
        return True

if __name__ == "__main__":
    import asyncio
    asyncio.run(health_check())
