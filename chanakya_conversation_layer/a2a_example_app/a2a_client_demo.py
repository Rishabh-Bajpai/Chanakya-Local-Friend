import argparse
import asyncio

from a2a.client import ClientFactory, create_text_message_object
from a2a.types import TaskState


async def run(prompt: str, server_url: str) -> int:
    client = None
    try:
        client = await ClientFactory.connect(server_url)
        message = create_text_message_object(content=prompt)
        printed = False
        async for event in client.send_message(message):
            if isinstance(event, tuple):
                task, _update = event
                if task.status.state == TaskState.completed and task.status.message:
                    print(task.status.message.parts[0].root.text)
                    printed = True
                    break
                if task.status.state == TaskState.failed and task.status.message:
                    print(task.status.message.parts[0].root.text)
                    return 1
            elif hasattr(event, "parts") and event.parts:
                print(event.parts[0].root.text)
                printed = True
                break
        if not printed:
            print("No response received from the A2A server.")
            return 1
        return 0
    except Exception as exc:
        print(f"A2A client error: {exc}")
        return 1
    finally:
        if client is not None:
            await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send a prompt to the OpenCode A2A bridge."
    )
    parser.add_argument("prompt", nargs="?", default="Say hello from OpenCode via A2A.")
    parser.add_argument("--server-url", default="http://127.0.0.1:18770")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.prompt, args.server_url)))


if __name__ == "__main__":
    main()
