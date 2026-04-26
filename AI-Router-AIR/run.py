"""Launcher script for starting both the AIR server and client processes."""

import subprocess
import time
import signal
import sys
import os
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Define processes
server_process = None
client_process = None

def cleanup(signum, frame):
    """Handler for signals to ensure processes are killed."""
    print("\nStopping AI Router services...")

    if client_process:
        print("Terminating Client...")
        client_process.terminate()
        try:
            client_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            client_process.kill()

    if server_process:
        print("Terminating Server...")
        server_process.terminate()
        try:
            server_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_process.kill()

    print("All services stopped. Ports should be free.")
    sys.exit(0)

# Register signal handlers
signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)

def main():
    """Start the AIR server and client subprocesses."""
    global server_process, client_process

    # Get python executable (use the same env)
    python_exe = sys.executable

    print(f"Starting AI Router (AIR) using {python_exe}...")

    # Start Server
    print("Launching Server (Backend)...")
    env = os.environ.copy()
    # Ensure stdout/stderr are unbuffered so we see output
    env["PYTHONUNBUFFERED"] = "1"

    server_process = subprocess.Popen(
        [python_exe, "-m", "server.main"],
        env=env
    )

    # Wait a bit for server to start
    time.sleep(2)

    # Start Client
    print("Launching Client (Frontend)...")
    client_process = subprocess.Popen(
        [python_exe, "-m", "client.main"],
        env=env
    )

    print("\n✅ AI Router is running!")
    print(f"   - Server Dashboard: http://localhost:{env.get('SERVER_PORT', 5512)}")
    print(f"   - Test Client:      http://localhost:{env.get('CLIENT_PORT', 5511)}")
    print("\nPress Ctrl+C to stop all services.")

    # Keep main process alive
    try:
        server_process.wait()
        client_process.wait()
    except KeyboardInterrupt:
        cleanup(None, None)

if __name__ == "__main__":
    main()
