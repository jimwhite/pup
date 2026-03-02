#!/usr/bin/env python3
"""Integration test: one batch of cells → Groq json_schema → parsed results.

No Weaviate required.  Exercises the full prompt→LLM→parse pipeline.

Usage:
    # Requires GROQ_API_KEY in environment (uses 20b by default)
    python scripts/test_groq_integration.py

    # With a specific version
    python scripts/test_groq_integration.py --version v2-groq-gpt-oss

    # With verbose LLM debug logging
    python scripts/test_groq_integration.py -v
"""

from __future__ import annotations

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
    _salvage_json,
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


# ── Failing batch from basetypes.lisp (json_validate_failed) ─────────

BASETYPES_CELLS = [
    CellRecord(
        notebook_source="books/centaur/fty/basetypes.lisp",
        cell_index=1,
        cell_type="markdown",
        code_text="",
        comment_text=(
            "; FTY type support library\n"
            "; Copyright (C) 2014 Centaur Technology\n"
            ";\n"
            "; Contact:\n"
            ";   Centaur Technology Formal Verification Group\n"
            ";   7600-C N. Capital of Texas Highway, Suite 300, Austin, TX 78731, USA.\n"
            ";   http://www.centtech.com/\n"
            ";\n"
            "; License: (An MIT/X11-style license)\n"
            ";\n"
            ';   Permission is hereby granted, free of charge, to any person obtaining a\n'
            ';   copy of this software and associated documentation files (the "Software"),\n'
            ";   to deal in the Software without restriction, including without limitation\n"
            ";   the rights to use, copy, modify, merge, publish, distribute, sublicense,\n"
            ";   and/or sell copies of the Software, and to permit persons to whom the\n"
            ";   Software is furnished to do so, subject to the following conditions:\n"
            ";\n"
            ";   The above copyright notice and this permission notice shall be included in\n"
            ";   all copies or substantial portions of the Software.\n"
            ";\n"
            ';   THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\n'
            ";   IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\n"
            ";   FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE\n"
            ";   AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER\n"
            ";   LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING\n"
            ";   FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER\n"
            ";   DEALINGS IN THE SOFTWARE.\n"
            ";\n"
            "; Original author: Sol Swords <sswords@centtech.com>"
        ),
        package="ACL2",
        is_portcullis=False,
    ),
    CellRecord(
        notebook_source="books/centaur/fty/basetypes.lisp",
        cell_index=2,
        cell_type="code",
        code_text='(in-package "ACL2")',
        comment_text="",
        package="ACL2",
        is_portcullis=False,
    ),
    CellRecord(
        notebook_source="books/centaur/fty/basetypes.lisp",
        cell_index=3,
        cell_type="code",
        code_text='(include-book "std/basic/defs" :dir :system)',
        comment_text="",
        package="ACL2",
        is_portcullis=False,
    ),
    CellRecord(
        notebook_source="books/centaur/fty/basetypes.lisp",
        cell_index=4,
        cell_type="code",
        code_text='(include-book "std/basic/pos-fix" :dir :system)',
        comment_text="",
        package="ACL2",
        is_portcullis=False,
    ),
    CellRecord(
        notebook_source="books/centaur/fty/basetypes.lisp",
        cell_index=5,
        cell_type="code",
        code_text='(include-book "std/lists/list-defuns" :dir :system)',
        comment_text="",
        package="ACL2",
        is_portcullis=False,
    ),
    CellRecord(
        notebook_source="books/centaur/fty/basetypes.lisp",
        cell_index=6,
        cell_type="code",
        code_text='(include-book "fixtype")',
        comment_text="",
        package="ACL2",
        is_portcullis=False,
    ),
    CellRecord(
        notebook_source="books/centaur/fty/basetypes.lisp",
        cell_index=7,
        cell_type="code",
        code_text='(local (include-book "std/lists/equiv" :dir :system))',
        comment_text="",
        package="ACL2",
        is_portcullis=False,
    ),
    CellRecord(
        notebook_source="books/centaur/fty/basetypes.lisp",
        cell_index=8,
        cell_type="code",
        code_text="(defconst fty::*defbasetype-keys*\n  '(:name\n    :fix\n    :topic))",
        comment_text="",
        package="ACL2",
        is_portcullis=False,
        symbol_names=["FTY::*DEFBASETYPE-KEYS*"],
        symbol_kinds=["constant"],
    ),
    CellRecord(
        notebook_source="books/centaur/fty/basetypes.lisp",
        cell_index=9,
        cell_type="code",
        code_text=(
            ";; This is just deffixtype with defaults for the names and with :define t.  We\n"
            ";; wouldn't need to take the equiv name as an input, but since we're defining\n"
            ";; it we'd like it to be tags-searchable.\n"
            "(defun fty::defbasetype-fn (equiv pred keys)\n"
            "  (declare (xargs :mode :program))\n"
            "  (b* ((__function__ 'fty::defbasetype-fn)\n"
            "       ((mv kwd-alist args) (std::extract-keywords __function__\n"
            "                                                   fty::*defbasetype-keys*\n"
            "                                                   keys nil))\n"
            "       ((when args) (raise \"Bad args: ~x0\" args))\n"
            "       (pkg (if (equal (symbol-package-name pred) \"COMMON-LISP\")\n"
            "                'acl2::foo\n"
            "              pred))\n"
            "       (typename (or (std::getarg :name nil kwd-alist)\n"
            "                     (b* ((predname (symbol-name pred))\n"
            "                          (len (length predname))\n"
            "                          (p? (char predname (- len 1)))\n"
            "                          ((unless (eql p? #\\P)) pred)\n"
            "                          (dash? (char predname (- len 2)))\n"
            "                          (newlen (- len (if (eql dash? #\\-) 2 1))))\n"
            "                       (intern-in-package-of-symbol\n"
            "                        (subseq predname 0 newlen)\n"
            "                        pkg))))\n"
            "       (fix (or (std::getarg :fix nil kwd-alist)\n"
            "                (intern-in-package-of-symbol\n"
            "                 (concatenate 'string (symbol-name typename) \"-FIX\")\n"
            "                 pkg)))\n"
            "       (topic (std::getarg :topic typename kwd-alist)))\n"
            "    `(fty::deffixtype ,typename :pred ,pred :fix ,fix :equiv ,equiv :define t :topic ,topic)))"
        ),
        comment_text="",
        package="ACL2",
        is_portcullis=False,
        symbol_names=["FTY::DEFBASETYPE-FN"],
        symbol_kinds=["function"],
    ),
    CellRecord(
        notebook_source="books/centaur/fty/basetypes.lisp",
        cell_index=10,
        cell_type="code",
        code_text="(defmacro fty::defbasetype (equiv pred &rest keys)\n  (fty::defbasetype-fn equiv pred keys))",
        comment_text="",
        package="ACL2",
        is_portcullis=False,
        symbol_names=["FTY::DEFBASETYPE"],
        symbol_kinds=["macro"],
    ),
    CellRecord(
        notebook_source="books/centaur/fty/basetypes.lisp",
        cell_index=11,
        cell_type="code",
        code_text="(fty::defbasetype bit-equiv bitp :fix bfix :topic bitp)",
        comment_text="",
        package="ACL2",
        is_portcullis=False,
        symbol_names=["ACL2::BIT-EQUIV"],
        symbol_kinds=["macro"],
    ),
    CellRecord(
        notebook_source="books/centaur/fty/basetypes.lisp",
        cell_index=12,
        cell_type="code",
        code_text="(fty::defbasetype nat-equiv natp :fix nfix :topic natp)",
        comment_text="",
        package="ACL2",
        is_portcullis=False,
        symbol_names=["ACL2::NAT-EQUIV"],
        symbol_kinds=["macro"],
    ),
    CellRecord(
        notebook_source="books/centaur/fty/basetypes.lisp",
        cell_index=13,
        cell_type="code",
        code_text="(fty::defbasetype int-equiv integerp :fix ifix :name int :topic integerp)",
        comment_text="",
        package="ACL2",
        is_portcullis=False,
        symbol_names=["ACL2::INT-EQUIV"],
        symbol_kinds=["macro"],
    ),
    CellRecord(
        notebook_source="books/centaur/fty/basetypes.lisp",
        cell_index=14,
        cell_type="code",
        code_text="(fty::defbasetype rational-equiv rationalp :fix rfix :topic rationalp)",
        comment_text="",
        package="ACL2",
        is_portcullis=False,
        symbol_names=["ACL2::RATIONAL-EQUIV"],
        symbol_kinds=["macro"],
    ),
    CellRecord(
        notebook_source="books/centaur/fty/basetypes.lisp",
        cell_index=15,
        cell_type="code",
        code_text="(fty::defbasetype number-equiv acl2-numberp :fix fix :topic acl2-numberp)",
        comment_text="",
        package="ACL2",
        is_portcullis=False,
        symbol_names=["ACL2::NUMBER-EQUIV"],
        symbol_kinds=["macro"],
    ),
    CellRecord(
        notebook_source="books/centaur/fty/basetypes.lisp",
        cell_index=16,
        cell_type="code",
        code_text="(fty::deffixtype true-list\n  :pred true-listp\n  :fix list-fix\n  :equiv list-equiv\n  :topic true-listp)",
        comment_text="",
        package="ACL2",
        is_portcullis=False,
    ),
    CellRecord(
        notebook_source="books/centaur/fty/basetypes.lisp",
        cell_index=17,
        cell_type="code",
        code_text="(local (in-theory (enable streqv)))",
        comment_text="",
        package="ACL2",
        is_portcullis=False,
    ),
    CellRecord(
        notebook_source="books/centaur/fty/basetypes.lisp",
        cell_index=18,
        cell_type="code",
        code_text="(fty::deffixtype string\n  :pred stringp\n  :fix str-fix\n  :equiv streqv\n  :topic stringp)",
        comment_text="",
        package="ACL2",
        is_portcullis=False,
    ),
    CellRecord(
        notebook_source="books/centaur/fty/basetypes.lisp",
        cell_index=19,
        cell_type="code",
        code_text=(
            '(defsection true-p\n'
            '  :parents (fty::basetypes)\n'
            '  :short "@(call true-p) recognizes only the symbol @(\'t\')."\n'
            '\n'
            '  (defun true-p (x)\n'
            '    (declare (xargs :guard t))\n'
            '    (eq x t)))'
        ),
        comment_text="",
        package="ACL2",
        is_portcullis=False,
        symbol_names=["ACL2::TRUE-P"],
        symbol_kinds=["function"],
    ),
    CellRecord(
        notebook_source="books/centaur/fty/basetypes.lisp",
        cell_index=20,
        cell_type="code",
        code_text=(
            '(defsection true-fix\n'
            '  :parents (fty::basetypes)\n'
            '  :short "@(call true-fix) ignores its argument and unconditionally returns @(\'t\')."\n'
            '\n'
            '  (defun true-fix (x)\n'
            '    (declare (xargs :guard t)\n'
            '             (ignore x))\n'
            '    t))'
        ),
        comment_text="",
        package="ACL2",
        is_portcullis=False,
        symbol_names=["ACL2::TRUE-FIX"],
        symbol_kinds=["function"],
    ),
    CellRecord(
        notebook_source="books/centaur/fty/basetypes.lisp",
        cell_index=21,
        cell_type="code",
        code_text=(
            '(defsection true-equiv\n'
            '  :parents (fty::basetypes)\n'
            '  :short "@(call true-equiv) is a ``degenerate\'\' equivalence for @(see true-p) objects."\n'
            '  :long "<p>Because of the way @(see true-fix) works, this is always just true.</p>"\n'
            '\n'
            '  ;; bozo gross\n'
            '  (local (set-default-hints \'(\'(:in-theory (enable true-fix true-p)))))\n'
            '\n'
            '  (fty::deffixtype true\n'
            '    :pred true-p\n'
            '    :fix true-fix\n'
            '    :equiv true-equiv\n'
            '    :define t\n'
            '    :topic true-p))'
        ),
        comment_text="",
        package="ACL2",
        is_portcullis=False,
        symbol_names=["ACL2::TRUE-EQUIV"],
        symbol_kinds=["macro"],
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


def test_cell_batch(llm: ChatOpenAI, model: str) -> CellBatchResponse | dict:
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
    response, was_cached = _cached_json_call(
        prompt, structured_llm, model, cache=None,
    )
    assert response is not None, "_cached_json_call returned None — LLM call failed, check logs"

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

    # ── Symbol assertions ──
    # Cell 3 defines MY-DOUBLE — at least one idea should carry the symbol
    cell3_syms = [sr.symbol for sr in summaries[3] if sr.symbol]
    assert cell3_syms, (
        f"Cell 3 defines MY-DOUBLE but no idea carries a symbol.\n"
        f"  Ideas: {[sr.what[:60] for sr in summaries[3]]}"
    )
    print(f"  Cell 3 symbols: {cell3_syms}")

    # Cell 4 defines MY-DOUBLE-IS-EVEN — at least one idea should carry it
    if 4 in summaries:
        cell4_syms = [sr.symbol for sr in summaries[4] if sr.symbol]
        assert cell4_syms, (
            f"Cell 4 defines MY-DOUBLE-IS-EVEN but no idea carries a symbol.\n"
            f"  Ideas: {[sr.what[:60] for sr in summaries[4]]}"
        )
        print(f"  Cell 4 symbols: {cell4_syms}")

    # Check that cells without Defines: headers have NO symbol
    for ci in [0, 1, 2]:
        if ci in summaries:
            bad = [sr for sr in summaries[ci] if sr.symbol]
            assert not bad, (
                f"Cell {ci} has no Defines: header but idea(s) have "
                f"symbol={[sr.symbol for sr in bad]}"
            )
    print("  Non-defining cells correctly have no symbol ✓")

    print("\n✓ Cell batch test PASSED")
    return response


def test_basetypes_batch(llm: ChatOpenAI, model: str) -> None:
    """Test the basetypes.lisp batch that triggers json_validate_failed."""
    print("\n" + "=" * 60)
    print("TEST 1b: Basetypes Batch (json_validate_failed reproducer)")
    print("=" * 60)

    prompt = _build_cell_prompt(BASETYPES_CELLS)
    print(f"Prompt length: {len(prompt)} chars, {len(prompt.encode())} bytes")
    print(f"Cells: {len(BASETYPES_CELLS)} (indices {BASETYPES_CELLS[0].cell_index}-{BASETYPES_CELLS[-1].cell_index})")

    structured_llm = llm.with_structured_output(
        CellBatchResponse, method="json_schema", strict=True,
    )
    response, was_cached = _cached_json_call(
        prompt, structured_llm, model, cache=None,
    )
    assert response is not None, "_cached_json_call returned None — LLM call failed, check logs"

    print(f"\nCached: {was_cached}")
    print(f"Response type: {type(response).__name__}")

    summaries, continuation = _json_response_to_summaries(response)
    total_ideas = sum(len(v) for v in summaries.values())
    print(f"Cells covered: {sorted(summaries.keys())}")
    print(f"Total ideas: {total_ideas}")

    for cell_idx in sorted(summaries.keys()):
        for i, sr in enumerate(summaries[cell_idx]):
            line = f"  Cell {cell_idx} idea {i}:"
            if sr.symbol:
                line += f" sym={sr.symbol}"
            print(line)

    assert len(summaries) >= 10, f"Expected ≥10 cells covered, got {len(summaries)}"
    assert total_ideas >= 15, f"Expected ≥15 ideas, got {total_ideas}"

    print("\n✓ Basetypes batch test PASSED")


def test_notebook_summary(
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

    response, was_cached = _cached_json_call(
        prompt, structured_llm, model, cache=None,
    )
    assert response is not None, "_cached_json_call returned None — LLM call failed, check logs"

    result = _json_response_to_result(response)

    print(f"\nNotebook summary:")
    print(f"  what: {result.what}")
    print(f"  why:  {result.why}")
    print(f"  how:  {result.how}")

    assert result.what, "Notebook 'what' should be non-empty"
    print("\n✓ Notebook summary test PASSED")


def test_phase2_salvage():
    """Test that _salvage_json recovers valid JSON from a real Phase 2 crash.

    This uses the exact failed_generation from the batch-3 crash in
    scripts/summarize-all-log-v3-r3.txt.  The LLM returned valid JSON
    but the Groq API rejected it with json_validate_failed.  Our
    salvage path should parse the JSON and _json_response_to_result
    should convert it into a SummaryResult with non-empty fields.
    """
    print("\n" + "=" * 60)
    print("TEST: Phase 2 salvage (real crash data)")
    print("=" * 60)

    # Exact failed_generation from batch 3 crash (arithmetic-5 normalization book)
    failed_generation = (
        '{"what":"This file defines a suite of normalization facilities for'
        " algebraic terms, including the function"
        " normalize-terms-such-as-a/a+b-+-b/a+b-fn that handles fractions"
        " with a common denominator, distribute-* for multiplying numeric"
        " operands, and normalize-terms-such-as-1/ax+bx-fn which extracts a"
        " common factor from a sum before taking the reciprocal.  It also"
        " introduces helper predicates and search functions such as"
        " find-matching-addend and find-matching-factor-gather-exponents for"
        " locating subterms that can be rewritten, and theorems like"
        " distribute-*-distributes-1, distribute-*-distributes-2,"
        " normalize-terms-such-as-a/a+b-+-b/a+b,"
        " normalize-terms-such-as-1/ax+bx, and normalize-addends that"
        ' formalize these transformations.",'
        '"why":"These constructions provide the algebraic backbone needed for'
        " simplifying rational expressions during ACL2 proofs.  By"
        " normalizing sums and products of fractions into canonical forms,"
        " subsequent reasoning steps (e.g., cancellation, common-denominator"
        " combination) become straightforward rewrites rather than ad-hoc"
        " manipulations.  The file therefore underpins the arithmetic-5"
        " library\\u2019s ability to handle non-trivial rational-arithmetic"
        ' goals automatically.",'
        '"how":"To use these facilities, first include the support book with'
        " (include-book \\\"../../support/top\\\").  The main entry points are"
        " the rules normalize-terms-such-as-a/a+b-+-b/a+b,"
        " normalize-terms-such-as-1/ax+bx, and normalize-addends, which fire"
        " automatically via :rewrite rules when their left-hand side patterns"
        " match a goal.  If finer control is needed, individual helpers such"
        " as distribute-* or find-matching-addend can be enabled or disabled"
        " selectively.  Because the rules depend on meta-level predicates"
        " (e.g., proveably-non-zero), users should ensure that relevant"
        " type-prescription or linear-arithmetic lemmas are available in the"
        ' current theory."}'
    )

    # Step 1: _salvage_json should parse the raw JSON string
    salvaged = _salvage_json(failed_generation)
    assert salvaged is not None, "_salvage_json returned None on real crash data"
    assert isinstance(salvaged, dict), f"Expected dict, got {type(salvaged)}"
    for key in ("what", "why", "how"):
        assert key in salvaged, f"Missing key {key!r} in salvaged dict"
        assert salvaged[key], f"Empty value for key {key!r}"
    print(f"  _salvage_json returned dict with keys: {sorted(salvaged.keys())}")

    # Step 2: _json_response_to_result should convert it to a SummaryResult
    result = _json_response_to_result(salvaged)
    assert isinstance(result, SummaryResult), f"Expected SummaryResult, got {type(result)}"
    assert result.what, "SummaryResult.what is empty"
    assert result.why, "SummaryResult.why is empty"
    assert result.how, "SummaryResult.how is empty"
    print(f"  SummaryResult.what: {result.what[:80]}...")
    print(f"  SummaryResult.why:  {result.why[:80]}...")
    print(f"  SummaryResult.how:  {result.how[:80]}...")

    print("\n✓ Phase 2 salvage test PASSED")


def run_all():
    """Run all integration tests."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print("ERROR: GROQ_API_KEY not set in environment")
        sys.exit(1)

    # Pick version: --version flag or default to 20b
    version_name = "v3-groq-gpt-oss-20b"
    for i, arg in enumerate(sys.argv):
        if arg == "--version" and i + 1 < len(sys.argv):
            version_name = sys.argv[i + 1]
    if version_name not in SUMMARY_VERSIONS:
        print(f"ERROR: unknown version {version_name!r}")
        print(f"  Available: {', '.join(SUMMARY_VERSIONS)}")
        sys.exit(1)

    version_cfg = SUMMARY_VERSIONS[version_name]
    model = version_cfg["model"]
    max_tokens = version_cfg.get("max_tokens")
    base_url = version_cfg.get("base_url", "https://api.groq.com/openai/v1")

    llm_kwargs = {
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
    }
    if max_tokens:
        llm_kwargs["max_tokens"] = max_tokens

    llm = ChatOpenAI(**llm_kwargs)

    print(f"Model: {model}")
    print(f"Max tokens: {max_tokens}")
    print(f"Cells: {len(SAMPLE_CELLS)}")

    cell_response = test_cell_batch(llm, model)
    test_basetypes_batch(llm, model)
    test_notebook_summary(llm, model, cell_response)
    test_phase2_salvage()

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

    run_all()


if __name__ == "__main__":
    main()
