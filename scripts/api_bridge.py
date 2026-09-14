"""Bridge script to interact with Jarvis Python modules from Web API / CLI."""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

# Redirect all logging to stderr so stdout is pure JSON
logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from jarvis.brain import Brain
from jarvis.config import load_config
from jarvis.apps import build_apps
from jarvis.intents import IntentHandler


def get_bridge_instances():
    config = load_config(BASE)
    brain_cfg = config.get("brain", {})
    brain = None
    if config.get("use_llm", True):
        model = brain_cfg.get("model", "qwen2.5:1.5b-instruct")
        url = brain_cfg.get("url", "http://127.0.0.1:11434")
        timeout = float(brain_cfg.get("timeout_sec", 20.0))
        brain = Brain(model=model, url=url, timeout=timeout)

    apps = build_apps(config)
    handler = IntentHandler(config=config, apps=apps, brain=brain)
    return config, brain, handler


def cmd_status():
    config, brain, handler = get_bridge_instances()
    tools_detail = []
    if brain:
        for t in brain.tools.values():
            tools_detail.append(t.to_ollama_format())

    data = {
        "ok": True,
        "ollama_available": brain.available if brain else False,
        "model": brain.model if brain else None,
        "tools_count": len(brain.tools) if brain else 0,
        "tools": brain.list_tools() if brain else [],
        "tools_detail": tools_detail,
        "wake_word": config.get("wake_word", "феникс"),
        "platform": sys.platform,
    }
    print(json.dumps(data, ensure_ascii=False))


def cmd_command(query: str):
    t0 = time.time()
    config, brain, handler = get_bridge_instances()

    # Track steps if brain.run is executed
    reply = handler.handle(query)
    elapsed = round((time.time() - t0) * 1000, 1)

    steps = []
    # If last command was handled by brain, check if there were steps
    data = {
        "ok": True,
        "query": query,
        "reply": reply,
        "elapsed_ms": elapsed,
        "dialog": list(handler.dialog)[-5:],
    }
    print(json.dumps(data, ensure_ascii=False))


def cmd_tool(name: str, args_json: str):
    config, brain, handler = get_bridge_instances()
    if not brain:
        print(json.dumps({"ok": False, "error": "Brain not initialized"}))
        return

    try:
        args = json.loads(args_json) if args_json else {}
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"Invalid arguments JSON: {e}"}))
        return

    result = brain.execute_tool(name, args)
    print(json.dumps({"ok": True, "tool": name, "args": args, "result": result}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="Jarvis API Bridge")
    subparsers = parser.add_subparsers(dest="action")

    subparsers.add_parser("status")

    cmd_p = subparsers.add_parser("command")
    cmd_p.add_argument("query", type=str, help="Text command for Jarvis")

    tool_p = subparsers.add_parser("tool")
    tool_p.add_argument("name", type=str, help="Tool name")
    tool_p.add_argument("args", type=str, default="{}", nargs="?", help="JSON tool arguments")

    args = parser.parse_args()

    if args.action == "status":
        cmd_status()
    elif args.action == "command":
        cmd_command(args.query)
    elif args.action == "tool":
        cmd_tool(args.name, args.args)
    else:
        cmd_status()


if __name__ == "__main__":
    main()
