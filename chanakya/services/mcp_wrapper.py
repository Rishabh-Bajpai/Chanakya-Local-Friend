import sys
import subprocess
import json

def is_json_rpc(line: str) -> bool:
    try:
        obj = json.loads(line)
        return isinstance(obj, dict) and 'jsonrpc' in obj
    except ValueError:
        return False

def main():
    if len(sys.argv) < 2:
        print("Usage: mcp_wrapper.py <command> [args...]", file=sys.stderr)
        sys.exit(1)
    
    cmd = sys.argv[1:]
    
    try:
        # We start the subprocess in unbuffered mode
        process = subprocess.Popen(
            cmd,
            stdin=None,           # Inherit standard input from wrapper directly
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,            # Line buffered
            universal_newlines=True
        )

        def poll_stderr():
            for line in iter(process.stderr.readline, ''):
                sys.stderr.write(line)
                sys.stderr.flush()

        import threading
        stderr_thread = threading.Thread(target=poll_stderr, daemon=True)
        stderr_thread.start()

        for line in iter(process.stdout.readline, ''):
            if is_json_rpc(line):
                # Valid JSON-RPC, emit to stdout
                sys.stdout.write(line)
                sys.stdout.flush()
            else:
                # Any other stdout (e.g. logs) redirected to stderr to protect stdout
                sys.stderr.write(f"[redirected stdout] {line}")
                sys.stderr.flush()

        process.wait()
        sys.exit(process.returncode)
    except Exception as e:
        print(f"Wrapper failed to execute {cmd}: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()
