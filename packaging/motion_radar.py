"""Entry point of the Windows helper (MotionRadar.exe).

Runs the engine with live WiFi and the sonar, keeps its data in the user's local app
data folder (the .exe may sit in Downloads or on a read-only drive), and opens the hosted
dashboard, which connects back to this machine.
"""

import os
import sys
from pathlib import Path

from radar.cli import main

if __name__ == "__main__":
    # Consoles on some Windows setups use a legacy code page; never crash on a character.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    data = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "MotionRadar"
    print("Motion Radar helper. Keep this window open; close it to stop sensing.\n")
    try:
        main(["--data", str(data), "serve", "--sonar", "--open-site", *sys.argv[1:]])
    except KeyboardInterrupt:
        pass
    except Exception as e:  # keep the window up so the message can be read
        print(f"\nMotion Radar stopped: {e}")
        input("Press Enter to close.")
