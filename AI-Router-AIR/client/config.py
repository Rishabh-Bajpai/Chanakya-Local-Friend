import os
from dotenv import load_dotenv

load_dotenv()


class ClientSettings:
    PROJECT_NAME: str = "AI Router Client"
    VERSION: str = "0.1.0"
    PORT: int = int(os.getenv("CLIENT_PORT", 5511))

    # URL of the AIR Server
    AIR_SERVER_URL: str = f"http://localhost:{os.getenv('SERVER_PORT', 5512)}"


settings = ClientSettings()
