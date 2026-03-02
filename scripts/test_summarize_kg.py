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
    LLMCache,
    SUMMARY_VERSIONS,
    PROMPTS_DIR,
    CellIdea,
    CellBatchResponse,
    NotebookSummaryResponse,
    _strip_markdown_fences,
    _build_batch_cells_text,
    _batch_cells_by_size,
    _summary_tools_to_result,
    _tool_calls_to_summaries,
    _json_response_to_summaries,
    _json_response_to_result,
    _load_prompt_templates,
    _render_prompt,
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

        progress_fn = lambda all_tcs, turn_tcs: [
            f"{len(all_tcs)} calls so far" for _ in turn_tcs
        ]

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

        # Every response has a UNIQUE tool call — should stop at max_turns
        counter = iter(range(20))
        def make_resp():
            n = next(counter)
            r = MagicMock()
            r.tool_calls = [
                {"name": "ReportWhat", "args": {"cell_number": n, "summary": f"W{n}"}, "id": f"t{n}"},
            ]
            return r

        mock_llm = AsyncMock()
        mock_llm.ainvoke.side_effect = [make_resp() for _ in range(20)]

        progress_fn = lambda all_tcs, turn_tcs: [
            "progress" for _ in turn_tcs
        ]

        tool_calls, _ = await _cached_tool_call(
            "test prompt", mock_llm, "test-model", None, sem,
            tool_response_fn=progress_fn,
            max_turns=3,
        )

        # Should have 3 tool calls (1 per turn, 3 turns)
        assert len(tool_calls) == 3
        assert mock_llm.ainvoke.call_count == 3

    @pytest.mark.asyncio
    async def test_multi_turn_stall_detection(self, sem) -> None:
        """Multi-turn breaks after 2 consecutive duplicate-only turns."""
        from summarize_kg import _cached_tool_call

        # All responses return the exact same tool call — stall detection
        def make_resp():
            r = MagicMock()
            r.tool_calls = [
                {"name": "ReportWhat", "args": {"cell_number": 1, "summary": "W"}, "id": "t"},
            ]
            return r

        mock_llm = AsyncMock()
        mock_llm.ainvoke.side_effect = [make_resp() for _ in range(20)]

        progress_fn = lambda all_tcs, turn_tcs: [
            "progress" for _ in turn_tcs
        ]

        tool_calls, _ = await _cached_tool_call(
            "test prompt", mock_llm, "test-model", None, sem,
            tool_response_fn=progress_fn,
            max_turns=10,
        )

        # Turn 1: new call (cell 1). Turn 2: dup (stall=1). Turn 3: dup (stall=2 → break).
        # So we get calls from turns 1 and 2 = 2 total, and 3 invocations.
        assert len(tool_calls) == 2
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

    @staticmethod
    def _build_progress_fn(batch_cells):
        """Build a progress fn that mirrors the production closure."""
        batch_indices = {c.cell_index for c in batch_cells}
        _min_idx = min(batch_indices)
        _max_idx = max(batch_indices)
        b_sym = sum(1 for c in batch_cells if c.symbol_names)
        b_code = sum(
            1 for c in batch_cells
            if c.cell_type == "code" and not c.symbol_names
        )
        b_cmt = sum(
            1 for c in batch_cells if c.cell_type == "markdown"
        )
        _seen: set = set()

        def fn(all_tcs, turn_tcs):
            responses = []
            for tc in turn_tcs:
                name = tc.get("name", "")
                args = tc.get("args", {})
                cn = args.get("cell_number")
                text = args.get("summary", "")
                if cn is not None and cn not in batch_indices:
                    responses.append(
                        f"ERROR: cell_number {cn} is out of range. "
                        f"Valid cell numbers in this batch are "
                        f"{_min_idx}\u2013{_max_idx}. "
                        f"Only use cell numbers that appear in the "
                        f"cells provided."
                    )
                elif name in ("ReportWhat", "ReportWhy",
                              "ReportHow") and text:
                    key = (cn, name, text)
                    if key in _seen:
                        responses.append(
                            f"Duplicate: cell {cn} {name} "
                            f"already recorded with the same text. "
                            f"Do not repeat summaries."
                        )
                    else:
                        _seen.add(key)
                        responses.append(f"Recorded cell {cn}.")
                else:
                    responses.append(
                        f"Recorded cell {cn}." if cn is not None
                        else "Recorded."
                    )
            covered = set()
            for tc in all_tcs:
                cn = tc.get("args", {}).get("cell_number")
                if cn is not None and cn in batch_indices:
                    covered.add(cn)
            d_sym = sum(1 for c in batch_cells
                        if c.symbol_names and c.cell_index in covered)
            d_code = sum(1 for c in batch_cells
                         if c.cell_type == "code" and not c.symbol_names
                         and c.cell_index in covered)
            d_cmt = sum(1 for c in batch_cells
                        if c.cell_type == "markdown"
                        and c.cell_index in covered)
            progress = (
                f"{len(covered)}/{len(batch_indices)} cells "
                f"(range {_min_idx}\u2013{_max_idx}) covered. "
                f"Breakdown: {d_sym}/{b_sym} symbol, "
                f"{d_code}/{b_code} code, {d_cmt}/{b_cmt} comment. "
                f"Continue with remaining cells."
            )
            if responses:
                responses[-1] += f" {progress}"
            return responses
        return fn

    def test_progress_computation(self) -> None:
        """Valid calls produce 'Recorded cell N.' with progress."""
        batch_cells = [
            _cell(0, "code", code_text="(defun f ())",
                  symbol_names=["ACL2::F"], symbol_kinds=["function"]),
            _cell(1, "code", code_text="(+ 1 2)"),
            _cell(2, "markdown", comment_text="; note"),
            _cell(3, "code", code_text="(defthm t1)",
                  symbol_names=["ACL2::T1"], symbol_kinds=["theorem"]),
        ]
        fn = self._build_progress_fn(batch_cells)

        turn1 = [
            {"name": "ReportWhat", "args": {"cell_number": 0, "summary": "W"}},
            {"name": "ReportWhat", "args": {"cell_number": 2, "summary": "C"}},
        ]
        r1 = fn(turn1, turn1)
        assert len(r1) == 2
        assert r1[0] == "Recorded cell 0."
        assert "2/4 cells" in r1[1]
        assert "range 0\u20133" in r1[1]
        assert "1/2 symbol" in r1[1]
        assert "0/1 code" in r1[1]
        assert "1/1 comment" in r1[1]

    def test_out_of_range_reported(self) -> None:
        """Out-of-range cell numbers produce ERROR responses."""
        batch_cells = [
            _cell(5, "code", code_text="(defun g ())",
                  symbol_names=["ACL2::G"], symbol_kinds=["function"]),
            _cell(6, "code", code_text="(+ 1 2)"),
        ]
        fn = self._build_progress_fn(batch_cells)

        turn = [
            {"name": "ReportWhat", "args": {"cell_number": 5, "summary": "ok"}},
            {"name": "ReportWhat", "args": {"cell_number": 99, "summary": "bad"}},
            {"name": "ReportWhat", "args": {"cell_number": 6, "summary": "ok2"}},
        ]
        r = fn(turn, turn)
        assert "Recorded cell 5." == r[0]
        assert "ERROR" in r[1]
        assert "99" in r[1]
        assert r[2].startswith("Recorded cell 6.")

    def test_duplicate_detected(self) -> None:
        """Exact-duplicate summaries get a 'Duplicate' response."""
        batch_cells = [
            _cell(0, "code", code_text="(defun f ())"),
            _cell(1, "code", code_text="(defun g ())"),
        ]
        fn = self._build_progress_fn(batch_cells)

        # Turn 1: original call
        turn1 = [
            {"name": "ReportWhat", "args": {"cell_number": 0, "summary": "X"}},
        ]
        r1 = fn(turn1, turn1)
        assert "Recorded cell 0." in r1[0]

        # Turn 2: exact same call again
        all_tcs = turn1 + turn1
        r2 = fn(all_tcs, turn1)
        assert "Duplicate" in r2[0]
        assert "cell 0" in r2[0]

    def test_different_text_not_duplicate(self) -> None:
        """Same cell + tool but different text is NOT a duplicate."""
        batch_cells = [
            _cell(0, "code", code_text="(defun f ())"),
        ]
        fn = self._build_progress_fn(batch_cells)

        turn1 = [
            {"name": "ReportWhat", "args": {"cell_number": 0, "summary": "idea A"}},
        ]
        fn(turn1, turn1)

        turn2 = [
            {"name": "ReportWhat", "args": {"cell_number": 0, "summary": "idea B"}},
        ]
        r2 = fn(turn1 + turn2, turn2)
        assert "Recorded cell 0." in r2[0]


