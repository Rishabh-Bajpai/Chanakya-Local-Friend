from fastapi import APIRouter, HTTPException
from typing import List
import httpx
from server.core.config import settings
from server.schemas.provider_schema import ProviderInput, ProviderConfig, ProviderStatus, AcceptProviderInput
from server.services.provider_manager import provider_manager
import logging
import os

router = APIRouter(tags=["Configuration/Admin"])
logger = logging.getLogger(__name__)

@router.get("/providers", response_model=List[ProviderConfig])
async def get_providers():
    return settings.PROVIDERS

@router.post("/providers")
async def add_provider(provider: ProviderInput):
    current_count = 0
    prefix = ""
    if provider.type == "llm":
        prefix = "LLM"
    elif provider.type == "stt":
        prefix = "STT"
    elif provider.type == "tts":
        prefix = "TTS"
    else:
        raise HTTPException(status_code=400, detail="Invalid provider type")

    i = 1
    while True:
        if os.getenv(f"{prefix}_BASE_URL_{i}") is None:
            break
        i += 1
    
    new_index = i
    
    settings.update_env_variable(f"{prefix}_BASE_URL_{new_index}", provider.base_url)
    if provider.api_key:
        settings.update_env_variable(f"{prefix}_API_KEY_{new_index}", provider.api_key)
    
    settings.reload()
    return {"message": "Provider added", "index": new_index}

@router.put("/providers/{index}")
async def update_provider(index: int, provider: ProviderInput):
    prefix = ""
    if provider.type == "llm":
        prefix = "LLM"
    elif provider.type == "stt":
        prefix = "STT"
    elif provider.type == "tts":
        prefix = "TTS"
    else:
        raise HTTPException(status_code=400, detail="Invalid provider type")

    settings.update_env_variable(f"{prefix}_BASE_URL_{index}", provider.base_url)
    
    if provider.api_key and provider.api_key != "na":
        settings.update_env_variable(f"{prefix}_API_KEY_{index}", provider.api_key)
    else:
        settings.update_env_variable(f"{prefix}_API_KEY_{index}", "na")

    settings.reload()
    return {"message": "Provider updated", "index": index}

@router.delete("/providers/{index}")
async def delete_provider(type: str, index: int):
    prefix = ""
    if type == "llm":
        prefix = "LLM"
    elif type == "stt":
        prefix = "STT"
    elif type == "tts":
        prefix = "TTS"
    else:
        raise HTTPException(status_code=400, detail="Invalid provider type")

    settings.remove_env_variable(f"{prefix}_BASE_URL_{index}")
    settings.remove_env_variable(f"{prefix}_API_KEY_{index}")
    
    settings.reload()
    return {"message": "Provider removed"}

@router.post("/providers/check")
async def check_provider_status(provider: ProviderInput):
    url = f"{provider.base_url.rstrip('/')}/models"
    headers = {"Content-Type": "application/json"}
    if provider.api_key and provider.api_key != "na":
        headers["Authorization"] = f"Bearer {provider.api_key}"
        
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                return {"status": "online", "details": f"Found {len(resp.json().get('data', []))} models"}
            else:
                return {"status": "error", "details": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"status": "offline", "details": str(e)}

@router.get("/status")
async def get_all_status():
    results = []
    for p in settings.PROVIDERS:
        status = "unknown"
        details = ""
        url = f"{p.base_url.rstrip('/')}/models"
        headers = {"Content-Type": "application/json"}
        if p.api_key and p.api_key != "na":
            headers["Authorization"] = f"Bearer {p.api_key}"
            
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    status = "online"
                    details = "Connected"
                else:
                    status = "error"
                    details = f"HTTP {resp.status_code}"
        except Exception:
            status = "offline"
            details = "Unreachable"
            
        results.append(ProviderStatus(
            name=p.name,
            type=p.type,
            base_url=p.base_url,
            status=status,
            details=details
        ))
    return results

@router.get("/discovered")
async def get_discovered_providers():
    if not settings.DISCOVERY_ENABLED:
        return []

    from server.services.discovery import discovery_service

    discovered = await discovery_service.scan()
    new_providers = discovery_service.filter_new(discovered, settings.PROVIDERS)

    return [dp.to_dict() for dp in new_providers]


@router.post("/discovered/accept")
async def accept_discovered_providers(providers: List[AcceptProviderInput]):
    added = []
    for prov in providers:
        for p_type in prov.detected_types:
            prefix = p_type.upper() 
            if prefix not in ("LLM", "STT", "TTS"):
                continue

            target_index = None
            i = 1
            while True:
                existing_url = os.getenv(f"{prefix}_BASE_URL_{i}")
                if existing_url is None:
                    break
                if existing_url.rstrip("/") == prov.base_url.rstrip("/"):
                    target_index = i
                    break
                i += 1

            if target_index is None:
                target_index = i

            settings.update_env_variable(f"{prefix}_BASE_URL_{target_index}", prov.base_url)
            if prov.api_key and prov.api_key != "na":
                settings.update_env_variable(f"{prefix}_API_KEY_{target_index}", prov.api_key)

            added.append({"type": p_type, "base_url": prov.base_url, "index": target_index})

    settings.reload()
    return {"message": f"Processed {len(added)} provider mapping(s)", "added": added}
