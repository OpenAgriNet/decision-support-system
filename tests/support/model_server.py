"""A chat-completions endpoint for tests that read what reached the model.

Pydantic AI rewrites a schema inside its OpenAI model class, so only a real
HTTP request shows what the model is sent.
"""

from __future__ import annotations

import json
from typing import Any

from werkzeug import Request, Response


class FakeModelServer:
    """Answers every chat request by calling the output tool with `arguments`."""

    def __init__(self, arguments: dict[str, Any]) -> None:
        self._arguments = arguments
        self.requests: list[dict[str, Any]] = []

    def chat(self, request: Request) -> Response:
        body = json.loads(request.get_data())
        self.requests.append(body)
        call = {
            "id": "call-1",
            "type": "function",
            "function": {
                "name": body["tools"][0]["function"]["name"],
                "arguments": json.dumps(self._arguments),
            },
        }
        message = {"role": "assistant", "content": None, "tool_calls": [call]}
        completion = {
            "id": "completion-1",
            "object": "chat.completion",
            "created": 0,
            "model": body["model"],
            "choices": [
                {"index": 0, "finish_reason": "tool_calls", "message": message}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        return Response(json.dumps(completion), content_type="application/json")

    @property
    def output_tool(self) -> dict[str, Any]:
        """The one tool on the one request made."""

        (request,) = self.requests
        (tool,) = request["tools"]
        return tool["function"]
