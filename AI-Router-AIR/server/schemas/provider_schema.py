from pydantic import BaseModel
from typing import Optional

class ProviderConfig(BaseModel):
    type: str # "llm", "stt", "tts"
    base_url: str
    api_key: str
    name: str

class ProviderInput(BaseModel):
    type: str
    base_url: str
    api_key: Optional[str] = "na"
    name: Optional[str] = None

class ProviderStatus(BaseModel):
    name: str
    type: str
    base_url: str
    status: str # "online", "offline", "error"
    details: Optional[str] = None

class AcceptProviderInput(BaseModel):
    name: str
    base_url: str
    detected_types: list[str]  # e.g. ["llm", "stt"]
    api_key: Optional[str] = "na"
