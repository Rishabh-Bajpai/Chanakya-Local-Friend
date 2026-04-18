import pytest
import httpx
import json
import os

# You can override the base URL by setting the ROUTER_TEST_BASE_URL environment variable.
BASE_URL = os.environ.get("ROUTER_TEST_BASE_URL", "http://127.0.0.1:5020")

@pytest.mark.asyncio
async def test_llm_streaming():
    """Test LLM chat completions with streaming enabled."""
    url = f"{BASE_URL}/v1/chat/completions"
    payload = {
        "model": "qwen3-vl-30b-a3b-instruct", # Using a discovered model
        "messages": [{"role": "user", "content": "Say 'Streaming is working'"}],
        "stream": True
    }
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            async with client.stream("POST", url, json=payload) as response:
                assert response.status_code == 200
                assert "text/event-stream" in response.headers.get("content-type", "")
                
                chunks = 0
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                        
                        data = json.loads(data_str)
                        if "choices" in data and len(data["choices"]) > 0:
                            delta = data["choices"][0].get("delta", {})
                            if "content" in delta:
                                chunks += 1
                
                assert chunks > 0
        except httpx.ConnectError:
            pytest.skip(f"Could not connect to test server at {BASE_URL}. Is it running?")
        except Exception as e:
            pytest.fail(f"LLM streaming request failed: {e}")

@pytest.mark.asyncio
async def test_stt_streaming():
    """Test STT transcriptions with streaming enabled."""
    url = f"{BASE_URL}/v1/audio/transcriptions"
    audio_path = "/home/rishabh/github_projects/AI-Router-AIR/test.mp3"
    
    if not os.path.exists(audio_path):
        pytest.skip(f"Audio file not found at {audio_path}")
        
    files = {'file': ('test.mp3', open(audio_path, 'rb'), 'audio/mpeg')}
    data = {
        'model': 'Systran/faster-whisper-base.en',
        'stream': 'true'
    }
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            async with client.stream("POST", url, data=data, files=files) as response:
                assert response.status_code == 200
                assert "text/event-stream" in response.headers.get("content-type", "")
                
                segments = 0
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                        
                        data = json.loads(data_str)
                        if "text" in data:
                            segments += 1
                
                assert segments > 0
        except Exception as e:
            pytest.fail(f"STT streaming request failed: {e}")
