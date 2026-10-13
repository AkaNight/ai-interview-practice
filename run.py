"""Launch the local server and open the browser once it is ready."""
import argparse
import socket
import threading
import time
import webbrowser

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
            print(f"Port {args.port} is already in use. Close the old server or use --port 8001.")
            return 1
    from app.main import app
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, workers=1))
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
    raise SystemExit(main())
