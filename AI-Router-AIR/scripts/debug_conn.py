import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv()

async def test_connection(url, name, api_key):
    print(f"Testing connection to {name}: {url}")
    headers = {"Content-Type": "application/json"}
    if api_key and api_key != "na":
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(f"{url.rstrip('/')}/models", headers=headers)
            print(f"[{name}] Status: {response.status_code}")
            if response.status_code == 200:
                print(f"[{name}] Success! Found {len(response.json().get('data', []))} models.")
            else:
                print(f"[{name}] Response: {response.text[:200]}")
    except Exception as e:
        print(f"[{name}] Error: {e}")

async def main():
    # Test Providers from Env
    i = 1
    while True:
        url = os.getenv(f"LLM_BASE_URL_{i}")
        if not url:
            break
        api_key = os.getenv(f"LLM_API_KEY_{i}", "na")
        await test_connection(url, f"LLM_{i}", api_key)
        i += 1

if __name__ == "__main__":
    asyncio.run(main())
