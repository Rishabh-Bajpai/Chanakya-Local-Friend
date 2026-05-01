"""Debug script for diagnosing proxy hang issues with audio speech endpoints."""

import httpx
import asyncio

async def test():
    """Test proxy connection with both streaming and non-streaming requests."""
    client = httpx.AsyncClient(timeout=10.0)
    url = "http://127.0.0.1:8969/v1/audio/speech"
    headers = {"Content-Type": "application/json"}
    body = {"model": "tts-1", "input": "Hello", "voice": "alloy", "stream": False}

    print("Sending request to", url)
    try:
        # Try both ways
        print("--- Testing send(stream=False) ---")
        resp = await client.post(url, json=body, headers=headers)
        print("Status:", resp.status_code)
        print("Content-Type:", resp.headers.get("content-type"))
        print("Content length:", len(resp.content))

    except Exception as e:
        print("Error with stream=False:", type(e), e)

    try:
        print("\n--- Testing send(stream=True) ---")
        async with client.stream("POST", url, json=body, headers=headers) as r:
            print("Status:", r.status_code)
            print("Headers:", r.headers)
            count = 0
            async for chunk in r.aiter_bytes():
                count += len(chunk)
            print("Total bytes received via stream:", count)
    except Exception as e:
        print("Error with stream=True:", type(e), e)

asyncio.run(test())
