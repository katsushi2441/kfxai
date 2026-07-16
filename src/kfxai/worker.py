from __future__ import annotations

import json
import signal
import time

from .config import load_settings
from .engine import TradingEngine


_running = True


def _stop(*_args: object) -> None:
    global _running
    _running = False


def run_once() -> int:
    settings = load_settings()
    settings.validate(require_credentials=True)
    result = TradingEngine(settings).run_cycle()
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0


def main() -> int:
    settings = load_settings()
    settings.validate(require_credentials=True)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    engine = TradingEngine(settings)
    while _running:
        started = time.monotonic()
        try:
            result = engine.run_cycle()
            executed = sum(1 for action in result["actions"] if action["executed"])
            print(
                f"[kfxai] cycle={result['cycle_id']} mode={result['mode']} "
                f"actions={len(result['actions'])} executed={executed} errors={len(result['errors'])}",
                flush=True,
            )
        except Exception as exc:
            print(f"[kfxai] cycle failed: {exc}", flush=True)
        elapsed = time.monotonic() - started
        wait = max(1.0, settings.cycle_seconds - elapsed)
        deadline = time.monotonic() + wait
        while _running and time.monotonic() < deadline:
            time.sleep(min(1.0, deadline - time.monotonic()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

