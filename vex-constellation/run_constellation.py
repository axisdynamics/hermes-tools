#!/usr/bin/env python3
"""Standalone runner for VEX Constellation Hermes plugin."""
import importlib.util
import signal
import sys
import time
from pathlib import Path

plugin_path = Path(__file__).with_name("__init__.py")
spec = importlib.util.spec_from_file_location("vex_constellation_plugin", plugin_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)  # type: ignore[union-attr]

print(mod._start_server(), flush=True)

running = True

def stop(signum, frame):
    global running
    running = False
    try:
        print(mod._stop_server(), flush=True)
    except Exception as exc:
        print(f"stop error: {exc}", flush=True)

signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)

while running:
    time.sleep(1)
