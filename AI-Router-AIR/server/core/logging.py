import logging
import sys

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            # Optional: Add file handler if needed
            # logging.FileHandler("server.log")
        ]
    )
    return logging.getLogger("air")

logger = setup_logging()
