#!/usr/bin/env python3
import argparse
import json
import re
import sys
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

    json_text = json.dumps(
        data,
        indent=2 if args.pretty else None,
        ensure_ascii=False,
    )

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(json_text)
            if args.pretty:
                f.write("\n")
    else:
        print(json_text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
