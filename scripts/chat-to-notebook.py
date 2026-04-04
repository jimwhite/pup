#!/usr/bin/env python3
"""Convert a ChatGPT shared-chat JSON file to a Jupyter notebook (.ipynb).

User messages become markdown cells (rendered as "prompt" input).
Assistant responses become markdown cell outputs.
Metadata (model, timestamps, token counts, thoughts) goes into
collapsible <details> blocks appended to each assistant cell.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _walk_linear(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Walk the mapping tree, following first-child links, yielding messages in order."""
    mapping = data["mapping"]
    # Find the root node
    node = mapping.get("client-created-root") or mapping.get(
        next(
            nid
            for nid, n in mapping.items()
            if n.get("parent") is None or n.get("parent") not in mapping
        )
    )
    messages: list[dict[str, Any]] = []
    while node:
        msg = node.get("message")
        if msg:
            messages.append(msg)
        children = node.get("children", [])
        node = mapping.get(children[0]) if children else None
    return messages


def _ts_str(ts: float | None) -> str:
    if ts is None:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _text_parts(msg: dict[str, Any]) -> str:
    """Extract the displayable text from a message."""
    content = msg.get("content", {})
    ct = content.get("content_type", "")
    if ct == "text":
        parts = content.get("parts", [])
        return "\n".join(str(p) for p in parts if p)
    if ct == "thoughts":
        thoughts = content.get("thoughts", [])
        pieces = []
        for t in thoughts:
            if isinstance(t, dict):
                pieces.append(t.get("content") or t.get("summary", ""))
            elif isinstance(t, str):
                pieces.append(t)
        return "\n\n".join(p for p in pieces if p)
    return ""


def _is_visible(msg: dict[str, Any]) -> bool:
    meta = msg.get("metadata", {})
    return not meta.get("is_visually_hidden_from_conversation", False)


def _metadata_block(msg: dict[str, Any]) -> str:
    """Build a collapsible HTML <details> block with message metadata."""
    meta = msg.get("metadata", {})
    lines: list[str] = []

    model = meta.get("resolved_model_slug") or meta.get("model_slug")
    if model:
        lines.append(f"**Model:** `{model}`")

    ts = msg.get("create_time")
    if ts:
        lines.append(f"**Time:** {_ts_str(ts)}")

    tokens = meta.get("token_count")
    if tokens:
        lines.append(f"**Tokens:** {tokens}")

    msg_id = msg.get("id")
    if msg_id:
        lines.append(f"**Message ID:** `{msg_id}`")

    req_id = meta.get("request_id")
    if req_id:
        lines.append(f"**Request ID:** `{req_id}`")

    turn_id = meta.get("turn_exchange_id")
    if turn_id:
        lines.append(f"**Turn ID:** `{turn_id}`")

    if not lines:
        return ""

    inner = "  \n".join(lines)
    return f"\n\n<details><summary>metadata</summary>\n\n{inner}\n\n</details>"


def _thoughts_block(thoughts_msgs: list[dict[str, Any]]) -> str:
    """Build a collapsible block for thinking/reasoning messages."""
    pieces: list[str] = []
    for msg in thoughts_msgs:
        text = _text_parts(msg)
        if text:
            pieces.append(text)
    if not pieces:
        return ""
    inner = "\n\n---\n\n".join(pieces)
    return f"\n\n<details><summary>thinking</summary>\n\n{inner}\n\n</details>"


def _make_markdown_cell(source: str, **cell_meta: Any) -> dict[str, Any]:
    return {
        "cell_type": "markdown",
        "metadata": cell_meta,
        "source": source.splitlines(keepends=True),
    }


