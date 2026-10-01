"""Installation inputs must remain readable on non-UTF-8 Windows."""

import subprocess
import sys
from pathlib import Path


def test_pip_reads_constraints_with_cp950_locale():
    constraints = Path(__file__).resolve().parents[1] / "constraints.txt"
    probe = """
import locale
import sys
from pathlib import Path
from pip._internal.utils.encoding import auto_decode
locale.getpreferredencoding = lambda do_setlocale=True: 'cp950'
raw = Path(sys.argv[1]).read_bytes()
assert auto_decode(raw) == raw.decode('utf-8')
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
