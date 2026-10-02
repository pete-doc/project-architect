"""Проверки доказывают, что линтер, форматтер и проверка типов ловят нарушения."""

import subprocess
import sys
from pathlib import Path

BAD_STYLE = "import os\nimport sys\ndef f( x ):\n  return x\n"
BAD_TYPES = "def f(x: int) -> str:\n    return x\n"
REPO = Path(__file__).resolve().parent.parent


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", *args], cwd=cwd, capture_output=True, text=True, check=False
    )


def test_ruff_check_rejects_unused_imports(tmp_path: Path) -> None:
    (tmp_path / "bad.py").write_text(BAD_STYLE, encoding="utf-8")
    result = _run("ruff", "check", "bad.py", cwd=tmp_path)
    assert result.returncode != 0
    assert "F401" in result.stdout


def test_ruff_format_rejects_unformatted_code(tmp_path: Path) -> None:
    (tmp_path / "bad.py").write_text(BAD_STYLE, encoding="utf-8")
    result = _run("ruff", "format", "--check", "bad.py", cwd=tmp_path)
    assert result.returncode != 0


def test_pyright_rejects_type_error(tmp_path: Path) -> None:
    (tmp_path / "bad.py").write_text(BAD_TYPES, encoding="utf-8")
    result = _run("pyright", "bad.py", cwd=tmp_path)
    assert result.returncode != 0
    assert "reportReturnType" in result.stdout


def test_repository_is_clean() -> None:
    assert _run("ruff", "check", ".", cwd=REPO).returncode == 0
    assert _run("ruff", "format", "--check", ".", cwd=REPO).returncode == 0