# ── Hallucinated cell index filtering ─────────────────────────────────


class TestHallucinatedCellFiltering:
    """Verify that _tool_calls_to_summaries output is correctly
    filtered when the LLM invents cell indices outside the batch."""

    def test_valid_indices_kept(self):
        """Summaries for valid cell indices survive filtering."""
        tcs = [
            {"name": "ReportWhat", "args": {"cell_number": 0, "summary": "a"}},
            {"name": "ReportWhat", "args": {"cell_number": 3, "summary": "b"}},
        ]
        summaries, _ = _tool_calls_to_summaries(tcs)
        batch_indices = {0, 1, 2, 3}
        bad = set(summaries.keys()) - batch_indices
        assert bad == set()
        assert set(summaries.keys()) == {0, 3}

    def test_invalid_indices_detected(self):
        """Cell indices not in the batch are caught."""
        tcs = [
            {"name": "ReportWhat", "args": {"cell_number": 0, "summary": "ok"}},
            {"name": "ReportWhat", "args": {"cell_number": 20, "summary": "bad"}},
            {"name": "ReportWhat", "args": {"cell_number": 75, "summary": "bad2"}},
        ]
        summaries, _ = _tool_calls_to_summaries(tcs)
        batch_indices = {0, 1, 2, 3, 4}
        bad = set(summaries.keys()) - batch_indices
        assert bad == {20, 75}
        # After filtering:
        for bi in bad:
            del summaries[bi]
        assert set(summaries.keys()) == {0}

    def test_all_invalid_produces_empty(self):
        """When ALL cell indices are hallucinated, result is empty."""
        tcs = [
            {"name": "ReportWhat", "args": {"cell_number": 99, "summary": "x"}},
        ]
        summaries, _ = _tool_calls_to_summaries(tcs)
        batch_indices = {0, 1, 2}
        bad = set(summaries.keys()) - batch_indices
        for bi in bad:
            del summaries[bi]
        assert summaries == {}


