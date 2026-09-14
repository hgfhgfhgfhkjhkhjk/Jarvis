"""Низкоуровневые действия: запуск, завершение процессов, скриншоты, ссылки."""

import ctypes
import datetime
import logging
import os
import re
import socket
import subprocess
import webbrowser
from pathlib import Path
from urllib.parse import quote_plus

try:
    from PIL import ImageGrab
except ImportError:
    ImageGrab = None

from jarvis.matching import match_score, translit

log = logging.getLogger("jarvis.actions")

BROWSER_PROCS = ["chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "brave.exe", "browser.exe"]
SCREENSHOTS_DIR = Path.home() / "Pictures" / "Screenshots"

# --- UI Automation: pyautogui, pywinauto, pyperclip ------------------------

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
except Exception:
    pyautogui = None

try:
    import pywinauto
    from pywinauto import Desktop
except Exception:
    pywinauto = None

try:
    import pyperclip
except Exception:
    pyperclip = None


def _get_user32():
    if hasattr(ctypes, "windll"):
        return ctypes.windll.user32
    return None


def run_spec(spec) -> None:
    """Выполняет открывающее действие: ("uri"|"exe"|"cmd", значение)."""
    kind, value = spec
    log.info("Запуск: %s %s", kind, value)
    if kind == "cmd":
        subprocess.Popen(value)
    elif kind == "exe":
        os.startfile(value)
    else:  # uri / ссылка / всё, что умеет оболочка
        os.startfile(value)


def spec_from_string(action: str):
    """Превращает строку из config.json в spec для run_spec."""
    action = os.path.expandvars(action.strip())
    if "://" in action:
        return ("uri", action)
    return ("exe", action)


def open_url(url: str) -> None:
    log.info("Открываю ссылку: %s", url)
    webbrowser.open(url, new=2)


def open_browser() -> None:
    webbrowser.open("https://www.google.com", new=1)


def kill_process(image_name: str) -> bool:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    res = subprocess.run(
        ["taskkill", "/IM", image_name, "/F", "/T"],
        capture_output=True,
        creationflags=flags,
    )
    ok = res.returncode == 0
    log.info("taskkill %s -> %s", image_name, "ok" if ok else "не запущен")
    return ok


def close_browser() -> bool:
    return any([kill_process(p) for p in BROWSER_PROCS])


def minimize_window(title_part: str) -> bool:
    """Сворачивает первое видимое окно, в заголовке которого есть подстрока."""
    log.info("Сворачиваю окно с %r", title_part)
    if pywinauto is not None:
        try:
            desktop = Desktop(backend="uia")
            win = desktop.window(title_re=f"(?i).*{re.escape(title_part)}.*")
            if win.exists(timeout=1.0):
                win.minimize()
                log.info("Свернул окно через pywinauto: %r", title_part)
                return True
        except Exception:
            pass

    user32 = _get_user32()
    if user32 is not None:
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _enum(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, buf, 256)
                if title_part.lower() in buf.value.lower():
                    found.append(hwnd)
                    return False
            return True

        user32.EnumWindows(_enum, 0)
        if found:
            user32.ShowWindow(found[0], 6)  # SW_MINIMIZE
            log.info("Свернул окно с %r", title_part)
            return True
    return False


def focus_window(title_part: str) -> bool:
    """Активирует и переводит на передний план окно с указанным заголовком."""
    log.info("Фокус на окно: %r", title_part)
    if pywinauto is not None:
        try:
            desktop = Desktop(backend="uia")
            win = desktop.window(title_re=f"(?i).*{re.escape(title_part)}.*")
            if win.exists(timeout=1.0):
                win.restore()
                win.set_focus()
                log.info("Сфокусировано окно через pywinauto: %r", title_part)
                return True
        except Exception:
            pass

    user32 = _get_user32()
    if user32 is not None:
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _enum(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, buf, 256)
                if title_part.lower() in buf.value.lower():
                    found.append(hwnd)
                    return False
            return True

        user32.EnumWindows(_enum, 0)
        if found:
            hwnd = found[0]
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            log.info("Сфокусировано окно %r (HWND %s)", title_part, hwnd)
            return True
    return False


def maximize_window(title_part: str) -> bool:
    """Разворачивает окно на весь экран."""
    log.info("Разворачиваю окно с %r", title_part)
    if pywinauto is not None:
        try:
            desktop = Desktop(backend="uia")
            win = desktop.window(title_re=f"(?i).*{re.escape(title_part)}.*")
            if win.exists(timeout=1.0):
                win.maximize()
                return True
        except Exception:
            pass

    user32 = _get_user32()
    if user32 is not None:
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _enum(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, buf, 256)
                if title_part.lower() in buf.value.lower():
                    found.append(hwnd)
                    return False
            return True

        user32.EnumWindows(_enum, 0)
        if found:
            user32.ShowWindow(found[0], 3)  # SW_MAXIMIZE
            return True
    return False


def restore_window(title_part: str) -> bool:
    """Восстанавливает размер окна из свёрнутого или полноэкранного вида."""
    log.info("Восстанавливаю окно с %r", title_part)
    if pywinauto is not None:
        try:
            desktop = Desktop(backend="uia")
            win = desktop.window(title_re=f"(?i).*{re.escape(title_part)}.*")
            if win.exists(timeout=1.0):
                win.restore()
                return True
        except Exception:
            pass

    user32 = _get_user32()
    if user32 is not None:
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _enum(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, buf, 256)
                if title_part.lower() in buf.value.lower():
                    found.append(hwnd)
                    return False
            return True

        user32.EnumWindows(_enum, 0)
        if found:
            user32.ShowWindow(found[0], 9)  # SW_RESTORE
            return True
    return False


def close_window(title_part: str) -> bool:
    """Закрывает окно, в заголовке которого есть подстрока."""
    log.info("Закрываю окно с %r", title_part)
    if pywinauto is not None:
        try:
            desktop = Desktop(backend="uia")
            win = desktop.window(title_re=f"(?i).*{re.escape(title_part)}.*")
            if win.exists(timeout=1.0):
                win.close()
                return True
        except Exception:
            pass

    user32 = _get_user32()
    if user32 is not None:
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _enum(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, buf, 256)
                if title_part.lower() in buf.value.lower():
                    found.append(hwnd)
                    return False
            return True

        user32.EnumWindows(_enum, 0)
        if found:
            user32.PostMessageW(found[0], 0x0010, 0, 0)  # WM_CLOSE
            return True
    return False


def list_windows() -> list[str]:
    """Возвращает список заголовков всех видимых окон рабочего стола."""
    titles = []
    if pywinauto is not None:
        try:
            desktop = Desktop(backend="uia")
            for w in desktop.windows():
                if w.is_visible():
                    t = w.window_text().strip()
                    if t and t not in titles:
                        titles.append(t)
            if titles:
                return titles
        except Exception:
            pass

    user32 = _get_user32()
    if user32 is not None:
        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _enum(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, buf, 256)
                t = buf.value.strip()
                if t and t not in titles:
                    titles.append(t)
            return True

        user32.EnumWindows(_enum, 0)
    return titles


def uri_scheme_exists(scheme: str) -> bool:
    """Зарегистрирован ли URL-протокол (yandexmusic:// и т.п.)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, scheme) as k:
            winreg.QueryValueEx(k, "URL Protocol")
            return True
    except (OSError, ImportError):
        return False


def ensure_music_playing(hint: str = "музык|yandex|music", attempts: int = 2) -> None:
    """Если плеер запущен, но не играет — жмёт play именно его медиа-сессии.

    Свежезапущенный плеер регистрирует сессию не сразу, поэтому при её
    отсутствии повторяем попытку, в конце — общая медиа-клавиша.
    """
    import asyncio
    import threading

    async def _try() -> str:
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as Manager,
        )

        mgr = await Manager.request_async()
        sessions = list(mgr.get_sessions())
        matched = [s for s in sessions
                   if re.search(hint, s.source_app_user_model_id.lower())] or sessions
        for s in matched:
            status = s.get_playback_info().playback_status
            if status == 4:  # уже играет
                return "playing"
            await s.try_play_async()
            return "started"
        return "no_session"

    try:
        result = asyncio.run(_try())
    except Exception:
        log.exception("Media Control недоступен")
        result = "error"
    log.info("ensure_music_playing: %s (осталось попыток %d)", result, attempts)
    if result == "no_session" and attempts > 1:
        threading.Timer(5.0, ensure_music_playing, args=(hint, attempts - 1)).start()
    elif result in ("no_session", "error"):
        media_key("play")  # последний шанс — системная клавиша


def take_screenshot() -> Path:
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SCREENSHOTS_DIR / f"jarvis_{stamp}.png"
    if ImageGrab is not None:
        try:
            img = ImageGrab.grab(all_screens=True)
            img.save(path)
        except Exception:
            path.write_bytes(b"")
    else:
        path.write_bytes(b"")
    log.info("Скриншот: %s", path)
    return path


SEARCH_URLS = {
    "google": "https://www.google.com/search?q={}",
    "youtube": "https://www.youtube.com/results?search_query={}",
    "wiki": "https://ru.wikipedia.org/w/index.php?search={}",
}


def open_search(engine: str, query: str) -> None:
    open_url(SEARCH_URLS.get(engine, SEARCH_URLS["google"]).format(quote_plus(query)))


def open_site_lucky(name: str) -> None:
    """Открывает сайт по названию через DuckDuckGo «мне повезёт» (редирект
    на первый результат). Работает для любого сайта и не зависит от DNS."""
    open_url("https://duckduckgo.com/?q=" + quote_plus("\\" + name))


def google_search(query: str) -> None:
    open_search("google", query)


def open_path(path, minimized: bool = False) -> None:
    log.info("Открываю%s: %s", " свёрнуто" if minimized else "", path)
    if minimized:
        # start /min работает и для exe, и для .lnk, и для URI
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(f'start /min "" "{path}"', shell=True, creationflags=flags)
        return
    try:
        if hasattr(os, "startfile"):
            os.startfile(path)
        else:
            webbrowser.open(Path(path).absolute().as_uri())
    except OSError:
        # сломанная ассоциация файла — показываем в браузере или в проводнике
        log.warning("Нет ассоциации для %s, открываю через браузер", path)
        try:
            webbrowser.open(Path(path).absolute().as_uri())
        except Exception:
            subprocess.Popen(["explorer", "/select,", str(path)])


# --- мультимедийные клавиши -------------------------------------------------

_VK = {"play": 0xB3, "stop": 0xB2, "next": 0xB0, "prev": 0xB1,
       "mute": 0xAD, "vol_up": 0xAF, "vol_down": 0xAE}
# LLM любит синонимы — приводим к play/pause-тогглу
_VK_ALIASES = {"pause": "play", "play_pause": "play", "toggle": "play", "resume": "play"}
_KEYUP = 0x0002


def media_key(name: str, times: int = 1) -> bool:
    """Жмёт системную медиа-клавишу (как на клавиатуре): play/next/vol_up..."""
    name = _VK_ALIASES.get(name, name)
    vk = _VK.get(name)
    if vk is None:
        return False
    log.info("Медиа-клавиша: %s x%d", name, times)
    user32 = _get_user32()
    if user32 is not None:
        for _ in range(times):
            user32.keybd_event(vk, 0, 0, 0)
            user32.keybd_event(vk, 0, _KEYUP, 0)
        return True
    return False


# --- произвольные сайты ---------------------------------------------------

_TLD_WORDS = {"ру": "ru", "ком": "com", "орг": "org", "нет": "net",
              "ио": "io", "рф": "xn--p1ai", "точка": ""}


def spoken_domain(spoken: str) -> str | None:
    """«хабр точка ру» -> https://habr.ru (домен, продиктованный через «точка»)."""
    if "точка" not in spoken:
        return None
    parts = [p.strip() for p in spoken.split("точка") if p.strip()]
    if len(parts) < 2:
        return None
    tld = _TLD_WORDS.get(parts[-1], translit(parts[-1].replace(" ", "")))
    host = ".".join(translit(p.replace(" ", "")) for p in parts[:-1]) + "." + tld
    if re.fullmatch(r"[a-z0-9.\-]+\.[a-z0-9\-]{2,}", host):
        return "https://" + host
    return None


_dns_trust: bool | None = None


def _dns_trustworthy() -> bool:
    """Паркинг/провайдерский DNS «резолвит» любую абракадабру — тогда
    угадывать домены по DNS бессмысленно и опасно. Проверяем один раз."""
    global _dns_trust
    if _dns_trust is None:
        import random
        import string

        junk = "".join(random.choices(string.ascii_lowercase, k=16))
        _dns_trust = True
        for tld in (".ru", ".com"):
            try:
                socket.getaddrinfo(junk + tld, 443)
                log.warning("DNS отвечает на мусорный домен %s — угадывание сайтов отключено", junk + tld)
                _dns_trust = False
                break
            except OSError:
                continue
    return _dns_trust


def guess_site(spoken: str) -> str | None:
    """Пробует превратить «хабр» в живой домен: habr.ru / habr.com / ...

    Только короткие цели: длинная фраза — это почти наверняка ошибка
    распознавания, а паркинг-DNS «отвечает» на любую абракадабру.
    """
    if len(spoken.split()) > 2:
        return None
    base = translit(spoken.replace(" ", "").replace("-", ""))
    if not re.fullmatch(r"[a-z0-9]{2,14}", base):
        return None
    if not _dns_trustworthy():
        return None
    for tld in (".ru", ".com", ".net", ".org", ".io"):
        host = base + tld
        try:
            socket.getaddrinfo(host, 443)
            log.info("Сайт угадан: %r -> %s", spoken, host)
            return "https://" + host
        except OSError:
            continue
    return None


# --- закрытие произвольных программ ----------------------------------------

# Эти процессы нельзя убивать ни при каком совпадении
_KILL_BLACKLIST = {"system", "svchost", "csrss", "winlogon", "wininit", "services",
                   "lsass", "dwm", "smss", "fontdrvhost", "registry", "idle",
                   "explorer", "python", "pythonw", "conhost", "audiodg"}


def list_processes() -> set[str]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        res = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="cp866",
            creationflags=flags,
        )
    except (FileNotFoundError, OSError):
        return set()
    names = set()
    for line in res.stdout.splitlines():
        if line.startswith('"'):
            names.add(line.split('","')[0].strip('"'))
    return names


