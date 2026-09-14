"""LLM-модуль: родной Function Calling (Tools API в Ollama) и цепочки действий.

Нейросеть (Ollama) имеет доступ к реестру зарегистрированных Python-функций
и может строить и выполнять цепочки вызовов инструментов для управления Windows,
приложениями, мультимедиа, файлами и UI Automation, либо возвращать разговорный ответ.
"""

import inspect
import json
import logging
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from jarvis import actions, files

log = logging.getLogger("jarvis.brain")

TOOL_SYSTEM = (
    "Ты — Феникс, локальный голосовой ассистент на Windows. Характер: спокойный, "
    "вежливый, слегка ироничный, обращаешься к пользователю «сэр». "
    "Для выполнения действий на компьютере (запуск/закрытие программ, открытие сайтов, "
    "поиск, скриншоты, управление окнами, мышью, клавиатурой, файлами, UI) вызывай соответствующие инструменты (tools). "
    "Ты можешь вызывать несколько инструментов последовательно, выстраивая цепочки действий. "
    "Твой финальный текстовый ответ озвучивается вслух, поэтому отвечай КРАТКО (1-2 предложения), "
    "без списков, без markdown и эмодзи. "
    "Если пользователь просто общается или задаёт вопрос без команд компьютеру — отвечай текстом без вызова инструментов."
)

CHAT_SYSTEM = (
    "Ты — Феникс, локальный голосовой ассистент на Windows. Характер: спокойный, "
    "вежливый, слегка ироничный, обращаешься к пользователю «сэр». "
    "Отвечай КРАТКО — одно-три предложения, разговорным языком, без списков, "
    "без markdown и эмодзи: твой ответ озвучивается вслух."
)

SYSTEM_FALLBACK = """Ты разбираешь команды голосового ассистента на Windows. Отвечай ТОЛЬКО JSON.
Действия: open_app (открыть программу или игру; поле minimized=true — свёрнуто), close_app, open_site (открыть сайт), search (поиск), screenshot, open_file (открыть последний созданный файл), media_key (key: play|next|prev|vol_up|vol_down|mute), wait (seconds), answer (короткий ответ на вопрос), none (бессмыслица).
Поля: action; target — название программы или домен сайта; query — поисковый запрос; engine — google|youtube|wiki; reply — ответ для answer.
Ещё действия: open_folder (открыть папку), list_folder (что лежит в папке), create_file (создать файл: target — имя, folder — папка).
Если команд несколько — верни {"steps":[...]} со списком действий по порядку."""

ACTIONS = {"open_app", "close_app", "open_site", "search", "screenshot",
           "open_file", "media_key", "wait", "answer", "none",
           "open_folder", "list_folder", "create_file"}


@dataclass
class Tool:
    name: str
    description: str
    func: Callable[..., Any]
    parameters: dict = field(default_factory=dict)

    def to_ollama_format(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {
                    "type": "object",
                    "properties": {},
                },
            },
        }


class BrainResult:
    """Результат выполнения цепочки инструментов нейросетью."""
    def __init__(self, reply: str = "", steps: list | None = None):
        self.reply = (reply or "").strip()
        self.steps = steps or []

    def __str__(self) -> str:
        return self.reply

    def __bool__(self) -> bool:
        return bool(self.reply or self.steps)

    def __repr__(self) -> str:
        return f"BrainResult(reply={self.reply!r}, steps={len(self.steps)})"


def schema_from_callable(fn: Callable, name: str | None = None,
                         description: str | None = None,
                         parameters: dict | None = None) -> Tool:
    """Создает объект Tool из функции с автоматическим выводом схемы параметров."""
    tool_name = name or fn.__name__
    doc = inspect.getdoc(fn) or tool_name
    tool_desc = description or doc.strip().split("\n")[0]

    if parameters is not None:
        return Tool(name=tool_name, description=tool_desc, func=fn, parameters=parameters)

    sig = inspect.signature(fn)
    props = {}
    required = []

    type_map = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        dict: "object",
    }

    for p_name, param in sig.parameters.items():
        if p_name in ("self", "cls"):
            continue
        annotation = param.annotation
        p_type = type_map.get(annotation, "string") if annotation != inspect.Parameter.empty else "string"
        props[p_name] = {"type": p_type}
        if param.default == inspect.Parameter.empty:
            required.append(p_name)

    param_schema = {
        "type": "object",
        "properties": props,
    }
    if required:
        param_schema["required"] = required

    return Tool(name=tool_name, description=tool_desc, func=fn, parameters=param_schema)