# ── Duplicate filtering in _tool_calls_to_summaries ───────────────────


class TestDuplicateFiltering:
    """Verify that _tool_calls_to_summaries drops exact-duplicate calls."""

    def test_exact_duplicate_dropped(self):
        """Identical (cell, name, text) is kept only once."""
        tcs = [
            {"name": "ReportWhat", "args": {"cell_number": 0, "summary": "X"}},
            {"name": "ReportWhat", "args": {"cell_number": 0, "summary": "X"}},
        ]
        summaries, _ = _tool_calls_to_summaries(tcs)
        assert len(summaries[0]) == 1
        assert summaries[0][0].what == "X"

    def test_different_text_kept(self):
        """Same cell + tool but different text → two SummaryResults."""
        tcs = [
            {"name": "ReportWhat", "args": {"cell_number": 0, "summary": "A"}},
            {"name": "ReportWhat", "args": {"cell_number": 0, "summary": "B"}},
        ]
        summaries, _ = _tool_calls_to_summaries(tcs)
        assert len(summaries[0]) == 2
        assert summaries[0][0].what == "A"
        assert summaries[0][1].what == "B"

    def test_duplicate_why_dropped(self):
        """Duplicate ReportWhy is dropped."""
        tcs = [
            {"name": "ReportWhat", "args": {"cell_number": 1, "summary": "W"}},
            {"name": "ReportWhy", "args": {"cell_number": 1, "summary": "Y"}},
            {"name": "ReportWhy", "args": {"cell_number": 1, "summary": "Y"}},
        ]
        summaries, _ = _tool_calls_to_summaries(tcs)
        assert len(summaries[1]) == 1
        assert summaries[1][0].what == "W"
        assert summaries[1][0].why == "Y"

    def test_triple_duplicate_produces_one(self):
        """Three identical calls → one SummaryResult."""
        tcs = [
            {"name": "ReportWhat", "args": {"cell_number": 2, "summary": "Z"}},
            {"name": "ReportWhat", "args": {"cell_number": 2, "summary": "Z"}},
            {"name": "ReportWhat", "args": {"cell_number": 2, "summary": "Z"}},
        ]
        summaries, _ = _tool_calls_to_summaries(tcs)
        assert len(summaries[2]) == 1


