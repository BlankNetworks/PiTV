#!/usr/bin/env python3
"""Installed entry point, independent of the caller's current directory."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tvcleaner.__main__ import main
raise SystemExit(main())