def find_process(target: str, threshold: float = 0.8) -> str | None:
    """Имя exe запущенного процесса, лучше всего похожего на сказанное."""
    best_exe, best_score = None, 0.0
    for exe in list_processes():
        raw = exe.removesuffix(".exe").removesuffix(".EXE")
        base = raw.lower()
        if base in _KILL_BLACKLIST:
            continue
        clean = re.sub(r"\d+$", "", base)  # obs64 -> obs
        # RobloxPlayerInstaller -> roblox player installer
        spaced = re.sub(r"(?<=[a-zа-я0-9])(?=[A-ZА-Я])", " ", raw).lower()
        score = max(match_score(target, base), match_score(target, clean),
                    match_score(target, spaced))
        if score > best_score:
            best_exe, best_score = exe, score
    if best_score >= threshold:
        log.info("Процесс: %r -> %s (score %.2f)", target, best_exe, best_score)
        return best_exe
    log.info("Процесс для %r не найден (лучший score %.2f, %r)", target, best_score, best_exe)
    return None


# --- Управление мышью и клавиатурой (pyautogui) -----------------------------

def mouse_click(x: int | None = None, y: int | None = None, button: str = "left", clicks: int = 1) -> bool:
    """Клик кнопкой мыши (left, right, middle) по координатам (x, y) или на текущем месте."""
    if pyautogui is None:
        log.warning("pyautogui недоступен для mouse_click")
        return False
    try:
        btn = button.lower() if button in ("left", "right", "middle") else "left"
        if x is not None and y is not None:
            pyautogui.click(x=int(x), y=int(y), button=btn, clicks=max(1, int(clicks)))
        else:
            pyautogui.click(button=btn, clicks=max(1, int(clicks)))
        log.info("Клик мыши: button=%s, clicks=%d, pos=(%s, %s)", btn, clicks, x, y)
        return True
    except Exception:
        log.exception("Ошибка при клике мыши")
        return False


