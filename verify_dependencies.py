"""Verify the runtime dependencies without `python -c` quoting on PowerShell 5.1.

Exit code 0 = imports work, 1 = something needs installation or repair.
The installer additionally tracks requirements.txt SHA-256 and runs `pip check`.
"""
from __future__ import annotations

import importlib
import sys

MODULES = ('fastapi', 'uvicorn', 'pandas', 'numpy', 'requests', 'pytest', 'httpx', 'dotenv')


def main() -> int:
    failures = []
    for module in MODULES:
        try:
            importlib.import_module(module)
        except Exception as exc:
            failures.append(f'{module}: {type(exc).__name__}: {exc}')
    if failures:
        print('Dependencias ausentes ou com problema:')
        for failure in failures:
            print(' - ' + failure)
        return 1
    print('Importacao das dependencias: OK.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
