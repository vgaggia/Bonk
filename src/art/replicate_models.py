import asyncio
import logging
import os
from typing import Dict, List, Optional

import replicate
import requests

logger = logging.getLogger(__name__)

# Set up Replicate client
replicate_api_token = os.getenv("REPLICATE_API_TOKEN")
if replicate_api_token:
    replicate_client = replicate.Client(api_token=replicate_api_token)
else:
    replicate_client = replicate


class ReplicateModelSearch:
    def __init__(self):
        self.cached_popular_models = None
        self.cache_timestamp = None
        self.cache_duration = 3600  # 1 hour cache

    async def search_models(
        self, query: str, limit: int = 20, _fallback_on_error: bool = True
    ) -> List[Dict]:
        """Search for models using the Replicate API"""
        try:
            logger.debug(f"Searching for models with query: {query}")

            # Use the HTTP API for searching since it's more reliable
            headers = {
                "Authorization": f"Bearer {replicate_api_token}",
                "Content-Type": "text/plain",
            }

            # Use asyncio to make the HTTP request non-blocking
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.request(
                    "QUERY", "https://api.replicate.com/v1/models", headers=headers, data=query
                ),
            )

            if response.status_code != 200:
                logger.error(f"Search failed: {response.status_code} - {response.text}")
                if _fallback_on_error:
                    return await self._get_fallback_models()
                return []

            result = response.json()
            models = []

            for model in result.get("results", [])[:limit]:
                # Filter for image generation models
                if self._is_image_model(model):
                    models.append(
                        {
                            "id": f"{model.get('owner')}/{model.get('name')}",
                            "name": model.get('name', 'Unknown'),
                            "description": model.get('description', 'No description'),
                            "owner": model.get('owner', 'Unknown'),
                            "run_count": model.get('run_count', 0),
                        }
                    )

            # If no results found, return fallback
            if not models:
                logger.info(f"No models found for query '{query}'")
                if _fallback_on_error:
                    return await self._get_fallback_models()
                return []

            return models

        except Exception as e:
            logger.error(f"Error searching models: {str(e)}")
            if _fallback_on_error:
                return await self._get_fallback_models()
            return []

    def _is_image_model(self, model: Dict) -> bool:
        """Check if a model is likely an image generation model"""
        description = model.get('description', '').lower()
        name = model.get('name', '').lower()

        image_keywords = [
            'image',
            'generate',
            'text-to-image',
            'diffusion',
            'flux',
            'sdxl',
            'stable',
            'dall',
            'midjourney',
            'art',
            'photo',
            'picture',
            'visual',
        ]

        # Exclude non-image models
        exclude_keywords = [
            'text-to-speech',
            'tts',
            'audio',
            'speech',
            'voice',
            'sound',
            'translation',
            'language',
            'chat',
            'completion',
            'llm',
        ]

        text = f"{description} {name}"

        # Check for exclusions first
        if any(keyword in text for keyword in exclude_keywords):
            return False

        # Check for image-related keywords
        return any(keyword in text for keyword in image_keywords)

    async def get_popular_models(self) -> List[Dict]:
        """Fetch popular image generation models from Replicate API"""
        try:
            import time

            # Check cache first
            if (
                self.cached_popular_models
                and self.cache_timestamp
                and time.time() - self.cache_timestamp < self.cache_duration
            ):
                logger.debug("Returning cached popular models")
                return self.cached_popular_models

            logger.debug("Fetching popular models from Replicate")

            # Search for popular image generation models
            popular_queries = [
                "flux text-to-image",
                "stable diffusion image",
                "sdxl text-to-image",
                "dall-e image generation",
            ]

            all_models = []
            for query in popular_queries:
                models = await self.search_models(query, limit=10, _fallback_on_error=False)
                all_models.extend(models)

            # Remove duplicates and sort by run count
            seen_ids = set()
            unique_models = []
            for model in all_models:
                if model['id'] not in seen_ids:
                    seen_ids.add(model['id'])
                    unique_models.append(model)

            # Sort by run count (descending) and take top 8
            unique_models.sort(key=lambda x: x.get('run_count', 0), reverse=True)
            popular_models = unique_models[:8]

            # If we got results, cache them
            if popular_models:
                self.cached_popular_models = popular_models
                self.cache_timestamp = time.time()
                logger.debug(f"Cached {len(popular_models)} popular models")
                return popular_models

            # Fallback to hardcoded list if API fails
            logger.warning("Failed to fetch popular models, using fallback")
            return await self._get_fallback_models()

        except Exception as e:
            logger.error(f"Error fetching popular models: {str(e)}")
            return await self._get_fallback_models()

    async def _get_fallback_models(self) -> List[Dict]:
        """Fallback list of known working models"""
        return [
            {
                "id": "black-forest-labs/flux-schnell",
                "name": "FLUX Schnell",
                "description": "Fast text-to-image generation",
                "owner": "black-forest-labs",
                "run_count": 1000000,
            },
            {
                "id": "black-forest-labs/flux-1.1-pro",
                "name": "FLUX 1.1 Pro",
                "description": "Faster, better FLUX Pro. Text-to-image model with excellent image quality",
                "owner": "black-forest-labs",
                "run_count": 500000,
            },
            {
                "id": "black-forest-labs/flux-dev",
                "name": "FLUX Dev",
                "description": "High-quality text-to-image generation",
                "owner": "black-forest-labs",
                "run_count": 800000,
            },
        ]

    async def get_model_info(self, model_id: str) -> Optional[Dict]:
        """Get detailed information about a specific model"""
        try:
            logger.debug(f"Getting model info for: {model_id}")

            # Use the Python client to get model details
            loop = asyncio.get_event_loop()
            model = await loop.run_in_executor(None, lambda: replicate_client.models.get(model_id))

            if model:
                return {
                    "id": f"{model.owner}/{model.name}",
                    "name": model.name,
                    "description": model.description or "No description",
                    "owner": model.owner,
                    "run_count": getattr(model, 'run_count', 0),
                    "github_url": getattr(model, 'github_url', None),
                    "paper_url": getattr(model, 'paper_url', None),
                    "license_url": getattr(model, 'license_url', None),
                }
        except Exception as e:
            logger.error(f"Error getting model info: {str(e)}")

        return None


# Global instance
model_search = ReplicateModelSearch()


async def search_replicate_models(query: str = "image generation") -> List[Dict]:
    """Search for Replicate models"""
    return await model_search.search_models(query)


async def get_popular_replicate_models() -> List[Dict]:
    """Get popular image generation models"""
    return await model_search.get_popular_models()