def mouse_double_click(x: int | None = None, y: int | None = None) -> bool:
    """Двойной клик левой кнопкой мыши."""
    return mouse_click(x=x, y=y, button="left", clicks=2)


def mouse_right_click(x: int | None = None, y: int | None = None) -> bool:
    """Клик правой кнопкой мыши."""
    return mouse_click(x=x, y=y, button="right", clicks=1)


def mouse_move(x: int, y: int, duration: float = 0.2) -> bool:
    """Перемещает курсор мыши в экранные координаты (x, y)."""
    if pyautogui is None:
        log.warning("pyautogui недоступен для mouse_move")
        return False
    try:
        pyautogui.moveTo(x=int(x), y=int(y), duration=max(0.0, float(duration)))
        log.info("Мышь перемещена в (%d, %d)", int(x), int(y))
        return True
    except Exception:
        log.exception("Ошибка при перемещении мыши")
        return False


def mouse_scroll(clicks: int) -> bool:
    """Прокрутка колеса мыши: положительное число — вверх, отрицательное — вниз."""
    if pyautogui is None:
        log.warning("pyautogui недоступен для mouse_scroll")
        return False
    try:
        pyautogui.scroll(int(clicks))
        log.info("Прокрутка мыши: %d", int(clicks))
        return True
    except Exception:
        log.exception("Ошибка при прокрутке мыши")
        return False


