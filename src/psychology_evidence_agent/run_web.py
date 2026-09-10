"""Start the local Psychology Evidence Agent workspace in a browser."""

from __future__ import annotations

import threading
import webbrowser

import uvicorn


def open_browser() -> None:
    webbrowser.open_new("http://127.0.0.1:8000")


def main() -> int:
    """Start the local web workspace with the same host, port, and browser behavior."""
    from .api_server import app

    print("正在启动 Psychology Evidence Agent 网页端：http://127.0.0.1:8000")
    print("按 Ctrl+C 可停止服务。")
    threading.Timer(0.8, open_browser).start()
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
