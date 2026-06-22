"""Лаунчер Феникса: молча запускает `pythonw -m jarvis` без окна консоли.

Собирается в exe (scripts/build_exe.py) и кладётся в корень проекта. Сам
вычисляет рабочую папку (где лежит exe) и интерпретатор (pythonw из PATH),
поэтому в репозитории нет машинно-зависимых путей. Защищён от повторного
запуска именованным мьютексом, чтобы автозапуск не плодил копии.
"""

import ctypes
import os
import shutil
import subprocess
import sys
from pathlib import Path

MUTEX_NAME = "Global\\JarvisPhoenixSingleInstance"


def already_running() -> bool:
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW(None, False, MUTEX_NAME)
    return kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


def project_dir() -> Path:
    # frozen exe -> рядом с exe; обычный запуск -> рядом с этим файлом
    base = Path(sys.executable if getattr(sys, "frozen", False) else __file__)
    return base.resolve().parent


def find_pythonw() -> str:
    for name in ("pythonw.exe", "pythonw"):
        found = shutil.which(name)
        if found:
            return found
    # запасной путь: pythonw рядом с активным python
    cand = Path(sys.base_prefix) / "pythonw.exe"
    return str(cand) if cand.exists() else "pythonw"


def main() -> None:
    if already_running():
        return
    cwd = project_dir()
    pythonw = find_pythonw()
    creationflags = 0x08000000 | 0x00000008  # NO_WINDOW | DETACHED_PROCESS
    subprocess.Popen(
        [pythonw, "-m", "jarvis"],
        cwd=str(cwd),
        creationflags=creationflags,
        close_fds=True,
    )


if __name__ == "__main__":
    main()
