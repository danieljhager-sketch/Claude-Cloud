"""Build replies the way the Claude API sends them, for the fake API in conftest.py."""

import itertools

_ids = itertools.count(1)


def message(content: list[dict], stop_reason: str = "tool_use") -> dict:
    """A Claude API reply in the shape the real API sends."""
    return {
        "id": f"msg_{next(_ids)}",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def tool_use(name: str, **arguments) -> dict:
    return {"type": "tool_use", "id": f"toolu_{next(_ids)}", "name": name, "input": arguments}
