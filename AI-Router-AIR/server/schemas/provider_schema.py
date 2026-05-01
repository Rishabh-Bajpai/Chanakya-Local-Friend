"""Pydantic schemas for provider configuration, input, and status models."""

from pydantic import BaseModel
from typing import Optional

class ProviderConfig(BaseModel):
    """Stored configuration for a registered AI provider."""
    type: str # "llm", "stt", "tts"
    base_url: str
    api_key: str
    name: str

class ProviderInput(BaseModel):
    """Input schema for adding or updating a provider via the API."""
    type: str
    base_url: str
    api_key: Optional[str] = "na"
    name: Optional[str] = None

class ProviderStatus(BaseModel):
    """Response schema representing a provider's connectivity status."""
    name: str
    type: str
    base_url: str
    status: str # "online", "offline", "error"
    details: Optional[str] = None

class AcceptProviderInput(BaseModel):
    """Input schema for accepting discovered providers into the configuration."""
    name: str
    base_url: str
    detected_types: list[str]  # e.g. ["llm", "stt"]
    api_key: Optional[str] = "na"
