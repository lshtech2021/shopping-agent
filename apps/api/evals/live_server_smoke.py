"""Boot the real ASGI app under uvicorn (lifespan included) and run one turn over HTTP.

Usage:  SA_DATABASE_URL=memory python evals/live_server_smoke.py
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402

settings.mock_browser = True  # local .env may point at live browsing

PORT = 8199
BASE = f"http://127.0.0.1:{PORT}"
REQ = "帮我挑一台 5000 元内用于开发的笔记本，内存至少 32G"


def main() -> int:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(40):
        try:
            httpx.get(f"{BASE}/health", timeout=1)
            break
        except httpx.HTTPError:
            time.sleep(0.25)
    try:
        print("health:", httpx.get(f"{BASE}/health").json())
        created = httpx.post(f"{BASE}/api/v1/sessions", json={"requirement": REQ, "site": "jd"}).json()
        body = httpx.post(
            f"{BASE}/api/v1/sessions/{created['session_id']}/messages",
            json={"content": REQ},
            timeout=30,
        ).json()
        print("reply:", body["reply"])
        print("interrupted:", body["interrupted"], "recs:", len(body["recommendations"]))
        print("log stages:", [e["stage"] for e in body["execution_logs"]][:8], "n =", len(body["execution_logs"]))
        console = httpx.get(f"{BASE}/console/")
        print("console:", console.status_code)
        return 0 if body["recommendations"] and console.status_code == 200 else 1
    finally:
        server.should_exit = True


if __name__ == "__main__":
    raise SystemExit(main())
