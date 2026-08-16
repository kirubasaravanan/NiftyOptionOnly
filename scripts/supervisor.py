"""Process supervisor for the FastAPI backend.

Keeps the API alive across SIGHUP / SIGTERM from the sandbox environment.
Restarts the worker up to N times within a sliding window.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import threading


def main():
    # Ignore signals that the sandbox may send to background processes
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)

    restarts = 0
    start_times: list[float] = []
    MAX_RESTARTS_PER_MIN = 10

    while True:
        now = time.time()
        # Trim window
        start_times = [t for t in start_times if now - t < 60]
        if len(start_times) >= MAX_RESTARTS_PER_MIN:
            print(f"[supervisor] too many restarts ({len(start_times)}/min) — sleeping 60s", flush=True)
            time.sleep(60)
            continue

        start_times.append(now)
        print(f"[supervisor] starting FastAPI (attempt #{restarts + 1})", flush=True)

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "nifty_engine.api"],
            cwd="/home/z/my-project",
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        # Forward output in a thread so we can keep monitoring signals
        def forward_output():
            assert proc.stdout is not None
            for line in iter(proc.stdout.readline, b""):
                sys.stdout.buffer.write(line)
                sys.stdout.buffer.flush()

        t = threading.Thread(target=forward_output, daemon=True)
        t.start()

        # Wait for child to exit
        rc = proc.wait()
        print(f"[supervisor] FastAPI exited (rc={rc})", flush=True)
        restarts += 1
        time.sleep(2)


if __name__ == "__main__":
    main()