class Brain:
    def __init__(self, model: str = "qwen2.5:1.5b-instruct",
                 url: str = "http://127.0.0.1:11434", timeout: float = 20.0):
        self.model = model
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.tools: dict[str, Tool] = {}
        self.handler = None
        self._register_default_tools()

        self.available = self._ping() or self._try_start()
        if self.available:
            log.info("LLM-модуль с Function Calling включён: %s через Ollama (%d инструментов)",
                     model, len(self.tools))
            threading.Thread(target=self._warmup, daemon=True, name="brain-warmup").start()
        else:
            log.warning("Ollama недоступна — LLM-фолбэк выключен (установка: winget install Ollama.Ollama)")

    def bind_handler(self, handler) -> None:
        """Связывает экземпляр IntentHandler для вызова расширенных действий."""
        self.handler = handler

    def register_tool(self, func: Callable | None = None, *,
                      name: str | None = None,
                      description: str | None = None,
                      parameters: dict | None = None):
        """Регистрирует Python-функцию в качестве инструмента для LLM (декоратор или метод)."""
        if func is None:
            def decorator(f: Callable):
                self.register_tool(f, name=name, description=description, parameters=parameters)
                return f
            return decorator

        tool = schema_from_callable(func, name=name, description=description, parameters=parameters)
        self.tools[tool.name] = tool
        log.info("Зарегистрирован инструмент для LLM: %s", tool.name)
        return tool

    def tool(self, *args, **kwargs):
        """Алиас декоратора @brain.tool для удобной регистрации функций."""
        return self.register_tool(*args, **kwargs)

    def unregister_tool(self, name: str) -> None:
        """Удаляет зарегистрированный инструмент."""
        if name in self.tools:
            del self.tools[name]
            log.info("Удален инструмент LLM: %s", name)

    def list_tools(self) -> list[str]:
        """Список зарегистрированных инструментов."""
        return list(self.tools.keys())

    def _register_default_tools(self) -> None:
        """Регистрирует встроенные инструменты управления компьютером и Windows."""

        def _open_app(target: str, minimized: bool = False) -> str:
            """Запустить программу, игру или приложение (например: стим, дискорд, блокнот, калькулятор)."""
            if self.handler is not None:
                return self.handler._do_open(target)
            actions.open_path(target, minimized=minimized)
            return f"Открываю {target}."

        def _close_app(target: str) -> str:
            """Закрыть программу или процесс по названию."""
            if self.handler is not None:
                return self.handler._do_close(target)
            exe = actions.find_process(target)
            if exe:
                actions.kill_process(exe)
                return f"Закрыл {exe}."
            return f"Процесс {target} не найден."

        def _open_site(url_or_domain: str) -> str:
            """Открыть веб-сайт или URL-ссылку в браузере."""
            if self.handler is not None:
                return self.handler._open_site(url_or_domain)
            url = url_or_domain if "://" in url_or_domain else f"https://{url_or_domain}"
            actions.open_url(url)
            return f"Открываю {url_or_domain}."

        def _search_internet(query: str, engine: str = "google") -> str:
            """Найти информацию в интернете (движок: google, youtube, wiki)."""
            actions.open_search(engine, query)
            return f"Ищу {query} в {engine}."

        def _take_screenshot() -> str:
            """Сделать снимок экрана (скриншот) и сохранить его."""
            path = actions.take_screenshot()
            if self.handler is not None:
                self.handler.last_file = path
            return f"Скриншот сохранён: {path.name}"

        def _open_last_file() -> str:
            """Открыть последний созданный файл или сделанный скриншот."""
            if self.handler is not None:
                return self.handler._open_last_file()
            return "Нет недавнего файла."

        def _open_folder(folder: str) -> str:
            """Открыть папку в проводнике (загрузки, рабочий стол, документы, картинки, музыка)."""
            p = files.resolve_folder(folder, explicit=True)
            if p:
                if self.handler is not None:
                    self.handler.last_folder = p
                files.open_folder(p)
                return f"Открываю папку {p.name}."
            return f"Папка {folder} не найдена."

        def _list_folder(folder: str = "") -> str:
            """Показать список файлов и папок в указанной директории."""
            target_folder = folder or (self.handler.last_folder if self.handler else "")
            p = files.resolve_folder(str(target_folder), explicit=True)
            if p:
                if self.handler is not None:
                    self.handler.last_folder = p
                return files.describe_folder(p)
            return f"Папка {folder} не найдена."

        def _create_file(name: str, folder: str = "desktop") -> str:
            """Создать новый файл в указанной папке."""
            target_folder = files.resolve_folder(folder, explicit=True) or (Path.home() / "Desktop")
            path = files.create_file(target_folder, name)
            if self.handler is not None:
                self.handler.last_file = path
            return f"Создал {path.name} в {target_folder.name}."

        def _media_key(key: str, times: int = 1) -> str:
            """Управление звуком и мультимедиа (key: play, pause, next, prev, vol_up, vol_down, mute)."""
            ok = actions.media_key(key, times=max(1, int(times)))
            return f"Медиа: {key} (x{times})" if ok else f"Неизвестная медиа-клавиша {key}"

        def _wait_seconds(seconds: float) -> str:
            """Подождать указанное количество секунд (пауза в цепочке)."""
            sec = min(max(float(seconds), 0.1), 15.0)
            time.sleep(sec)
            return f"Пауза {sec:.1f} сек."

        # --- UI Automation инструменты ---

        def _focus_window(title_part: str) -> str:
            """Активировать и перевести на передний план окно программы по части заголовка."""
            ok = actions.focus_window(title_part)
            return f"Окно {title_part} активировано." if ok else f"Окно {title_part} не найдено."

        def _minimize_window(title_part: str) -> str:
            """Свернуть окно программы по части заголовка."""
            ok = actions.minimize_window(title_part)
            return f"Окно {title_part} свёрнуто." if ok else f"Окно {title_part} не найдено."

        def _maximize_window(title_part: str) -> str:
            """Развернуть окно программы на весь экран."""
            ok = actions.maximize_window(title_part)
            return f"Окно {title_part} развёрнуто." if ok else f"Окно {title_part} не найдено."

        def _restore_window(title_part: str) -> str:
            """Восстановить исходный размер окна программы."""
            ok = actions.restore_window(title_part)
            return f"Окно {title_part} восстановлено." if ok else f"Окно {title_part} не найдено."

        def _close_window(title_part: str) -> str:
            """Закрыть окно программы по части заголовка."""
            ok = actions.close_window(title_part)
            return f"Окно {title_part} закрыто." if ok else f"Окно {title_part} не найдено."

        def _list_windows() -> str:
            """Получить список всех открытых видимых окон."""
            wins = actions.list_windows()
            return f"Открытые окна: {', '.join(wins[:10])}" if wins else "Нет видимых окон."

        def _mouse_click(x: int = None, y: int = None, button: str = "left", clicks: int = 1) -> str:
            """Кликнуть мышью в текущей позиции или по координатам (x, y). Кнопка: left, right, middle."""
            ok = actions.mouse_click(x=x, y=y, button=button, clicks=clicks)
            return "Клик мыши выполнен." if ok else "Ошибка клика мыши."

        def _mouse_move(x: int, y: int) -> str:
            """Переместить курсор мыши в координаты (x, y)."""
            ok = actions.mouse_move(x=x, y=y)
            return f"Мышь перемещена в ({x}, {y})." if ok else "Ошибка перемещения мыши."

        def _mouse_scroll(clicks: int) -> str:
            """Прокрутить колесо мыши: положительное — вверх, отрицательное — вниз."""
            ok = actions.mouse_scroll(clicks)
            return f"Прокрутка мыши: {clicks}." if ok else "Ошибка прокрутки мыши."

        def _type_text(text: str) -> str:
            """Ввести (напечатать) текст с клавиатуры в активное окно."""
            ok = actions.type_text(text)
            return f"Введён текст: {text[:30]}" if ok else "Ошибка ввода текста."

        def _press_key(key: str) -> str:
            """Нажать клавишу клавиатуры (enter, esc, tab, space, backspace, f5 и др.)."""
            ok = actions.press_key(key)
            return f"Нажата клавиша {key}." if ok else f"Ошибка нажатия клавиши {key}."

        def _hotkey(keys: list[str]) -> str:
            """Нажать сочетание клавиш (например: ['ctrl', 'c'], ['alt', 'tab'], ['win', 'd'])."""
            ok = actions.hotkey(*keys)
            return f"Нажато сочетание {' + '.join(keys)}." if ok else "Ошибка вызова сочетания клавиш."

        def _click_ui_element(window_title: str, control_name: str) -> str:
            """Нажать на конкретный элемент UI (кнопку, меню) внутри окна через UI Automation."""
            ok = actions.click_ui_element(window_title, control_name)
            return f"Нажат элемент {control_name} в {window_title}." if ok else f"Элемент {control_name} не найден."

        def _get_screen_size() -> str:
            """Получить разрешение экрана (ширина, высота) в пикселях."""
            w, h = actions.get_screen_size()
            return f"Разрешение экрана: {w}x{h}"

        # Регистрируем базовые инструменты
        self.register_tool(_open_app, name="open_app")
        self.register_tool(_close_app, name="close_app")
        self.register_tool(_open_site, name="open_site")
        self.register_tool(_search_internet, name="search_internet")
        self.register_tool(_take_screenshot, name="take_screenshot")
        self.register_tool(_open_last_file, name="open_last_file")
        self.register_tool(_open_folder, name="open_folder")
        self.register_tool(_list_folder, name="list_folder")
        self.register_tool(_create_file, name="create_file")
        self.register_tool(_media_key, name="media_key")
        self.register_tool(_wait_seconds, name="wait_seconds")

        # Регистрируем инструменты автоматизации Windows и интерфейса
        self.register_tool(_focus_window, name="focus_window")
        self.register_tool(_minimize_window, name="minimize_window")
        self.register_tool(_maximize_window, name="maximize_window")
        self.register_tool(_restore_window, name="restore_window")
        self.register_tool(_close_window, name="close_window")
        self.register_tool(_list_windows, name="list_windows")
        self.register_tool(_mouse_click, name="mouse_click")
        self.register_tool(_mouse_move, name="mouse_move")
        self.register_tool(_mouse_scroll, name="mouse_scroll")
        self.register_tool(_type_text, name="type_text")
        self.register_tool(_press_key, name="press_key")
        self.register_tool(_hotkey, name="hotkey")
        self.register_tool(_click_ui_element, name="click_ui_element")
        self.register_tool(_get_screen_size, name="get_screen_size")

    def _ping(self) -> bool:
        try:
            with urllib.request.urlopen(self.url + "/api/version", timeout=2):
                return True
        except OSError:
            return False

    def _try_start(self) -> bool:
        """Ollama не отвечает — пробуем поднять сервис самостоятельно."""
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(["ollama", "serve"], creationflags=flags,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            return False
        for _ in range(10):
            time.sleep(0.5)
            if self._ping():
                return True
        return False

    def _request_chat(self, messages: list, timeout: float,
                      tools: list | None = None, fmt: str | None = None,
                      temperature: float = 0.0, num_predict: int = 256) -> dict:
        """Отправляет запрос к Ollama /api/chat с поддержкой инструментов."""
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": -1,
            "options": {"temperature": temperature, "num_predict": num_predict},
        }
        if tools:
            payload["tools"] = tools
        if fmt:
            payload["format"] = fmt

        req = urllib.request.Request(
            f"{self.url}/api/chat",
            json.dumps(payload).encode("utf-8"),
            {"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
            return data.get("message", {})

    def execute_tool(self, name: str, args: dict) -> Any:
        """Выполняет зарегистрированную Python-функцию с переданными аргументами."""
        tool = self.tools.get(name)
        if not tool:
            log.warning("Инструмент %r не найден в реестре", name)
            return f"Инструмент {name} не зарегистрирован."
        try:
            sig = inspect.signature(tool.func)
            valid_args = {}
            for p_name, param in sig.parameters.items():
                if p_name in args:
                    valid_args[p_name] = args[p_name]
            result = tool.func(**valid_args)
            log.info("Выполнен инструмент %s(%s) -> %r", name, valid_args, result)
            return result
        except Exception as e:
            log.exception("Ошибка при выполнении инструмента %s с аргументами %s", name, args)
            return f"Ошибка при выполнении {name}: {e}"

    def run(self, cmd: str, history: list | None = None, max_steps: int = 5) -> BrainResult:
        """Нативный Function Calling loop: Ollama строит и выполняет цепочку вызовов инструментов."""
        if not self.available:
            return BrainResult()

        messages = [{"role": "system", "content": TOOL_SYSTEM}]
        if history:
            messages.extend(list(history)[-8:])
        messages.append({"role": "user", "content": cmd})

        tools_schema = [t.to_ollama_format() for t in self.tools.values()]
        executed_steps = []
        final_reply = ""

        t0 = time.time()
        for step_idx in range(max_steps):
            try:
                resp_msg = self._request_chat(
                    messages=messages,
                    timeout=self.timeout,
                    tools=tools_schema,
                    temperature=0.1,
                )
            except Exception:
                log.exception("Ошибка запроса к Ollama на шаге %d", step_idx)
                break

            tool_calls = resp_msg.get("tool_calls") or []
            content = (resp_msg.get("content") or "").strip()

            if not tool_calls:
                # LLM закончила цепочку или ответила текстом напрямую
                final_reply = content
                break

            # Добавляем ответ ассистента в контекст диалога
            messages.append(resp_msg)

            for tc in tool_calls:
                fn_name = tc.get("function", {}).get("name", "")
                raw_args = tc.get("function", {}).get("arguments", {})
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args)
                    except Exception:
                        args = {}
                else:
                    args = dict(raw_args or {})

                log.info("LLM вызов инструмента [%d]: %s(%s)", step_idx + 1, fn_name, args)
                step_result = self.execute_tool(fn_name, args)
                executed_steps.append({"tool": fn_name, "args": args, "result": step_result})

                tool_response = {
                    "role": "tool",
                    "content": str(step_result) if step_result is not None else "OK",
                    "name": fn_name,
                }
                if "id" in tc:
                    tool_response["tool_call_id"] = tc["id"]
                messages.append(tool_response)

        log.info("LLM Tool Calling (%.2f с, %d шагов): %r -> %s (ответ: %r)",
                 time.time() - t0, len(executed_steps), cmd,
                 [s["tool"] for s in executed_steps], final_reply)

        if not final_reply and executed_steps:
            results = [str(s["result"]) for s in executed_steps if s.get("result") and isinstance(s["result"], str)]
            final_reply = " ".join(results) if results else "Выполнено, сэр."

        return BrainResult(reply=final_reply, steps=executed_steps)

    def chat(self, cmd: str, history: list | None = None) -> str | None:
        """Разговорный ответ с учётом истории диалога."""
        if not self.available:
            return None
        msgs = ([{"role": "system", "content": CHAT_SYSTEM}]
                + list(history or [])[-10:]
                + [{"role": "user", "content": cmd}])
        try:
            t0 = time.time()
            resp = self._request_chat(msgs, timeout=self.timeout, temperature=0.5, num_predict=180)
            text = (resp.get("content") or "").strip()
            log.info("LLM-диалог (%.2f с): %r -> %r", time.time() - t0, cmd, text)
            return text or None
        except Exception:
            log.exception("LLM-диалог не удался")
            return None

    def _warmup(self) -> None:
        try:
            t0 = time.time()
            self._request_chat(
                [{"role": "system", "content": "Ответь кратко."}, {"role": "user", "content": "привет"}],
                timeout=120, temperature=0.0
            )
            log.info("LLM прогрета за %.1f с", time.time() - t0)
        except Exception:
            log.exception("Прогрев LLM не удался")
            self.available = False

    def parse(self, cmd: str) -> dict | None:
        """Разбор команды в структурный интент через Function Calling (с JSON-фолбэком)."""
        if not self.available:
            return None

        # 1. Сначала пробуем разобрать через нативный Tools API
        try:
            t0 = time.time()
            tools_schema = [t.to_ollama_format() for t in self.tools.values()]
            messages = [
                {"role": "system", "content": TOOL_SYSTEM},
                {"role": "user", "content": cmd},
            ]
            resp_msg = self._request_chat(messages, timeout=self.timeout, tools=tools_schema, temperature=0.0)
            tool_calls = resp_msg.get("tool_calls") or []
            content = (resp_msg.get("content") or "").strip()

            if tool_calls:
                parsed_steps = []
                for tc in tool_calls:
                    fn_name = tc.get("function", {}).get("name", "")
                    raw_args = tc.get("function", {}).get("arguments", {})
                    if isinstance(raw_args, str):
                        try:
                            args = json.loads(raw_args)
                        except Exception:
                            args = {}
                    else:
                        args = dict(raw_args or {})
                    step_intent = self._map_tool_to_intent(fn_name, args)
                    parsed_steps.append(step_intent)

                if len(parsed_steps) > 1:
                    intent = {"steps": parsed_steps}
                elif parsed_steps:
                    intent = parsed_steps[0]
                else:
                    intent = {"action": "answer", "reply": content or "Готово."}

                log.info("LLM parse через Tools API (%.2f с): %r -> %s", time.time() - t0, cmd, intent)
                return intent
            elif content:
                return {"action": "answer", "reply": content}
        except Exception:
            log.warning("Tool Calling в parse() не удался, пробую JSON-фолбэк для %r", cmd)

        # 2. Фолбэк на структурированный JSON-промпт (для старых версий Ollama / моделей без Tools)
        try:
            t0 = time.time()
            resp = self._request_chat(
                [{"role": "system", "content": SYSTEM_FALLBACK}, {"role": "user", "content": cmd}],
                timeout=self.timeout, fmt="json", temperature=0.0
            )
            raw = resp.get("content", "")
            intent = json.loads(raw)
            log.info("LLM JSON-фолбэк (%.2f с): %r -> %s", time.time() - t0, cmd, intent)
            if isinstance(intent, dict):
                if isinstance(intent.get("steps"), list):
                    steps = [s for s in intent["steps"]
                             if isinstance(s, dict) and s.get("action") in ACTIONS]
                    return {"steps": steps} if steps else None
                if intent.get("action") in ACTIONS:
                    return intent
        except Exception:
            log.exception("LLM JSON-фолбэк не справился с %r", cmd)

        return None

    @staticmethod
    def _map_tool_to_intent(name: str, args: dict) -> dict:
        """Преобразует вызов инструмента в формат классического интента."""
        if name == "open_app":
            return {"action": "open_app", "target": args.get("target", ""), "minimized": args.get("minimized", False)}
        if name == "close_app":
            return {"action": "close_app", "target": args.get("target", "")}
        if name == "open_site":
            return {"action": "open_site", "target": args.get("url_or_domain") or args.get("target", "")}
        if name == "search_internet":
            return {"action": "search", "query": args.get("query", ""), "engine": args.get("engine", "google")}
        if name == "take_screenshot":
            return {"action": "screenshot"}
        if name == "open_last_file":
            return {"action": "open_file"}
        if name == "media_key":
            return {"action": "media_key", "key": args.get("key", "play"), "times": int(args.get("times", 1) or 1)}
        if name == "wait_seconds":
            return {"action": "wait", "seconds": float(args.get("seconds", 1) or 1)}
        if name == "open_folder":
            return {"action": "open_folder", "target": args.get("folder") or args.get("target", "")}
        if name == "list_folder":
            return {"action": "list_folder", "target": args.get("folder") or args.get("target", "")}
        if name == "create_file":
            return {"action": "create_file", "target": args.get("name") or args.get("target", ""), "folder": args.get("folder", "desktop")}
        return {"action": name, **args}
