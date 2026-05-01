"""Quick proxy connectivity test for the audio speech endpoint."""

import httpx
import asyncio

async def test():
    """Send a streaming POST request to the proxy and print the response."""
    try:
        async with httpx.AsyncClient() as client:
            req = client.build_request("POST", "http://127.0.0.1:8969/v1/audio/speech", json={"model": "tts-1", "input": "Hello", "voice": "alloy"})
            r = await client.send(req, stream=True)
            print("Status", r.status_code)
            print("Headers:", r.headers)
            await r.aread()
            print(r.content[:100])
    except httpx.ConnectError:
        print("Could not connect to test server at http://127.0.0.1:8969. Is the proxy running?")

if __name__ == '__main__':
    asyncio.run(test())
