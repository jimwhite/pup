"""Unit tests for summarize_kg.py helper functions.

Run:
    pytest scripts/test_summarize_kg.py -v
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import field
from unittest.mock import AsyncMock, MagicMock

import pytest

# Import the module under test.
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from summarize_kg import (
    CellRecord,
    SummaryResult,
    _strip_markdown_fences,
    _build_batch_cells_text,
    _batch_cells_by_size,
    _summary_tools_to_result,
    _tool_calls_to_summaries,
)


# ── Helpers ───────────────────────────────────────────────────────────

def _cell(
    index: int,
    cell_type: str = "code",
    code_text: str = "",
    comment_text: str = "",
    package: str = "ACL2",
    symbol_names: list[str] | None = None,
    symbol_kinds: list[str] | None = None,
) -> CellRecord:
    """Build a CellRecord for tests."""
    return CellRecord(
        notebook_source="test/notebook.lisp",
        cell_index=index,
        cell_type=cell_type,
        code_text=code_text,
        comment_text=comment_text,
        package=package,
        is_portcullis=False,
        symbol_names=symbol_names or [],
        symbol_kinds=symbol_kinds or [],
    )


# ── _strip_markdown_fences ────────────────────────────────────────────

class TestStripMarkdownFences:
    def test_bare_fences_stripped(self) -> None:
        text = "```\n; This is a comment\n; about something\n```\n"
        result = _strip_markdown_fences(text)
        assert result == "; This is a comment\n; about something\n"

    def test_no_fences_passthrough(self) -> None:
        text = "; Just a comment\n; no fences here\n"
        result = _strip_markdown_fences(text)
        assert result == text

    def test_fences_with_language_tag(self) -> None:
        text = "```lisp\n(defun foo (x) x)\n```\n"
        result = _strip_markdown_fences(text)
        assert result == "(defun foo (x) x)\n"

    def test_empty_between_fences(self) -> None:
        text = "```\n```\n"
        result = _strip_markdown_fences(text)
        # start == end, so original is returned
        assert result == text

    def test_multiline_content(self) -> None:
        text = "```\nline1\nline2\nline3\n```\n"
        result = _strip_markdown_fences(text)
        assert result == "line1\nline2\nline3\n"

    def test_fences_with_whitespace(self) -> None:
        text = "  ```\n; comment\n  ```  \n"
        result = _strip_markdown_fences(text)
        assert result == "; comment\n"

    def test_plain_text_unchanged(self) -> None:
        text = "no fences at all"
        result = _strip_markdown_fences(text)
        assert result == text

    def test_empty_string(self) -> None:
        result = _strip_markdown_fences("")
        assert result == ""

    def test_only_opening_fence(self) -> None:
        text = "```\nsome content\n"
        result = _strip_markdown_fences(text)
        # Doesn't end with ``` so no stripping
        assert result == text

    def test_backticks_in_middle(self) -> None:
        """Fences must wrap the whole text, not appear in the middle."""
        text = "before\n```\ncode\n```\nafter\n"
        result = _strip_markdown_fences(text)
        # Doesn't start with ``` so no stripping
        assert result == text


# ── _build_batch_cells_text ───────────────────────────────────────────

class TestBuildBatchCellsText:
    def test_code_cell_uses_code_text(self) -> None:
        cells = [_cell(0, "code", code_text="(defun foo (x) x)")]
        result = _build_batch_cells_text(cells)
        assert "(defun foo (x) x)" in result
        assert "[Cell 0]" in result

    def test_markdown_cell_uses_comment_text(self) -> None:
        cells = [_cell(0, "markdown", comment_text="; A comment")]
        result = _build_batch_cells_text(cells)
        assert "; A comment" in result

    def test_markdown_cell_strips_fences(self) -> None:
        cells = [_cell(0, "markdown",
                       comment_text="```\n; A comment\n```\n")]
        result = _build_batch_cells_text(cells)
        assert "```" not in result
        assert "; A comment" in result

    def test_code_cell_does_not_strip_fences(self) -> None:
        """Fence stripping only applies to non-code cells."""
        cells = [_cell(0, "code",
                       code_text="```\nsome text\n```\n")]
        result = _build_batch_cells_text(cells)
        assert "```" in result

    def test_symbol_names_shown(self) -> None:
        cells = [_cell(0, "code", code_text="(defun bar (x) x)",
                       symbol_names=["ACL2::BAR"],
                       symbol_kinds=["function"])]
        result = _build_batch_cells_text(cells)
        assert "ACL2::BAR" in result
        assert "function" in result

    def test_multiple_cells(self) -> None:
        cells = [
            _cell(0, "code", code_text="(+ 1 2)"),
            _cell(1, "markdown", comment_text="; note"),
            _cell(2, "code", code_text="(+ 3 4)"),
        ]
        result = _build_batch_cells_text(cells)
        assert "[Cell 0]" in result
        assert "[Cell 1]" in result
        assert "[Cell 2]" in result

    def test_empty_cell(self) -> None:
        cells = [_cell(0, "code", code_text="")]
        result = _build_batch_cells_text(cells)
        assert "(empty)" in result


# ── _batch_cells_by_size ──────────────────────────────────────────────

class TestBatchCellsBySize:
    def test_single_batch_when_small(self) -> None:
        cells = [_cell(i, code_text=f"cell{i}") for i in range(3)]
        batches = _batch_cells_by_size(cells, max_bytes=10000)
        assert len(batches) == 1
        assert len(batches[0]) == 3

    def test_splits_when_over_limit(self) -> None:
        # Each cell is ~200 bytes + 120 overhead = ~320
        cells = [_cell(i, code_text="x" * 200) for i in range(10)]
        # 320 * 3 = 960; set max to 1000 → ~3 per batch
        batches = _batch_cells_by_size(cells, max_bytes=1000)
        assert len(batches) > 1
        # All cells accounted for
        total = sum(len(b) for b in batches)
        assert total == 10

    def test_empty_input(self) -> None:
        batches = _batch_cells_by_size([], max_bytes=10000)
        assert batches == []

    def test_single_large_cell(self) -> None:
        """A single cell larger than max_bytes still gets its own batch."""
        cells = [_cell(0, code_text="x" * 20000)]
        batches = _batch_cells_by_size(cells, max_bytes=1000)
        assert len(batches) == 1
        assert len(batches[0]) == 1

    def test_preserves_order(self) -> None:
        cells = [_cell(i, code_text=f"cell{i}") for i in range(5)]
        batches = _batch_cells_by_size(cells, max_bytes=10000)
        indices = [c.cell_index for b in batches for c in b]
        assert indices == [0, 1, 2, 3, 4]


# ── _summary_tools_to_result ─────────────────────────────────────────

class TestSummaryToolsToResult:
    def test_what_why_how(self) -> None:
        tool_calls = [
            {"name": "SummaryWhat", "args": {"summary": "It does X"}},
            {"name": "SummaryWhy", "args": {"summary": "Because Y"}},
            {"name": "SummaryHow", "args": {"summary": "By doing Z"}},
        ]
        result = _summary_tools_to_result(tool_calls)
        assert result.what == "It does X"
        assert result.why == "Because Y"
        assert result.how == "By doing Z"

    def test_partial_result(self) -> None:
        tool_calls = [
            {"name": "SummaryWhat", "args": {"summary": "It does X"}},
        ]
        result = _summary_tools_to_result(tool_calls)
        assert result.what == "It does X"
        assert result.why == ""
        assert result.how == ""

    def test_empty_tool_calls(self) -> None:
        result = _summary_tools_to_result([])
        assert result.what == ""
        assert result.why == ""
        assert result.how == ""

    def test_unknown_tool_ignored(self) -> None:
        tool_calls = [
            {"name": "UnknownTool", "args": {"summary": "ignored"}},
            {"name": "SummaryWhat", "args": {"summary": "kept"}},
        ]
        result = _summary_tools_to_result(tool_calls)
        assert result.what == "kept"


# ── _tool_calls_to_summaries ─────────────────────────────────────────

class TestToolCallsToSummaries:
    def test_single_cell_single_summary(self) -> None:
        tool_calls = [
            {"name": "ReportWhat", "args": {"cell_number": 3, "summary": "What"}},
            {"name": "ReportWhy", "args": {"cell_number": 3, "summary": "Why"}},
            {"name": "ReportHow", "args": {"cell_number": 3, "summary": "How"}},
        ]
        summaries, cont = _tool_calls_to_summaries(tool_calls)
        assert 3 in summaries
        assert len(summaries[3]) == 1
        assert summaries[3][0].what == "What"
        assert summaries[3][0].why == "Why"
        assert summaries[3][0].how == "How"
        assert cont == ""

    def test_multi_summary_per_cell(self) -> None:
        """Multiple ReportWhat calls for the same cell create separate summaries."""
        tool_calls = [
            {"name": "ReportWhat", "args": {"cell_number": 5, "summary": "Idea 1"}},
            {"name": "ReportWhy", "args": {"cell_number": 5, "summary": "Because 1"}},
            {"name": "ReportWhat", "args": {"cell_number": 5, "summary": "Idea 2"}},
            {"name": "ReportWhy", "args": {"cell_number": 5, "summary": "Because 2"}},
        ]
        summaries, cont = _tool_calls_to_summaries(tool_calls)
        assert len(summaries[5]) == 2
        assert summaries[5][0].what == "Idea 1"
        assert summaries[5][0].why == "Because 1"
        assert summaries[5][1].what == "Idea 2"
        assert summaries[5][1].why == "Because 2"

    def test_multiple_cells(self) -> None:
        tool_calls = [
            {"name": "ReportWhat", "args": {"cell_number": 1, "summary": "W1"}},
            {"name": "ReportWhat", "args": {"cell_number": 2, "summary": "W2"}},
        ]
        summaries, cont = _tool_calls_to_summaries(tool_calls)
        assert 1 in summaries
        assert 2 in summaries

    def test_continuation_context(self) -> None:
        tool_calls = [
            {"name": "ReportWhat", "args": {"cell_number": 1, "summary": "W"}},
            {"name": "ContinuationContext", "args": {"context": "FTY library"}},
        ]
        summaries, cont = _tool_calls_to_summaries(tool_calls)
        assert cont == "FTY library"

    def test_no_cell_number_skipped(self) -> None:
        tool_calls = [
            {"name": "ReportWhat", "args": {"summary": "no cell num"}},
        ]
        summaries, cont = _tool_calls_to_summaries(tool_calls)
        assert len(summaries) == 0

    def test_empty_tool_calls(self) -> None:
        summaries, cont = _tool_calls_to_summaries([])
        assert len(summaries) == 0
        assert cont == ""

    def test_multi_why_creates_new_summary(self) -> None:
        """A second ReportWhy for the same cell also starts a new summary."""
        tool_calls = [
            {"name": "ReportWhat", "args": {"cell_number": 1, "summary": "W"}},
            {"name": "ReportWhy", "args": {"cell_number": 1, "summary": "Y1"}},
            {"name": "ReportWhy", "args": {"cell_number": 1, "summary": "Y2"}},
        ]
        summaries, cont = _tool_calls_to_summaries(tool_calls)
        assert len(summaries[1]) == 2
        assert summaries[1][0].why == "Y1"
        assert summaries[1][1].why == "Y2"


# ── _cached_tool_call ─────────────────────────────────────────────────

class TestCachedToolCall:
    """Test _cached_tool_call with mocked LLM."""

    @pytest.fixture
    def sem(self):
        return asyncio.Semaphore(1)

    @pytest.mark.asyncio
    async def test_single_shot_returns_tool_calls(self, sem) -> None:
        """Single-shot mode (tool_response_fn=None) returns tool calls."""
        from summarize_kg import _cached_tool_call

        mock_response = MagicMock()
        mock_response.tool_calls = [
            {"name": "ReportWhat", "args": {"cell_number": 1, "summary": "X"}, "id": "tc1"},
        ]
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = mock_response

        tool_calls, was_cached = await _cached_tool_call(
            "test prompt", mock_llm, "test-model", None, sem,
        )

        assert len(tool_calls) == 1
        assert tool_calls[0]["name"] == "ReportWhat"
        assert was_cached is False
        mock_llm.ainvoke.assert_called_once()

    @pytest.mark.asyncio
    async def test_multi_turn_accumulates_calls(self, sem) -> None:
        """Multi-turn mode accumulates tool calls across turns."""
        from summarize_kg import _cached_tool_call

        # Turn 1: 2 tool calls
        resp1 = MagicMock()
        resp1.tool_calls = [
            {"name": "ReportWhat", "args": {"cell_number": 1, "summary": "W1"}, "id": "t1"},
            {"name": "ReportWhy", "args": {"cell_number": 1, "summary": "Y1"}, "id": "t2"},
        ]
        # Turn 2: 1 tool call
        resp2 = MagicMock()
        resp2.tool_calls = [
            {"name": "ReportWhat", "args": {"cell_number": 2, "summary": "W2"}, "id": "t3"},
        ]
        # Turn 3: no tool calls → stop
        resp3 = MagicMock()
        resp3.tool_calls = []

        mock_llm = AsyncMock()
        mock_llm.ainvoke.side_effect = [resp1, resp2, resp3]

        progress_fn = lambda tcs: f"{len(tcs)} calls so far"

        tool_calls, was_cached = await _cached_tool_call(
            "test prompt", mock_llm, "test-model", None, sem,
            tool_response_fn=progress_fn,
        )

        assert len(tool_calls) == 3
        assert tool_calls[0]["name"] == "ReportWhat"
        assert tool_calls[2]["name"] == "ReportWhat"
        assert mock_llm.ainvoke.call_count == 3

    @pytest.mark.asyncio
    async def test_multi_turn_respects_max_turns(self, sem) -> None:
        """Multi-turn stops at max_turns even if model keeps calling tools."""
        from summarize_kg import _cached_tool_call

        # Every response has a tool call — should stop at max_turns
        def make_resp():
            r = MagicMock()
            r.tool_calls = [
                {"name": "ReportWhat", "args": {"cell_number": 1, "summary": "W"}, "id": "t"},
            ]
            return r

        mock_llm = AsyncMock()
        mock_llm.ainvoke.side_effect = [make_resp() for _ in range(20)]

        progress_fn = lambda tcs: "progress"

        tool_calls, _ = await _cached_tool_call(
            "test prompt", mock_llm, "test-model", None, sem,
            tool_response_fn=progress_fn,
            max_turns=3,
        )

        # Should have 3 tool calls (1 per turn, 3 turns)
        assert len(tool_calls) == 3
        assert mock_llm.ainvoke.call_count == 3

    @pytest.mark.asyncio
    async def test_cache_hit(self, sem) -> None:
        """Cached results are returned without invoking the LLM."""
        from summarize_kg import _cached_tool_call

        cached_data = [{"name": "ReportWhat", "args": {"cell_number": 1, "summary": "cached"}}]
        mock_cache = MagicMock()
        mock_cache.get.return_value = json.dumps(cached_data)

        mock_llm = AsyncMock()

        tool_calls, was_cached = await _cached_tool_call(
            "test prompt", mock_llm, "test-model", mock_cache, sem,
        )

        assert was_cached is True
        assert tool_calls == cached_data
        mock_llm.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_miss_stores_result(self, sem) -> None:
        """Cache miss invokes LLM and stores the result."""
        from summarize_kg import _cached_tool_call

        mock_cache = MagicMock()
        mock_cache.get.return_value = None

        mock_response = MagicMock()
        mock_response.tool_calls = [
            {"name": "ReportWhat", "args": {"cell_number": 1, "summary": "W"}, "id": "tc1"},
        ]
        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = mock_response

        tool_calls, was_cached = await _cached_tool_call(
            "test prompt", mock_llm, "test-model", mock_cache, sem,
        )

        assert was_cached is False
        mock_cache.put.assert_called_once()
        stored = json.loads(mock_cache.put.call_args[0][1])
        assert stored[0]["name"] == "ReportWhat"

    @pytest.mark.asyncio
    async def test_no_tool_calls_returns_empty(self, sem) -> None:
        """If model returns no tool calls, result is empty list."""
        from summarize_kg import _cached_tool_call

        mock_response = MagicMock()
        mock_response.tool_calls = []

        mock_llm = AsyncMock()
        mock_llm.ainvoke.return_value = mock_response

        tool_calls, was_cached = await _cached_tool_call(
            "prompt", mock_llm, "model", None, sem,
        )

        assert tool_calls == []
        assert was_cached is False


# ── _make_progress_fn (integration-style) ─────────────────────────────

class TestProgressFunction:
    """Test the progress function logic that would be used in summarize_cells."""

    def test_progress_computation(self) -> None:
        """Simulate the progress function closure behavior."""
        batch_cells = [
            _cell(0, "code", code_text="(defun f ())",
                  symbol_names=["ACL2::F"], symbol_kinds=["function"]),
            _cell(1, "code", code_text="(+ 1 2)"),
            _cell(2, "markdown", comment_text="; note"),
            _cell(3, "code", code_text="(defthm t1)",
                  symbol_names=["ACL2::T1"], symbol_kinds=["theorem"]),
        ]
        batch_indices = {c.cell_index for c in batch_cells}

        # Replicate the _make_progress_fn logic
        b_sym = sum(1 for c in batch_cells if c.symbol_names)
        b_code = sum(
            1 for c in batch_cells
            if c.cell_type == "code" and not c.symbol_names
        )
        b_cmt = sum(
            1 for c in batch_cells if c.cell_type == "markdown"
        )

        assert b_sym == 2  # cells 0, 3
        assert b_code == 1  # cell 1
        assert b_cmt == 1  # cell 2

        # Simulate after covering cells 0 and 2
        tool_calls = [
            {"name": "ReportWhat", "args": {"cell_number": 0, "summary": "W"}},
            {"name": "ReportWhat", "args": {"cell_number": 2, "summary": "C"}},
        ]

        covered = set()
        for tc in tool_calls:
            cn = tc.get("args", {}).get("cell_number")
            if cn is not None and cn in batch_indices:
                covered.add(cn)

        assert covered == {0, 2}

        d_sym = sum(
            1 for c in batch_cells
            if c.symbol_names and c.cell_index in covered
        )
        d_code = sum(
            1 for c in batch_cells
            if c.cell_type == "code" and not c.symbol_names
            and c.cell_index in covered
        )
        d_cmt = sum(
            1 for c in batch_cells
            if c.cell_type == "markdown"
            and c.cell_index in covered
        )

        assert d_sym == 1   # cell 0 covered
        assert d_code == 0   # cell 1 not covered
        assert d_cmt == 1    # cell 2 covered

        progress = (
            f"Recorded. "
            f"{len(covered)}/{len(batch_indices)} cells in this batch covered. "
            f"Breakdown: {d_sym}/{b_sym} symbol, "
            f"{d_code}/{b_code} code, {d_cmt}/{b_cmt} comment cells. "
            f"Continue with remaining cells."
        )
        assert "2/4 cells in this batch covered" in progress
        assert "1/2 symbol" in progress
        assert "0/1 code" in progress
        assert "1/1 comment" in progress
