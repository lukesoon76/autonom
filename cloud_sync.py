#!/usr/bin/env python3
"""
Keep the Render deployment's curated core in sync with the bundled workbook.

The persistent disk seeds the vector store only once, so a redeploy that ships a
new data/Master_List.xlsx won't propagate on its own. On boot (cloud only) this
compares the workbook's hash to a marker on the disk and, if it changed, runs
import_eatlist against the disk store — which purges + rebuilds the Eat List rows
while PRESERVING Authority picks and member community reviews. Runs in a daemon
thread so the web service stays responsive; no-ops when nothing changed.
"""
import hashlib
import os
import threading

REPO = os.path.dirname(os.path.abspath(__file__))
WB = os.path.join(REPO, "data", "Master_List.xlsx")
MARKER = os.path.join(os.environ.get("AUTONOM_DATA_DIR", REPO), "core_version.txt")


def _hash() -> str:
    h = hashlib.sha256()
    with open(WB, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _run():
    try:
        cur = _hash()
        old = open(MARKER).read().strip() if os.path.exists(MARKER) else ""
        if cur == old:
            print("[cloud_sync] curated core already up to date")
            return
        print("[cloud_sync] bundled workbook changed — refreshing core…")
        import import_eatlist
        import_eatlist.run(WB)
        with open(MARKER, "w") as f:
            f.write(cur)
        print("[cloud_sync] curated core refreshed from workbook")
    except Exception as e:                          # never crash the app
        print(f"[cloud_sync] error: {type(e).__name__}: {e}")


def start():
    if os.path.exists(WB):
        threading.Thread(target=_run, daemon=True, name="autonom-core-sync").start()
