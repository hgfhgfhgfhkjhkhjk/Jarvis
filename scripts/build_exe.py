"""Сборка Феникс.exe (лёгкий лаунчер) и иконки.

Запуск: python scripts/build_exe.py
Результат: <корень>/Феникс.exe — кладётся рядом с пакетом jarvis.
"""

import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
ICON = BASE / "jarvis" / "icon.ico"
EXE_NAME = "Феникс"


def make_icon() -> None:
    """Иконка из того же рисунка, что и в трее (несколько размеров)."""
    from PIL import Image, ImageDraw

    def draw(size: int) -> Image.Image:
        k = size / 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((2 * k, 2 * k, 62 * k, 62 * k), fill=(18, 32, 58, 255),
                  outline=(86, 156, 255, 255), width=max(1, int(3 * k)))
        d.line((38 * k, 16 * k, 38 * k, 42 * k), fill=(86, 156, 255, 255),
               width=max(1, int(6 * k)))
        d.arc((20 * k, 30 * k, 42 * k, 52 * k), start=20, end=180,
              fill=(86, 156, 255, 255), width=max(1, int(6 * k)))
        return img

    sizes = [16, 24, 32, 48, 64, 128, 256]
    draw(256).save(ICON, sizes=[(s, s) for s in sizes])
    print("Иконка:", ICON)


def build() -> None:
    make_icon()
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile", "--noconsole", "--clean", "--noconfirm",
        "--name", EXE_NAME,
        "--icon", str(ICON),
        "--distpath", str(BASE / "dist"),
        "--workpath", str(BASE / "build"),
        "--specpath", str(BASE / "build"),
        str(BASE / "launcher.py"),
    ]
    print("PyInstaller:", " ".join(cmd))
    subprocess.run(cmd, check=True)

    src = BASE / "dist" / f"{EXE_NAME}.exe"
    dst = BASE / f"{EXE_NAME}.exe"
    dst.write_bytes(src.read_bytes())
    print("Готово:", dst)


if __name__ == "__main__":
    build()
