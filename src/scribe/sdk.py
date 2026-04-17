"""Async wrapper around the Claude Code SDK."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from claude_code_sdk import (
    AssistantMessage,
    ClaudeCodeOptions,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    query,
)
from claude_code_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from claude_code_sdk.types import StreamEvent

logger = logging.getLogger(__name__)


def _find_claude_cli() -> str | None:
    """Find the Claude Code CLI binary."""
    # Check PATH first
    found = shutil.which("claude")
    if found:
        return found

    # Windows desktop app location
    if os.name == "nt":
        local_app = os.environ.get("LOCALAPPDATA", "")
        if local_app:
            claude_code_dir = Path(local_app) / "packages"
            if claude_code_dir.exists():
                for pkg_dir in claude_code_dir.iterdir():
                    if "Claude" in pkg_dir.name:
                        # Search for claude.exe in the Roaming cache
                        roaming = pkg_dir / "LocalCache" / "Roaming" / "Claude" / "claude-code"
                        if roaming.exists():
                            for version_dir in sorted(roaming.iterdir(), reverse=True):
                                exe = version_dir / "claude.exe"
                                if exe.exists():
                                    return str(exe)

    return None

# Callback type: (chunk_id, event_type, data_dict) -> None
StreamCallback = Callable[[str, str, dict[str, Any]], Awaitable[None]]


@dataclass
class SDKResponse:
    """Collected response from a single SDK call."""

    text: str = ""
    session_id: str = ""
    duration_ms: int = 0
    total_cost_usd: float = 0.0
    usage: dict[str, Any] = field(default_factory=dict)
    is_error: bool = False


def _patch_message_parser():
    """Patch the SDK message parser to handle unknown message types gracefully."""
    try:
        from claude_code_sdk._internal import message_parser, client

        _original_parse = message_parser.parse_message

        def _patched_parse(data):
            try:
                return _original_parse(data)
            except Exception as e:
                if "Unknown message type" in str(e):
                    logger.debug("Skipping unknown message type: %s", data.get("type", "?"))
                    return None
                raise

        # Patch both the module and the imported reference in client
        message_parser.parse_message = _patched_parse
        client.parse_message = _patched_parse
    except Exception as e:
        logger.debug("Failed to patch message parser: %s", e)


_patch_message_parser()


async def _safe_query(prompt, options, transport=None):
    """Wrap query() to skip None messages from patched parser."""
    async for message in query(prompt=prompt, options=options, transport=transport):
        if message is not None:
            yield message


async def invoke(
    prompt: str,
    *,
    model: str | None = None,
    system_prompt: str | None = None,
    cwd: Path | None = None,
    allowed_tools: list[str] | None = None,
    max_turns: int | None = None,
    stream_callback: StreamCallback | None = None,
    callback_id: str = "",
) -> SDKResponse:
    """Invoke Claude Code SDK and collect the response.

    Args:
        prompt: The user prompt to send.
        model: Model ID (e.g., "claude-opus-4-0").
        system_prompt: System prompt to prepend.
        cwd: Working directory for tool execution.
        allowed_tools: Whitelist of tools Claude can use.
        max_turns: Max agentic turns.
        stream_callback: Async callback for streaming events.
        callback_id: Identifier passed to stream_callback (e.g., chunk ID).
    """
    options = ClaudeCodeOptions(
        model=model,
        system_prompt=system_prompt,
        cwd=str(cwd) if cwd else None,
        allowed_tools=allowed_tools or [],
        permission_mode="bypassPermissions",
        max_turns=max_turns or 50,
        include_partial_messages=stream_callback is not None,
    )

    # For long prompts, use streaming mode to avoid Windows command-line length limits
    cli_path = _find_claude_cli()
    use_streaming = len(prompt) > 20000

    if use_streaming:
        async def _prompt_stream():
            yield {"type": "user", "message": {"role": "user", "content": prompt}}

        transport = SubprocessCLITransport(
            prompt=_prompt_stream(),
            options=options,
            cli_path=cli_path,
        ) if cli_path else None
        query_prompt = _prompt_stream()
    else:
        transport = SubprocessCLITransport(
            prompt=prompt,
            options=options,
            cli_path=cli_path,
        ) if cli_path else None
        query_prompt = prompt

    response = SDKResponse()
    text_parts: list[str] = []

    async for message in _safe_query(query_prompt, options, transport):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
                    if stream_callback:
                        await stream_callback(
                            callback_id,
                            "text",
                            {"text": block.text, "total": "".join(text_parts)},
                        )
                elif isinstance(block, ToolUseBlock):
                    if stream_callback:
                        await stream_callback(
                            callback_id,
                            "tool_use",
                            {"name": block.name, "input": block.input},
                        )
                elif isinstance(block, ToolResultBlock):
                    if stream_callback:
                        content_str = ""
                        if isinstance(block.content, str):
                            content_str = block.content[:200]
                        await stream_callback(
                            callback_id,
                            "tool_result",
                            {
                                "tool_use_id": block.tool_use_id,
                                "content": content_str,
                                "is_error": block.is_error or False,
                            },
                        )

        elif isinstance(message, StreamEvent):
            if stream_callback:
                await stream_callback(
                    callback_id,
                    "stream",
                    {"event": message.event},
                )

        elif isinstance(message, ResultMessage):
            response.session_id = message.session_id
            response.duration_ms = message.duration_ms
            response.total_cost_usd = message.total_cost_usd or 0.0
            response.usage = message.usage or {}
            response.is_error = message.is_error
            # Use result text only if we didn't collect any from AssistantMessages
            if message.result and not text_parts:
                text_parts.append(message.result)

            if stream_callback:
                await stream_callback(
                    callback_id,
                    "result",
                    {
                        "session_id": message.session_id,
                        "duration_ms": message.duration_ms,
                        "cost": message.total_cost_usd or 0.0,
                        "is_error": message.is_error,
                    },
                )

    response.text = "".join(text_parts)
    return response


async def invoke_parallel(
    tasks: list[dict[str, Any]],
    *,
    max_concurrent: int = 3,
    stream_callback: StreamCallback | None = None,
) -> list[SDKResponse]:
    """Run multiple SDK calls in parallel with a concurrency limit.

    Args:
        tasks: List of dicts, each containing kwargs for invoke().
            Required: "prompt". Optional: "model", "system_prompt", "cwd",
            "allowed_tools", "max_turns", "callback_id".
        max_concurrent: Maximum concurrent SDK calls.
        stream_callback: Shared callback for all tasks.

    Returns:
        List of SDKResponse in the same order as tasks.
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _run_one(task: dict[str, Any]) -> SDKResponse:
        async with semaphore:
            return await invoke(
                prompt=task["prompt"],
                model=task.get("model"),
                system_prompt=task.get("system_prompt"),
                cwd=task.get("cwd"),
                allowed_tools=task.get("allowed_tools"),
                max_turns=task.get("max_turns"),
                stream_callback=stream_callback,
                callback_id=task.get("callback_id", ""),
            )

    results = await asyncio.gather(
        *[_run_one(t) for t in tasks],
        return_exceptions=True,
    )

    # Convert exceptions to error responses
    final: list[SDKResponse] = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            logger.error("SDK call failed for task %s: %s", i, r)
            resp = SDKResponse(is_error=True, text=str(r))
            final.append(resp)
        else:
            final.append(r)

    return final
