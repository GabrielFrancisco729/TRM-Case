"""Start the local LeadLoop server under the Windows bootstrap.

The parent PowerShell process captures stdout and stderr in its startup log.
"""
from __future__ import annotations

import os
from pathlib import Path
import runpy
import sys
import traceback

ROOT = Path(__file__).resolve().parent


def main() -> int:
    try:
        os.chdir(ROOT)
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        # Run the unchanged application; the parent PowerShell transcript
        # captures standard output and errors without a second file writer.
        runpy.run_path(str(ROOT / 'server.py'), run_name='__main__')
    except KeyboardInterrupt:
        print('Servidor encerrado pelo usuario.', flush=True)
        return 0
    except Exception:
        print('\nFALHA DE INICIALIZACAO - traceback completo abaixo:', file=sys.stderr, flush=True)
        traceback.print_exc()
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
