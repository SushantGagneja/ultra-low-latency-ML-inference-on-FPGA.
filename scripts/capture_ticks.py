#!/usr/bin/env python3
"""Capture Binance bookTicker snapshots to NDJSON for deterministic replay."""

from __future__ import annotations

import argparse
import json
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from websocket import WebSocketApp


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture Binance bookTicker ticks")
    parser.add_argument("--symbol", default="btcusdt")
    parser.add_argument("--output", default="data/bookticker_capture.ndjson")
    parser.add_argument("--max-messages", type=int, default=1000)
    parser.add_argument("--duration-sec", type=int, default=0)
    args = parser.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    url = f"wss://stream.binance.com:9443/ws/{args.symbol.lower()}@bookTicker"
    count = 0
    deadline = time.time() + args.duration_sec if args.duration_sec > 0 else 0.0
    stop = False
    ws_app: WebSocketApp

    def request_stop(*_: Any) -> None:
        nonlocal stop
        stop = True
        ws_app.close()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    with output.open("a", encoding="utf-8") as f:
        def on_message(ws: WebSocketApp, message: str) -> None:
            nonlocal count, stop
            if deadline and time.time() >= deadline:
                stop = True

            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                return

            row = {
                "captured_at": utc_now(),
                "symbol": args.symbol.lower(),
                "stream": "bookticker",
                "payload": payload,
            }
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
            f.flush()
            count += 1
            if count % 100 == 0:
                print(f"captured={count}")
            if stop or (args.max_messages > 0 and count >= args.max_messages):
                ws.close()

        ws_app = WebSocketApp(url, on_message=on_message)
        print(f"connecting={url}")
        print(f"output={output}")
        ws_app.run_forever(ping_interval=20, ping_timeout=10)

    print(f"complete messages={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
