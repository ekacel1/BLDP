"""Permet l'invocation `python -m bldp ...`."""

from __future__ import annotations

import sys

from bldp.cli import main

if __name__ == "__main__":
    sys.exit(main())
