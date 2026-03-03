"""
Generic Anthropic SDK agent loop.

Runs a tool→reply loop until stop_reason == 'end_turn'.
Tools are plain Python callables with associated JSON schemas.
"""

from typing import Callable, Dict, List, Any, Optional
import json
import anthropic


class Agent:
    """
    Generic agent loop using the Anthropic Messages API.

    Args:
        model: Anthropic model ID (e.g. 'claude-sonnet-4-6')
        system_prompt: System prompt string
        tools: List of dicts, each with keys:
               - 'name': str
               - 'description': str
               - 'input_schema': dict (JSON Schema object)
               - 'fn': callable that takes **kwargs and returns str
        max_turns: Maximum number of assistant turns before stopping
    """

    def __init__(
        self,
        model: str,
        system_prompt: str,
        tools: List[Dict],
        max_turns: int = 20,
    ):
        self.model = model
        self.system_prompt = system_prompt
        self.tools = tools
        self.max_turns = max_turns
        self.client = anthropic.Anthropic()

        # Tool dispatch map: name → callable
        self._tool_fns: Dict[str, Callable] = {
            t["name"]: t["fn"] for t in tools
        }

        # Tool schemas for the API (strip our custom 'fn' key)
        self._tool_schemas = [
            {k: v for k, v in t.items() if k != "fn"}
            for t in tools
        ]

    def run(self, user_message: str) -> str:
        """
        Run the agent loop starting with user_message.

        Returns the final text response from the assistant.
        """
        messages: List[Dict] = [{"role": "user", "content": user_message}]
        turns = 0

        while turns < self.max_turns:
            turns += 1

            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=self.system_prompt,
                tools=self._tool_schemas,
                messages=messages,
            )

            # Append assistant response to history
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                # Extract the final text block
                for block in response.content:
                    if hasattr(block, "text"):
                        return block.text
                return ""

            if response.stop_reason == "tool_use":
                # Execute all tool calls and collect results
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        tool_name = block.name
                        tool_input = block.input
                        tool_use_id = block.id

                        print(f"  [tool] {tool_name}({json.dumps(tool_input, default=str)[:120]})")

                        fn = self._tool_fns.get(tool_name)
                        if fn is None:
                            result = f"Error: unknown tool '{tool_name}'"
                        else:
                            try:
                                result = fn(**tool_input)
                            except Exception as exc:
                                result = f"Error executing {tool_name}: {exc}"

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": str(result),
                        })

                messages.append({"role": "user", "content": tool_results})
                continue

            # Any other stop reason — return what we have
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text
            return f"Stopped: {response.stop_reason}"

        return f"Reached max_turns ({self.max_turns})"