# ── Symbol extraction in _tool_calls_to_summaries ────────────────────


class TestSymbolExtraction:
    """Verify that the `symbol` field is extracted from Report tool args."""

    def test_symbol_from_report_what(self):
        tcs = [
            {"name": "ReportWhat", "args": {"cell_number": 0, "summary": "W", "symbol": "ACL2::FOO"}},
        ]
        summaries, _ = _tool_calls_to_summaries(tcs)
        assert summaries[0][0].symbol == "ACL2::FOO"

    def test_no_symbol_defaults_empty(self):
        tcs = [
            {"name": "ReportWhat", "args": {"cell_number": 0, "summary": "W"}},
        ]
        summaries, _ = _tool_calls_to_summaries(tcs)
        assert summaries[0][0].symbol == ""

    def test_symbol_from_why_fills_missing(self):
        """ReportWhy sets symbol when ReportWhat didn't provide one."""
        tcs = [
            {"name": "ReportWhat", "args": {"cell_number": 0, "summary": "W"}},
            {"name": "ReportWhy", "args": {"cell_number": 0, "summary": "Y", "symbol": "ACL2::BAR"}},
        ]
        summaries, _ = _tool_calls_to_summaries(tcs)
        assert summaries[0][0].symbol == "ACL2::BAR"

    def test_symbol_from_what_not_overridden_by_why(self):
        """ReportWhy does NOT override symbol already set by ReportWhat."""
        tcs = [
            {"name": "ReportWhat", "args": {"cell_number": 0, "summary": "W", "symbol": "ACL2::FIRST"}},
            {"name": "ReportWhy", "args": {"cell_number": 0, "summary": "Y", "symbol": "ACL2::SECOND"}},
        ]
        summaries, _ = _tool_calls_to_summaries(tcs)
        assert summaries[0][0].symbol == "ACL2::FIRST"

    def test_multi_summary_symbols(self):
        """Each ReportWhat starts a new summary with its own symbol."""
        tcs = [
            {"name": "ReportWhat", "args": {"cell_number": 0, "summary": "W1", "symbol": "ACL2::A"}},
            {"name": "ReportWhat", "args": {"cell_number": 0, "summary": "W2", "symbol": "ACL2::B"}},
        ]
        summaries, _ = _tool_calls_to_summaries(tcs)
        assert len(summaries[0]) == 2
        assert summaries[0][0].symbol == "ACL2::A"
        assert summaries[0][1].symbol == "ACL2::B"


# ── LLMCache model-aware hashing ────────────────────────────────────


