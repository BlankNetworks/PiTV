#!/usr/bin/python3 -I
"""Installed USB bridge; this path is selected only by the TV controller."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tvcleaner.usb_bridge import main

raise SystemExit(main())
