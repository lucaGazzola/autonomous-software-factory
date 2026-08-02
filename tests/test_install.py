"""Tests for the root install.sh one-liner installer.

The script is exercised with stub `python3`/`pipx`/`pip` binaries on PATH so
no network access is needed. Each stub logs its argv to ``$STUB_LOG`` so the
tests can assert exactly what the installer invoked.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "install.sh"
REPO_URL = "git+https://github.com/lucaGazzola/forgeo.git"
SH = shutil.which("sh")
assert SH, "sh must be available to run install.sh"

PY_VERSION_CHECK = "-c"
PYTHON_STUB = """printf 'python3 %s\\n' "$*" >> "$STUB_LOG"
if [ "$1" = "-c" ]; then
    exit "${PY_VERSION_OK:-0}"
fi
if [ "$1" = "-m" ] && [ "$2" = "site" ]; then
    printf '%s\\n' "$STUB_USER_BASE"
    exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then
    exit 0
fi
exit 0"""

GENERIC_STUB = """printf '%s %s\\n' "${0##*/}" "$*" >> "$STUB_LOG"
exit 0"""


def _write_stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _write_bin(bin_dir: Path, names: list[str]) -> None:
    bin_dir.mkdir(exist_ok=True)
    for name in names:
        if name == "python3":
            _write_stub(bin_dir, name, PYTHON_STUB)
        else:
            _write_stub(bin_dir, name, GENERIC_STUB)


def _run_install(tmp_path: Path, bin_dir: Path, *, python_ok: bool = True, user_base_on_path: bool = False) -> subprocess.CompletedProcess:
    log = tmp_path / "calls.log"
    user_base = tmp_path / "userbase"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{user_base / 'bin'}" if user_base_on_path else str(bin_dir),
        "STUB_LOG": str(log),
        "STUB_USER_BASE": str(user_base),
        "PY_VERSION_OK": "0" if python_ok else "1",
        "HOME": str(tmp_path),
    }
    return subprocess.run(
        [SH, str(INSTALL_SH)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "calls.log"
    if not log.exists():
        return []
    return log.read_text(encoding="utf-8").splitlines()


def test_happy_path_prefers_pipx(tmp_path):
    bin_dir = tmp_path / "bin"
    _write_bin(bin_dir, ["python3", "pipx", "pip"])

    result = _run_install(tmp_path, bin_dir)

    assert result.returncode == 0, result.stderr
    assert f"pipx install --force {REPO_URL}" in _calls(tmp_path)
    assert not any("python3 -m pip" in line for line in _calls(tmp_path))
    assert "factory init" in result.stdout
    assert "factory start" in result.stdout


def test_rerun_upgrades_instead_of_failing(tmp_path):
    bin_dir = tmp_path / "bin"
    _write_bin(bin_dir, ["python3", "pipx"])

    first = _run_install(tmp_path, bin_dir)
    second = _run_install(tmp_path, bin_dir)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert _calls(tmp_path).count(f"pipx install --force {REPO_URL}") == 2


def test_missing_interpreter_fails_with_actionable_error(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    result = _run_install(tmp_path, bin_dir)

    assert result.returncode != 0
    assert "Python 3.11 or newer is required" in result.stderr
    assert "pipx" not in " ".join(_calls(tmp_path))


def test_too_old_python_fails(tmp_path):
    bin_dir = tmp_path / "bin"
    _write_bin(bin_dir, ["python3"])

    result = _run_install(tmp_path, bin_dir, python_ok=False)

    assert result.returncode != 0
    assert "Python 3.11 or newer is required" in result.stderr


def test_pip_fallback_warns_when_user_bin_not_on_path(tmp_path):
    bin_dir = tmp_path / "bin"
    _write_bin(bin_dir, ["python3", "pip"])

    result = _run_install(tmp_path, bin_dir, user_base_on_path=False)

    assert result.returncode == 0, result.stderr
    assert f"python3 -m pip install --user --upgrade {REPO_URL}" in _calls(tmp_path)
    assert "not on your PATH" in result.stderr
    assert "factory init" in result.stdout


def test_pip_fallback_silent_when_user_bin_on_path(tmp_path):
    bin_dir = tmp_path / "bin"
    _write_bin(bin_dir, ["python3"])

    result = _run_install(tmp_path, bin_dir, user_base_on_path=True)

    assert result.returncode == 0, result.stderr
    assert f"python3 -m pip install --user --upgrade {REPO_URL}" in _calls(tmp_path)
    assert "not on your PATH" not in result.stderr
