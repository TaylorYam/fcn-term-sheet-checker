"""Installation inputs must remain readable on non-UTF-8 Windows."""

import subprocess
import sys
from pathlib import Path


def test_pip_reads_constraints_with_cp950_locale():
    constraints = Path(__file__).resolve().parents[1] / "constraints.txt"
    probe = """
import locale
import sys
import runpy
locale.getpreferredencoding = lambda do_setlocale=True: 'cp950'
constraints = sys.argv[1]
sys.argv = ['pip', 'install', '--dry-run', '--no-deps', '--no-index', '-c', constraints, 'pip']
runpy.run_module('pip', run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, "-c", probe, str(constraints)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stderr
