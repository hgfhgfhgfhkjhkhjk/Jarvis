"""Подбор микрофона: показывает устройства ввода и уровень сигнала.

Запуск: python scripts/mics.py
Скажите что-нибудь — у живого микрофона будет высокий пик. Затем впишите
его имя (или часть) в config.json: "input_device": "camo".
"""

import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

FS = 16000


def main() -> None:
    default = sd.query_devices(kind="input")["name"]
    print(f"Устройство по умолчанию: {default}\n")

    # уникальные по имени входные устройства
    seen = {}
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            seen.setdefault(d["name"][:24], i)

    print("Говорите/шумите — измеряю уровень каждого микрофона...\n")
    results = []
    for name, idx in seen.items():
        try:
            rec = sd.rec(int(1.2 * FS), samplerate=FS, channels=1, dtype="int16", device=idx)
            sd.wait()
            results.append((int(np.abs(rec).max()), idx, name))
        except Exception as e:
            results.append((-1, idx, f"{name} (ошибка: {str(e)[:24]})"))

    results.sort(reverse=True)
    for peak, idx, name in results:
        mark = "  <-- ЖИВОЙ, впишите его имя в config" if peak > 1500 else ""
        lvl = "ошибка" if peak < 0 else str(peak)
        print(f"  [{idx:2}] пик={lvl:>6}  {name}{mark}")

    print('\nВ config.json: "input_device": "<часть имени>"  (например "camo"),')
    print('или null — устройство по умолчанию. Перезапустите Феникс после правки.')


if __name__ == "__main__":
    main()
