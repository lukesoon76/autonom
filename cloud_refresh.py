#!/usr/bin/env python3
"""
In-app daily refresh scheduler for the Render deployment.

Render cron jobs can't mount a service's persistent disk, so the only safe way to
update the web app's disk-backed vector store is from inside the web process.
`start()` spawns ONE daemon thread that runs ig_cloud.run() once a day (dedup by
permalink makes it idempotent). Enabled only when AUTONOM_CLOUD_REFRESH is set,
so local dev is unaffected. Guarded so Streamlit reruns never spawn duplicates.
"""
import os
import threading
import time
from datetime import datetime, timedelta, timezone

_started = False
_lock = threading.Lock()


def _loop(hour_utc: int):
    # optional one-shot shortly after boot (off by default to avoid hammering
    # the API on repeated redeploys)
    if os.getenv("AUTONOM_REFRESH_ON_BOOT"):
        time.sleep(90)
        _run_safe()
    while True:
        now = datetime.now(timezone.utc)
        nxt = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        time.sleep(max(60, (nxt - now).total_seconds()))
        _run_safe()


def _run_safe():
    try:
        import ig_cloud
        n, msg = ig_cloud.run()
        print(f"[cloud_refresh] {msg}")
    except Exception as e:                          # never let the thread die
        print(f"[cloud_refresh] error: {type(e).__name__}: {e}")


def start():
    """Start the daily scheduler once per process."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    hour = int(os.getenv("AUTONOM_REFRESH_HOUR_UTC", "22"))   # 22:00 UTC ≈ 06:00 MYT
    threading.Thread(target=_loop, args=(hour,), daemon=True,
                     name="autonom-refresh").start()
    print(f"[cloud_refresh] daily Instagram refresh scheduled for {hour:02d}:00 UTC")