def mouse_drag(x: int, y: int, duration: float = 0.5) -> bool:
    """Перетаскивание с зажатой левой кнопкой мыши до координат (x, y)."""
    if pyautogui is None:
        log.warning("pyautogui недоступен для mouse_drag")
        return False
    try:
        pyautogui.dragTo(x=int(x), y=int(y), duration=max(0.0, float(duration)), button="left")
        log.info("Перетаскивание мыши в (%d, %d)", int(x), int(y))
        return True
    except Exception:
        log.exception("Ошибка при перетаскивании мыши")
        return False


def get_mouse_position() -> tuple[int, int] | None:
    """Возвращает текущие экранные координаты курсора мыши (x, y)."""
    if pyautogui is None:
        return None
    try:
        pos = pyautogui.position()
        return (pos.x, pos.y)
    except Exception:
        return None


def get_screen_size() -> tuple[int, int]:
    """Возвращает разрешение экрана (ширина, высота) в пикселях."""
    if pyautogui is not None:
        try:
            sz = pyautogui.size()
            return (sz.width, sz.height)
        except Exception:
            pass
    user32 = _get_user32()
    if user32 is not None:
        try:
            return (user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
        except Exception:
            pass
    return (1920, 1080)


def type_text(text: str, interval: float = 0.0) -> bool:
    """Печатает текст в активное окно. Для кириллицы/Unicode используется буфер обмена."""
    if not text:
        return False
    log.info("Ввод текста (%d симв.): %r", len(text), text[:50] + ("..." if len(text) > 50 else ""))

    # Для русского языка и спецсимволов буфер обмена даёт 100% надёжность без сбоев раскладки
    is_ascii = all(ord(c) < 128 for c in text)
    if not is_ascii or "\n" in text:
        if pyperclip is not None:
            try:
                old = ""
                try:
                    old = pyperclip.paste()
                except Exception:
                    pass
                pyperclip.copy(text)
                hotkey("ctrl", "v")
                if old:
                    import threading
                    threading.Timer(0.5, lambda: pyperclip.copy(old)).start()
                return True
            except Exception:
                log.exception("Не удалось вставить текст через pyperclip")

    if pyautogui is not None:
        try:
            pyautogui.write(text, interval=interval)
            return True
        except Exception:
            log.exception("Не удалось напечатать текст через pyautogui")
    return False


def press_key(key: str) -> bool:
    """Нажимает одну клавишу клавиатуры (enter, esc, tab, space, backspace, f5 и т.д.)."""
    log.info("Нажатие клавиши: %s", key)
    if pyautogui is not None:
        try:
            pyautogui.press(key.lower().strip())
            return True
        except Exception:
            log.exception("Не удалось нажать клавишу %r", key)
    return False


def hotkey(*keys: str) -> bool:
    """Нажимает комбинацию горячих клавиш (например: hotkey('ctrl', 'c'), hotkey('alt', 'tab'))."""
    log.info("Горячая комбинация: %s", " + ".join(keys))
    if pyautogui is not None:
        try:
            clean_keys = [k.lower().strip() for k in keys if k]
            pyautogui.hotkey(*clean_keys)
            return True
        except Exception:
            log.exception("Не удалось нажать хоткей %r", keys)
    return False


# --- Глубокое управление элементами UI (pywinauto) ---------------------------

def click_ui_element(window_title: str, control_name: str) -> bool:
    """Нажимает на элемент интерфейса (кнопку, пункт меню) внутри окна программы."""
    log.info("Клик по UI-элементу %r в окне %r", control_name, window_title)
    if pywinauto is not None:
        # Пробуем современный backend UIA
        try:
            desktop = Desktop(backend="uia")
            win = desktop.window(title_re=f"(?i).*{re.escape(window_title)}.*")
            if win.exists(timeout=1.5):
                win.set_focus()
                ctrl = win.child_window(title_re=f"(?i).*{re.escape(control_name)}.*")
                if ctrl.exists(timeout=1.5):
                    ctrl.click_input()
                    log.info("Элемент %r в окне %r успешно нажат (UIA)", control_name, window_title)
                    return True
        except Exception:
            log.debug("Поиск элемента через UIA не сработал, пробуем win32")

        # Фолбэк на backend win32
        try:
            desktop_w32 = Desktop(backend="win32")
            win_w32 = desktop_w32.window(title_re=f"(?i).*{re.escape(window_title)}.*")
            if win_w32.exists(timeout=1.0):
                ctrl_w32 = win_w32.child_window(title_re=f"(?i).*{re.escape(control_name)}.*")
                if ctrl_w32.exists(timeout=1.0):
                    ctrl_w32.click_input()
                    log.info("Элемент %r в окне %r успешно нажат (win32)", control_name, window_title)
                    return True
        except Exception:
            log.exception("Ошибка при клике по элементу через pywinauto")

    return False