class TestLLMCacheModelAware:
    """Verify that LLMCache hashes include the model string."""

    @pytest.fixture
    def cache(self, tmp_path):
        c = LLMCache(str(tmp_path / "test_cache.db"))
        yield c
        c.close()

    def test_same_prompt_different_model_no_collision(self, cache):
        """Same prompt text with different models should not collide."""
        cache.put("hello world", "response_a", "model-a")
        cache.put("hello world", "response_b", "model-b")

        assert cache.get("hello world", "model-a") == "response_a"
        assert cache.get("hello world", "model-b") == "response_b"

    def test_default_model_empty_string(self, cache):
        """Default model='' works for backward compat."""
        cache.put("prompt", "result", "")
        assert cache.get("prompt", "") == "result"
        # Different model should not collide
        assert cache.get("prompt", "some-model") is None

    def test_cache_miss_returns_none(self, cache):
        assert cache.get("nonexistent", "model") is None

    def test_put_overwrites(self, cache):
        cache.put("p", "old", "m")
        cache.put("p", "new", "m")
        assert cache.get("p", "m") == "new"

    def test_count(self, cache):
        assert cache.count() == 0
        cache.put("a", "1", "m")
        cache.put("b", "2", "m")
        assert cache.count() == 2


# ── Portcullis filtering ────────────────────────────────────────────


class TestPortcullisFiltering:
    """Verify that portcullis cells are excluded from summarization."""

    def test_portcullis_cell_record(self):
        """CellRecord with is_portcullis=True should be filterable."""
        normal = CellRecord(
            notebook_source="test.lisp", cell_index=0, cell_type="code",
            code_text="(defun f (x) x)", comment_text="", package="ACL2",
            is_portcullis=False, symbol_names=[], symbol_kinds=[],
        )
        portcullis = CellRecord(
            notebook_source="test.lisp", cell_index=1, cell_type="code",
            code_text="(set-in-theory ...)", comment_text="", package="ACL2",
            is_portcullis=True, symbol_names=[], symbol_kinds=[],
        )
        cells = [normal, portcullis]
        filtered = [c for c in cells if not c.is_portcullis]
        assert len(filtered) == 1
        assert filtered[0].cell_index == 0


# ── SUMMARY_VERSIONS dict integrity ─────────────────────────────────


class TestSummaryVersions:
    """Ensure SUMMARY_VERSIONS is well-formed."""

    def test_has_at_least_one_version(self):
        assert len(SUMMARY_VERSIONS) >= 1

    def test_all_entries_have_required_keys(self):
        for label, entry in SUMMARY_VERSIONS.items():
            assert isinstance(label, str), f"Version label must be str, got {type(label)}"
            assert "model" in entry, f"Version '{label}' missing 'model'"
            assert "prompts" in entry, f"Version '{label}' missing 'prompts'"
            assert "description" in entry, f"Version '{label}' missing 'description'"
            assert "mode" in entry, f"Version '{label}' missing 'mode'"

    def test_prompt_dirs_exist(self):
        """Each version's prompts directory should exist under PROMPTS_DIR."""
        for label, entry in SUMMARY_VERSIONS.items():
            prompt_dir = PROMPTS_DIR / entry["prompts"]
            assert prompt_dir.is_dir(), (
                f"Version '{label}' references prompts dir "
                f"'{entry['prompts']}' but {prompt_dir} does not exist"
            )


# ── Jinja template loading ──────────────────────────────────────────


