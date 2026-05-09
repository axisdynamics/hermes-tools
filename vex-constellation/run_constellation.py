#!/usr/bin/env python3
"""VEX Heartbeat — standalone launcher for Constellation + Autonomous mode.
Starts the HTTP node, activates autonomous monitoring, and keeps running.
Designed for systemd user service: vex-constellation.service
"""
import importlib.util, signal, sys, time
from pathlib import Path

plugin_path = Path(__file__).with_name("__init__.py")
spec = importlib.util.spec_from_file_location("vex_constellation_plugin", plugin_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# Start HTTP server
print(mod._start_server(), flush=True)

# Auto-activate autonomous mode
print(mod._start_autonomous(), flush=True)

running = True
def stop(signum, frame):
    global running
    running = False
    mod._stop_autonomous()
    mod._stop_server()
    print("VEX Heartbeat stopped.", flush=True)

signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)

while running:
    time.sleep(1)
