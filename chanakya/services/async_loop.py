import asyncio
import threading
from typing import Any

# A designated background event loop for all agent execution to allow
# persistent persistent connections to MCP processes across sync Flask requests.
_maf_loop: asyncio.AbstractEventLoop | None = None
_maf_thread: threading.Thread | None = None

def _start_background_loop(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    loop.run_forever()

def start_maf_event_loop() -> None:
    global _maf_loop, _maf_thread
    if _maf_loop is not None:
        return
    _maf_loop = asyncio.new_event_loop()
    _maf_thread = threading.Thread(
        target=_start_background_loop,
        args=(_maf_loop,),
        daemon=True,
        name="MAFLoopThread"
    )
    _maf_thread.start()

def get_maf_loop() -> asyncio.AbstractEventLoop:
    if _maf_loop is None:
        start_maf_event_loop()
    return _maf_loop  # type: ignore

def run_in_maf_loop(coro: Any) -> Any:
    """Run a coroutine safely in the shared MAF background event loop."""
    loop = get_maf_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result()