class TestJinjaTemplates:
    """Test _load_prompt_templates and _render_prompt."""

    def test_load_templates_returns_environment(self):
        """_load_prompt_templates returns a Jinja2 Environment or None."""
        env = _load_prompt_templates("v1")
        if env is not None:
            import jinja2
            assert isinstance(env, jinja2.Environment)

    def test_load_templates_nonexistent_raises(self):
        """Non-existent version label raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            _load_prompt_templates("nonexistent_version_xyz_999")

    def test_render_prompt_with_env(self):
        """_render_prompt uses Jinja template when env is available."""
        env = _load_prompt_templates("v1")
        if env is None:
            pytest.skip("v1 templates not found")
        result = _render_prompt(
            env, "cell_batch.j2", "fallback {source_file}",
            source_file="test/file.lisp",
            topic_section="",
            continuation_section="",
            cells_text="(defun f (x) x)",
        )
        assert "test/file.lisp" in result
        assert "(defun f (x) x)" in result

    def test_render_prompt_fallback(self):
        """_render_prompt uses fallback format string when env is None."""
        result = _render_prompt(
            None, "cell_batch.j2", "File: {source_file}, Cells: {cells_text}",
            source_file="test.lisp",
            cells_text="code here",
        )
        assert result == "File: test.lisp, Cells: code here"

    def test_all_v1_templates_exist(self):
        """All expected template files exist in v1."""
        expected_templates = [
            "cell_batch.j2", "notebook_chunk.j2",
            "notebook_reduce.j2", "directory.j2",
        ]
        env = _load_prompt_templates("v1")
        if env is None:
            pytest.skip("v1 templates not found")
        for name in expected_templates:
            # Should not raise TemplateNotFound
            tmpl = env.get_template(name)
            assert tmpl is not None

    def test_all_v3_templates_exist(self):
        """All expected template files exist in v3."""
        expected_templates = [
            "cell_batch.j2", "notebook_chunk.j2",
            "notebook_reduce.j2", "directory.j2",
        ]
        env = _load_prompt_templates("v3")
        if env is None:
            pytest.skip("v3 templates not found")
        for name in expected_templates:
            tmpl = env.get_template(name)
            assert tmpl is not None

    def test_v3_cell_batch_no_tool_references(self):
        """v3 cell_batch template should not reference tool calling."""
        env = _load_prompt_templates("v3")
        if env is None:
            pytest.skip("v3 templates not found")
        tmpl = env.get_template("cell_batch.j2")
        rendered = tmpl.render(
            source_file="test.lisp",
            topic_section="",
            continuation_section="",
            cells_text="(defun f (x) x)",
        )
        # v3 templates should NOT mention tool calling.
        assert "Call report_what" not in rendered
        assert "Call report_why" not in rendered
        assert "Call each tool" not in rendered


# ── JSON response converters ────────────────────────────────────────


class TestJsonResponseToSummaries:
    """Test _json_response_to_summaries (CellBatchResponse → per-cell results)."""

    def test_basic_conversion(self):
        """Single idea per cell converts correctly."""
        response = CellBatchResponse(
            ideas=[
                CellIdea(cell_number=0, what="Defines foo", why="Needed for bar"),
                CellIdea(cell_number=1, what="Proves thm-x", how="By induction"),
            ],
            continuation="Context for next batch",
        )
        summaries, cont = _json_response_to_summaries(response)
        assert cont == "Context for next batch"
        assert 0 in summaries
        assert 1 in summaries
        assert len(summaries[0]) == 1
        assert summaries[0][0].what == "Defines foo"
        assert summaries[0][0].why == "Needed for bar"
        assert summaries[1][0].how == "By induction"

    def test_multiple_ideas_per_cell(self):
        """Multiple ideas for the same cell index are collected."""
        response = CellBatchResponse(ideas=[
            CellIdea(cell_number=5, what="First idea"),
            CellIdea(cell_number=5, what="Second idea", symbol="MY-FN"),
        ])
        summaries, cont = _json_response_to_summaries(response)
        assert cont == ""
        assert len(summaries[5]) == 2
        assert summaries[5][0].what == "First idea"
        assert summaries[5][1].what == "Second idea"
        assert summaries[5][1].symbol == "MY-FN"

    def test_empty_ideas(self):
        """Empty ideas list gives empty summaries."""
        response = CellBatchResponse(ideas=[])
        summaries, cont = _json_response_to_summaries(response)
        assert summaries == {}
        assert cont == ""

    def test_dict_input(self):
        """Accepts a raw dict (from cache) as input."""
        data = {
            "ideas": [
                {"cell_number": 3, "what": "Something", "why": "", "how": "", "symbol": ""},
            ],
            "continuation": "",
        }
        summaries, cont = _json_response_to_summaries(data)
        assert 3 in summaries
        assert summaries[3][0].what == "Something"

    def test_symbol_preserved(self):
        """Symbol field is passed through to SummaryResult."""
        response = CellBatchResponse(ideas=[
            CellIdea(cell_number=0, what="Defines X", symbol="ACL2::X"),
        ])
        summaries, _ = _json_response_to_summaries(response)
        assert summaries[0][0].symbol == "ACL2::X"


class TestJsonResponseToResult:
    """Test _json_response_to_result (NotebookSummaryResponse → SummaryResult)."""

    def test_basic_conversion(self):
        """All fields convert correctly."""
        response = NotebookSummaryResponse(
            what="Defines utility macros",
            why="Simplifies common patterns",
            how="Include via books/utils.lisp",
        )
        result = _json_response_to_result(response)
        assert isinstance(result, SummaryResult)
        assert result.what == "Defines utility macros"
        assert result.why == "Simplifies common patterns"
        assert result.how == "Include via books/utils.lisp"

    def test_optional_fields_default(self):
        """Why and how default to empty string."""
        response = NotebookSummaryResponse(what="Only what provided")
        result = _json_response_to_result(response)
        assert result.what == "Only what provided"
        assert result.why == ""
        assert result.how == ""

    def test_dict_input(self):
        """Accepts a raw dict (from cache) as input."""
        data = {"what": "From dict", "why": "Because", "how": "Like this"}
        result = _json_response_to_result(data)
        assert result.what == "From dict"
        assert result.why == "Because"
        assert result.how == "Like this"


class TestSummaryVersionsMode:
    """Test that SUMMARY_VERSIONS mode configuration is well-formed."""

    def test_all_versions_have_mode(self):
        """Every version entry has a 'mode' key with a valid value."""
        valid_modes = {"tools", "json_schema"}
        for label, entry in SUMMARY_VERSIONS.items():
            assert "mode" in entry, f"Version '{label}' missing 'mode' key"
            assert entry["mode"] in valid_modes, (
                f"Version '{label}' has invalid mode '{entry['mode']}'"
            )

    def test_v1_uses_tools(self):
        assert SUMMARY_VERSIONS["v1-qwen3-coder"]["mode"] == "tools"

    def test_v2_uses_json_schema(self):
        assert SUMMARY_VERSIONS["v2-groq-gpt-oss"]["mode"] == "json_schema"


class TestPydanticResponseModels:
    """Test that Pydantic response models validate correctly."""

    def test_cell_idea_minimal(self):
        idea = CellIdea(cell_number=0, what="Test")
        assert idea.cell_number == 0
        assert idea.what == "Test"
        assert idea.why == ""
        assert idea.how == ""
        assert idea.symbol == ""

    def test_cell_idea_full(self):
        idea = CellIdea(
            cell_number=5, what="What", why="Why",
            how="How", symbol="SYM",
        )
        assert idea.symbol == "SYM"

    def test_cell_batch_response_round_trip(self):
        """Model serializes to JSON and back."""
        resp = CellBatchResponse(
            ideas=[CellIdea(cell_number=0, what="Test")],
            continuation="ctx",
        )
        data = json.loads(resp.model_dump_json())
        restored = CellBatchResponse(**data)
        assert len(restored.ideas) == 1
        assert restored.continuation == "ctx"

    def test_notebook_summary_response_round_trip(self):
        resp = NotebookSummaryResponse(
            what="W", why="Y", how="H",
        )
        data = json.loads(resp.model_dump_json())
        restored = NotebookSummaryResponse(**data)
        assert restored.what == "W"
        assert restored.why == "Y"
        assert restored.how == "H"
