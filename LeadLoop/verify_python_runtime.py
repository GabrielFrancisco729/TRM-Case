"""Check for a usable 64-bit Python, avoiding python -c quoting issues on Windows.

No third-party packages are needed. Called with the executable being checked.
"""
from __future__ import annotations

import struct
import sys


def main() -> int:
    supported = (
        sys.version_info.major == 3
        and 10 <= sys.version_info.minor <= 13
        and struct.calcsize("P") == 8
    )
    if not supported:
        return 1
    print(sys.executable)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
