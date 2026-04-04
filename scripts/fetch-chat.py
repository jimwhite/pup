#!/usr/bin/env python3
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


def fetch_html(url: str, timeout: int = 30) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def _decode_turbo_stream(arr: list[Any]) -> Any:
    """Decode a React Router turbo-stream flat array into a nested structure."""
    seen: dict[int, Any] = {}

    def _resolve(idx: int) -> Any:
        if idx < 0:
            return None
        if idx in seen:
            return seen[idx]
        val = arr[idx]
        if isinstance(val, dict):
            result: dict[str, Any] = {}
            seen[idx] = result
            for k, v in val.items():
                key_idx = int(k[1:])  # strip leading "_"
                key = _resolve(key_idx)
                value = _resolve(v)
                result[key] = value
            return result
        if isinstance(val, list):
            if len(val) == 2 and val[0] == "P":
                seen[idx] = None
                return None
            result_list: list[Any] = []
            seen[idx] = result_list
            for item in val:
                if isinstance(item, int):
                    result_list.append(_resolve(item))
                else:
                    result_list.append(item)
            return result_list
        seen[idx] = val
        return val

    return _resolve(0)


def _extract_stream_chunks(html: str) -> list[str]:
    """Extract payloads from __reactRouterContext.streamController.enqueue() calls."""
    pattern = re.compile(
        r"streamController\.enqueue\(\"(.*?)\"\);", re.DOTALL
    )
    chunks: list[str] = []
    for m in pattern.finditer(html):
        raw = m.group(1)
        # The captured text is the interior of a JS double-quoted string.
        # JSON uses the same escape rules, so let json.loads decode it.
        unescaped = json.loads('"' + raw + '"')
        chunks.append(unescaped)
    return chunks


def extract_shared_chat_json(url: str) -> Any:
    html = fetch_html(url)

    # Current format: React Router turbo-stream in streamController.enqueue()
    chunks = _extract_stream_chunks(html)
    for chunk in chunks:
        try:
            arr = json.loads(chunk)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(arr, list) or len(arr) < 30:
            continue
        decoded = _decode_turbo_stream(arr)
        if not isinstance(decoded, dict):
            continue
        loader_data = decoded.get("loaderData", {})
        for key, val in loader_data.items():
            if "share" not in key:
                continue
            resp_data = (val or {}).get("serverResponse", {}).get("data")
            if resp_data and isinstance(resp_data, dict) and "mapping" in resp_data:
                return resp_data
        # If no share route found, return the full decoded structure
        return decoded

    raise RuntimeError(
        "No conversation data found in the page. "
        "The page may require authentication or the format may have changed."
    )


# ── Notebook conversion ──────────────────────────────────────────────


def _walk_linear(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Walk the mapping tree, following first-child links, yielding messages in order."""
    mapping = data["mapping"]
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


def _resolve_citations(text: str, msg: dict[str, Any]) -> str:
    refs = (msg.get("metadata") or {}).get("content_references", [])
    if not refs:
        return text
    refs_sorted = sorted(
        (r for r in refs if r.get("start_idx") is not None and r.get("alt")),
        key=lambda r: r["start_idx"],
        reverse=True,
    )
    for ref in refs_sorted:
        start = ref["start_idx"]
        end = ref["end_idx"]
        alt = ref["alt"]
        text = text[:start] + alt + text[end:]
    return text


def _nb_text_parts(msg: dict[str, Any]) -> str:
    content = msg.get("content", {})
    ct = content.get("content_type", "")
    if ct == "text":
        parts = content.get("parts", [])
        text = "\n".join(str(p) for p in parts if p)
        return _resolve_citations(text, msg)
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
    pieces: list[str] = []
    for msg in thoughts_msgs:
        text = _nb_text_parts(msg)
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


def _convert_to_notebook(data: dict[str, Any]) -> dict[str, Any]:
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

    i = 0
    while i < len(messages):
        msg = messages[i]
        role = msg.get("author", {}).get("role", "")

        if role == "system" or not _is_visible(msg):
            i += 1
            continue

        if role == "user":
            text = _nb_text_parts(msg)
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

            intermediate: list[dict[str, Any]] = []
            final_msg: dict[str, Any] | None = None
            while i < len(messages):
                nxt = messages[i]
                nxt_role = nxt.get("author", {}).get("role", "")
                if nxt_role == "user":
                    break
                if nxt_role == "system" or not _is_visible(nxt):
                    i += 1
                    continue
                channel = nxt.get("channel")
                if nxt_role == "assistant" and channel == "final":
                    final_msg = nxt
                    i += 1
                    break
                intermediate.append(nxt)
                i += 1

            if final_msg:
                text = _nb_text_parts(final_msg)
                assistant_meta: dict[str, Any] = {"chatgpt": {"role": "assistant"}}
                ts = final_msg.get("create_time")
                if ts:
                    assistant_meta["chatgpt"]["create_time"] = ts
                model = (final_msg.get("metadata") or {}).get("resolved_model_slug") or (final_msg.get("metadata") or {}).get("model_slug")
                if model:
                    assistant_meta["chatgpt"]["model"] = model
                source = f"**Assistant:**\n\n{text}"
                thought_msgs = [
                    m for m in intermediate
                    if m.get("content", {}).get("content_type") in ("thoughts", "reasoning_recap")
                ]
                if thought_msgs:
                    source += _thoughts_block(thought_msgs)
                source += _metadata_block(final_msg)
                cells.append(_make_markdown_cell(source, **assistant_meta))
            continue

        i += 1

    return {
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract conversation JSON from a ChatGPT shared chat page."
    )
    parser.add_argument("url", help="Shared chat URL, e.g. https://chatgpt.com/share/...")
    parser.add_argument(
        "-o",
        "--output",
        help="Write JSON to this file instead of stdout.",
    )
    parser.add_argument(
        "--ipynb", "--notebook",
        action="store_true",
        help="Output as Jupyter notebook (.ipynb) instead of raw JSON.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    args = parser.parse_args()

    try:
        data = extract_shared_chat_json(args.url)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.ipynb:
        notebook = _convert_to_notebook(data)
        out_text = json.dumps(notebook, indent=1, ensure_ascii=False) + "\n"
    else:
        out_text = json.dumps(
            data,
            indent=2 if args.pretty else None,
            ensure_ascii=False,
        )
        if args.pretty:
            out_text += "\n"

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out_text)
        print(f"Wrote {args.output}", file=sys.stderr)
    else:
        print(out_text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
