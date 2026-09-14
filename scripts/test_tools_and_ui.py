"""Unit/integration test for Ollama Tool Calling and UI Automation modules."""

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from jarvis.brain import Brain, BrainResult, Tool, schema_from_callable
from jarvis import actions
from jarvis.intents import IntentHandler


def test_tool_registration():
    print("Testing Tool Registration & Schema Generation...")
    brain = Brain(url="http://127.0.0.1:99999")  # offline dummy URL
    brain.available = False

    # Check default registered tools
    tools = brain.list_tools()
    assert "open_app" in tools, "open_app tool should be registered"
    assert "close_app" in tools, "close_app tool should be registered"
    assert "focus_window" in tools, "focus_window tool should be registered"
    assert "mouse_click" in tools, "mouse_click tool should be registered"
    assert "type_text" in tools, "type_text tool should be registered"
    assert "click_ui_element" in tools, "click_ui_element tool should be registered"

    # Test custom tool registration with decorator
    @brain.tool
    def calculate_sum(a: int, b: int) -> int:
        """Сложить два целых числа."""
        return a + b

    assert "calculate_sum" in brain.tools
    tool_obj = brain.tools["calculate_sum"]
    schema = tool_obj.to_ollama_format()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "calculate_sum"
    assert "Сложить два целых числа" in schema["function"]["description"]
    assert "a" in schema["function"]["parameters"]["properties"]
    assert "b" in schema["function"]["parameters"]["properties"]
    assert "a" in schema["function"]["parameters"]["required"]
    assert "b" in schema["function"]["parameters"]["required"]

    # Test tool execution directly
    res = brain.execute_tool("calculate_sum", {"a": 40, "b": 2})
    assert res == 42, f"Expected 42, got {res}"
    print("[OK] calculate_sum executed correctly: 40 + 2 =", res)


def test_tool_calling_execution_loop():
    print("\nTesting Brain.run with Tool Calling chain simulation...")
    brain = Brain(url="http://127.0.0.1:99999")
    brain.available = True

    execution_log = []

    @brain.tool
    def step_one(target: str) -> str:
        """Первый шаг."""
        execution_log.append(("step_one", target))
        return f"step_one({target}) done"

    @brain.tool
    def step_two(value: int) -> str:
        """Второй шаг."""
        execution_log.append(("step_two", value))
        return f"step_two({value}) done"

    # Mock _request_chat to simulate multi-step tool call sequence from Ollama
    calls = 0

    def mock_request_chat(messages, timeout, tools=None, fmt=None, temperature=0.0, num_predict=256):
        nonlocal calls
        calls += 1
        if calls == 1:
            # First response: LLM calls step_one
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {
                            "name": "step_one",
                            "arguments": {"target": "браузер"},
                        },
                    }
                ],
            }
        elif calls == 2:
            # Second response: LLM calls step_two after seeing tool output
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_2",
                        "function": {
                            "name": "step_two",
                            "arguments": {"value": 10},
                        },
                    }
                ],
            }
        else:
            # Final response: LLM provides spoken text answer
            return {
                "role": "assistant",
                "content": "Браузер запущен и настроен, сэр.",
            }

    brain._request_chat = mock_request_chat

    result = brain.run("запусти браузер и настрой масштаб на 10")
    assert result.reply == "Браузер запущен и настроен, сэр."
    assert len(result.steps) == 2
    assert execution_log == [("step_one", "браузер"), ("step_two", 10)]
    print("[OK] Multi-step tool chain executed successfully:")
    for step in result.steps:
        print(f"  -> {step['tool']}({step['args']}) -> {step['result']}")
    print("  -> Final reply:", result.reply)


def test_ui_automation_actions():
    print("\nTesting UI Automation action functions in actions.py...")

    # Screen resolution
    width, height = actions.get_screen_size()
    assert width > 0 and height > 0
    print(f"[OK] get_screen_size: {width}x{height}")

    # Window list
    wins = actions.list_windows()
    assert isinstance(wins, list)
    print(f"[OK] list_windows returned {len(wins)} windows")

    # Safe calls (mouse, keyboard, window control)
    pos = actions.get_mouse_position()
    print(f"[OK] get_mouse_position: {pos}")

    # Hotkey, type_text, press_key, mouse actions
    assert hasattr(actions, "mouse_click")
    assert hasattr(actions, "mouse_double_click")
    assert hasattr(actions, "mouse_right_click")
    assert hasattr(actions, "mouse_move")
    assert hasattr(actions, "mouse_scroll")
    assert hasattr(actions, "mouse_drag")
    assert hasattr(actions, "type_text")
    assert hasattr(actions, "press_key")
    assert hasattr(actions, "hotkey")
    assert hasattr(actions, "focus_window")
    assert hasattr(actions, "minimize_window")
    assert hasattr(actions, "maximize_window")
    assert hasattr(actions, "restore_window")
    assert hasattr(actions, "close_window")
    assert hasattr(actions, "click_ui_element")
    print("[OK] All UI automation functions are defined and accessible.")


def test_intent_handler_integration():
    print("\nTesting IntentHandler with Brain and UI Automation...")
    brain = Brain(url="http://127.0.0.1:99999")
    brain.available = True

    handler = IntentHandler(config={}, apps=[], brain=brain)
    assert brain.handler is handler

    # Mock brain.run
    def mock_run(cmd, history=None):
        if "финансовый отчет" in cmd:
            return BrainResult(reply="Отчет проанализирован, сэр.", steps=[{"tool": "analyze_report"}])
        return BrainResult()

    brain.run = mock_run

    res = handler.handle("проанализируй финансовый отчет")
    assert "Отчет проанализирован" in res
    print(f"[OK] IntentHandler routed to brain.run successfully: {res!r}")


if __name__ == "__main__":
    test_tool_registration()
    test_tool_calling_execution_loop()
    test_ui_automation_actions()
    test_intent_handler_integration()
    print("\nALL TESTS PASSED SUCCESSFULLY!")