def convert(data: dict[str, Any]) -> dict[str, Any]:
    title = data.get("title", "ChatGPT Conversation")
    create_time = data.get("create_time")
    conversation_id = data.get("conversation_id", "")
    default_model = data.get("default_model_slug", "")

    messages = _walk_linear(data)

    cells: list[dict[str, Any]] = []

    # Title cell
    header_lines = [f"# {title}\n"]
    if create_time:
        header_lines.append(f"\n*{_ts_str(create_time)}*\n")
    if conversation_id:
        header_lines.append(f"\nConversation `{conversation_id}`\n")
    if default_model:
        header_lines.append(f"\nDefault model: `{default_model}`\n")
    cells.append(_make_markdown_cell("".join(header_lines), chatgpt={"type": "header"}))

    # Group messages into turns: user prompt → (intermediate steps) → final answer
    i = 0
    while i < len(messages):
        msg = messages[i]
        role = msg.get("author", {}).get("role", "")

        # Skip system / hidden messages
        if role == "system" or not _is_visible(msg):
            i += 1
            continue

        if role == "user":
            text = _text_parts(msg)
            if not text:
                i += 1
                continue
            user_meta: dict[str, Any] = {"chatgpt": {"role": "user"}}
            ts = msg.get("create_time")
            if ts:
                user_meta["chatgpt"]["create_time"] = ts
            source = f"**User:**\n\n{text}"
            source += _metadata_block(msg)
            cells.append(_make_markdown_cell(source, **user_meta))
            i += 1

            # Now collect everything up to (and including) the final assistant answer
            intermediate: list[dict[str, Any]] = []
            final_msg: dict[str, Any] | None = None
            while i < len(messages):
                nxt = messages[i]
                nxt_role = nxt.get("author", {}).get("role", "")
                if nxt_role == "user":
                    break  # next turn
                if nxt_role == "system" or not _is_visible(nxt):
                    i += 1
                    continue
                channel = nxt.get("channel")
                nxt_ct = nxt.get("content", {}).get("content_type", "")
                if nxt_role == "assistant" and channel == "final":
                    final_msg = nxt
                    i += 1
                    break
                # Intermediate: thoughts, code, tool, model_editable_context, etc.
                intermediate.append(nxt)
                i += 1

            if final_msg:
                text = _text_parts(final_msg)
                assistant_meta: dict[str, Any] = {"chatgpt": {"role": "assistant"}}
                ts = final_msg.get("create_time")
                if ts:
                    assistant_meta["chatgpt"]["create_time"] = ts
                model = (final_msg.get("metadata") or {}).get("resolved_model_slug") or (final_msg.get("metadata") or {}).get("model_slug")
                if model:
                    assistant_meta["chatgpt"]["model"] = model
                source = f"**Assistant:**\n\n{text}"
                # Attach intermediate thoughts/reasoning as collapsible block
                thought_msgs = [
                    m for m in intermediate
                    if m.get("content", {}).get("content_type") in ("thoughts", "reasoning_recap")
                ]
                if thought_msgs:
                    source += _thoughts_block(thought_msgs)
                source += _metadata_block(final_msg)
                cells.append(_make_markdown_cell(source, **assistant_meta))
            continue

        # Skip non-user-initiated messages at top level (shouldn't normally happen)
        i += 1

    # Build notebook
    notebook: dict[str, Any] = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {
                "display_name": "Markdown",
                "language": "markdown",
                "name": "markdown",
            },
            "language_info": {"name": "markdown"},
            "chatgpt": {
                "title": title,
                "conversation_id": conversation_id,
                "create_time": create_time,
                "update_time": data.get("update_time"),
                "default_model_slug": default_model,
                "source_url": data.get("continue_conversation_url", ""),
            },
        },
        "cells": cells,
    }
    return notebook


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert ChatGPT shared-chat JSON to a Jupyter notebook."
    )
    parser.add_argument("input", help="Path to the ChatGPT JSON file.")
    parser.add_argument(
        "-o",
        "--output",
        help="Output .ipynb path (default: same name with .ipynb extension).",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"error: {input_path} not found", file=sys.stderr)
        return 1

    with open(input_path, encoding="utf-8") as f:
        data = json.load(f)

    notebook = convert(data)

    output_path = Path(args.output) if args.output else input_path.with_suffix(".ipynb")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(notebook, f, indent=1, ensure_ascii=False)
        f.write("\n")

    print(f"Wrote {len(notebook['cells'])} cells to {output_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
