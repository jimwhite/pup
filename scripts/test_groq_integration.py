#!/usr/bin/env python3
"""Integration test: one batch of cells → Groq json_schema → parsed results.

No Weaviate required.  Exercises the full prompt→LLM→parse pipeline.

Usage:
    # Requires GROQ_API_KEY in environment
    python scripts/test_groq_integration.py

    # With verbose LLM debug logging
    python scripts/test_groq_integration.py -v
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from summarize_kg import (
    SUMMARY_VERSIONS,
    CellBatchResponse,
    CellRecord,
    NotebookSummaryResponse,
    SummaryResult,
    _build_batch_cells_text,
    _cached_json_call,
    _json_response_to_result,
    _json_response_to_summaries,
    _load_prompt_templates,
    _render_prompt,
    BATCH_CELL_PROMPT,
    NOTEBOOK_CHUNK_PROMPT,
    _format_topic_section,
)
from langchain_openai import ChatOpenAI

log = logging.getLogger("groq-integration-test")


# ── Fake cells (no Weaviate needed) ──────────────────────────────────

SAMPLE_CELLS = [
    CellRecord(
        notebook_source="books/test/sample.lisp",
        cell_index=0,
        cell_type="markdown",
        code_text="",
        comment_text=(
            "; Sample book for integration testing\n"
            "; Defines a small utility for natural-number arithmetic."
        ),
        package="ACL2",
        is_portcullis=False,
    ),
    CellRecord(
        notebook_source="books/test/sample.lisp",
        cell_index=1,
        cell_type="code",
        code_text='(in-package "ACL2")',
        comment_text="",
        package="ACL2",
        is_portcullis=False,
    ),
    CellRecord(
        notebook_source="books/test/sample.lisp",
        cell_index=2,
        cell_type="code",
        code_text='(include-book "std/util/define" :dir :system)',
        comment_text="",
        package="ACL2",
        is_portcullis=False,
    ),
    CellRecord(
        notebook_source="books/test/sample.lisp",
        cell_index=3,
        cell_type="code",
        code_text=(
            "(define my-double ((x natp))\n"
            "  :returns (result natp)\n"
            "  (* 2 (nfix x)))"
        ),
        comment_text="",
        package="ACL2",
        is_portcullis=False,
        symbol_names=["ACL2::MY-DOUBLE"],
        symbol_kinds=["function"],
    ),
    CellRecord(
        notebook_source="books/test/sample.lisp",
        cell_index=4,
        cell_type="code",
        code_text=(
            "(defthm my-double-is-even\n"
            "  (implies (natp x)\n"
            "           (integerp (/ (my-double x) 2))))"
        ),
        comment_text="",
        package="ACL2",
        is_portcullis=False,
        symbol_names=["ACL2::MY-DOUBLE-IS-EVEN"],
        symbol_kinds=["theorem"],
    ),
]


def _build_cell_prompt(cells: list[CellRecord]) -> str:
    """Build a cell-batch prompt using v3 templates."""
    jinja_env = _load_prompt_templates("v3")
    cells_text = _build_batch_cells_text(cells)
    nb_src = cells[0].notebook_source
    return _render_prompt(
        jinja_env,
        "cell_batch.j2",
        BATCH_CELL_PROMPT,
        source_file=nb_src,
        topic_section=_format_topic_section(nb_src),
        continuation_section="",
        cells_text=cells_text,
    )


def _build_notebook_prompt(cell_summaries: list[str]) -> str:
    """Build a notebook-chunk prompt from cell summary text."""
    jinja_env = _load_prompt_templates("v3")
    return _render_prompt(
        jinja_env,
        "notebook_chunk.j2",
        NOTEBOOK_CHUNK_PROMPT,
        source_file="books/test/sample.lisp",
        topic_section="",
        cell_summaries="\n".join(cell_summaries),
    )


async def test_cell_batch(llm: ChatOpenAI, model: str) -> CellBatchResponse | dict:
    """Test Phase 1: cell batch → CellBatchResponse."""
    print("\n" + "=" * 60)
    print("TEST 1: Cell Batch → CellBatchResponse")
    print("=" * 60)

    prompt = _build_cell_prompt(SAMPLE_CELLS)
    print(f"Prompt length: {len(prompt)} chars, {len(prompt.encode())} bytes")
    print(f"Cells: {len(SAMPLE_CELLS)}")

    structured_llm = llm.with_structured_output(
        CellBatchResponse, method="json_schema", strict=True,
    )
    sem = asyncio.Semaphore(1)

    response, was_cached = await _cached_json_call(
        prompt, structured_llm, model, cache=None, sem=sem,
    )

    print(f"\nCached: {was_cached}")
    print(f"Response type: {type(response).__name__}")

    # Parse into summaries
    summaries, continuation = _json_response_to_summaries(response)

    print(f"Continuation: {continuation!r}")
    print(f"Cells covered: {sorted(summaries.keys())}")

    total_ideas = sum(len(v) for v in summaries.values())
    print(f"Total ideas: {total_ideas}")
    print()

    for cell_idx in sorted(summaries.keys()):
        for i, sr in enumerate(summaries[cell_idx]):
            print(f"  Cell {cell_idx} idea {i}:")
            if sr.what:
                print(f"    what: {sr.what}")
            if sr.why:
                print(f"    why:  {sr.why}")
            if sr.how:
                print(f"    how:  {sr.how}")
            if sr.symbol:
                print(f"    sym:  {sr.symbol}")
            print()

    # Basic assertions
    assert len(summaries) >= 3, f"Expected ≥3 cells covered, got {len(summaries)}"
    assert total_ideas >= 4, f"Expected ≥4 ideas, got {total_ideas}"
    # Cell 3 (my-double) should have at least one idea
    assert 3 in summaries, "Cell 3 (my-double) should have an idea"

    print("✓ Cell batch test PASSED")
    return response


async def test_notebook_summary(
    llm: ChatOpenAI, model: str,
    cell_response: CellBatchResponse | dict,
) -> None:
    """Test Phase 2: notebook summary → NotebookSummaryResponse."""
    print("\n" + "=" * 60)
    print("TEST 2: Notebook Summary → NotebookSummaryResponse")
    print("=" * 60)

    # Build cell summary text from the cell response
    summaries, _ = _json_response_to_summaries(cell_response)
    cell_text_parts: list[str] = []
    for cell_idx in sorted(summaries.keys()):
        for i, sr in enumerate(summaries[cell_idx]):
            entry = f"Cell {cell_idx}"
            if sr.what:
                entry += f"\n  what: {sr.what}"
            if sr.why:
                entry += f"\n  why: {sr.why}"
            if sr.how:
                entry += f"\n  how: {sr.how}"
            cell_text_parts.append(entry)

    prompt = _build_notebook_prompt(cell_text_parts)
    print(f"Prompt length: {len(prompt)} chars")

    structured_llm = llm.with_structured_output(
        NotebookSummaryResponse, method="json_schema", strict=True,
    )
    sem = asyncio.Semaphore(1)

    response, was_cached = await _cached_json_call(
        prompt, structured_llm, model, cache=None, sem=sem,
    )

    result = _json_response_to_result(response)

    print(f"\nNotebook summary:")
    print(f"  what: {result.what}")
    print(f"  why:  {result.why}")
    print(f"  how:  {result.how}")

    assert result.what, "Notebook 'what' should be non-empty"
    print("\n✓ Notebook summary test PASSED")


async def run_all():
    """Run all integration tests."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print("ERROR: GROQ_API_KEY not set in environment")
        sys.exit(1)

    version_cfg = SUMMARY_VERSIONS["v2-groq-gpt-oss"]
    model = version_cfg["model"]
    max_tokens = version_cfg.get("max_tokens")

    llm_kwargs = {
        "base_url": "https://api.groq.com/openai/v1",
        "api_key": api_key,
        "model": model,
    }
    if max_tokens:
        llm_kwargs["max_tokens"] = max_tokens

    llm = ChatOpenAI(**llm_kwargs)

    print(f"Model: {model}")
    print(f"Max tokens: {max_tokens}")
    print(f"Cells: {len(SAMPLE_CELLS)}")

    cell_response = await test_cell_batch(llm, model)
    await test_notebook_summary(llm, model, cell_response)

    print("\n" + "=" * 60)
    print("ALL INTEGRATION TESTS PASSED")
    print("=" * 60)


def main():
    verbose = "-v" in sys.argv or "--verbose" in sys.argv
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    asyncio.run(run_all())


if __name__ == "__main__":
    main()
