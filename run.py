"""Launch the local server and open the browser once it is ready."""
import argparse
import json
import socket
import sys
import threading
import time
import webbrowser
from urllib.request import urlopen

import uvicorn


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", args.port))
        except OSError:
            try:
                with urlopen(f"http://127.0.0.1:{args.port}/api/health", timeout=2) as response:
                    existing = json.load(response)
                if existing.get("application") == "ai-interview-practice" or (
                    existing.get("status") == "ok" and existing.get("ai_mode") in {"demo", "openai", "deepseek", "compatible"}
                    and "main_model" in existing and "version" in existing
                ):
                    if not args.no_browser:
                        webbrowser.open(f"http://127.0.0.1:{args.port}")
                    print(f"AI Interview Practice is already running at http://127.0.0.1:{args.port}")
                    return 0
            except (OSError, ValueError):
                pass
            print(f"Port {args.port} is already in use. Close the old server or use --port 8001.")
            return 1
    from app.main import app
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, workers=1,
                                          loop="asyncio", http="h11", ws="none"))
    if not args.no_browser:
        def open_when_ready():
            for _ in range(300):
                if server.started:
                    webbrowser.open(f"http://127.0.0.1:{args.port}")
                    return
                if server.should_exit:
                    return
                time.sleep(0.1)
        threading.Thread(target=open_when_ready, daemon=True).start()
    server.run()
    return 0


if __name__ == "__main__":
    try:
        result = main()
    except Exception:
        import traceback
        traceback.print_exc()
        result = 1
    if result and getattr(sys, "frozen", False) and sys.stdin and sys.stdin.isatty():
        input("Startup failed. Press Enter to close this window. ")
    raise SystemExit(result)
