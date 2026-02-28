#!/usr/bin/env python3
"""Generate what/why/how summaries for ACL2 KG cells, notebooks, and directories.

Uses a map-reduce pattern powered by an LM Studio LLM (OpenAI-compatible API)
to produce self-contained descriptions at three levels of resolution.  Results
are stored in an ``ACL2Summary`` Weaviate collection with three independently
searchable named vectors (what_vector, why_vector, how_vector).

All LLM invocations are memoized in a SQLite content-addressable cache so that
re-ingestion reuses earlier results when the prompt hasn't changed.

Usage examples::

    # Dry-run on a subtree (report cell counts, skip LLM calls)
    python scripts/summarize_kg.py --dry-run --source-dir books/defsort

    # Summarize a subtree
    python scripts/summarize_kg.py --source-dir books/defsort

    # Full corpus, 4 concurrent LLM requests
    python scripts/summarize_kg.py --jobs 4

    # Rebuild the ACL2Summary collection from scratch (uses cached LLM calls)
    python scripts/summarize_kg.py --recreate
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sqlite3
import sys
import time
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import weaviate
from weaviate.classes.config import (
    Configure,
    DataType,
    Property,
    ReferenceProperty,
)
from weaviate.classes.query import Filter
from weaviate.util import generate_uuid5

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

# ─── Constants ───────────────────────────────────────────────────────

COLLECTION_SUMMARY = "ACL2Summary"
COLLECTION_NOTEBOOK = "ACL2Notebook"
COLLECTION_CELL = "ACL2Cell"
COLLECTION_SYMBOL = "ACL2Symbol"

DEFAULT_WEAVIATE_HOST = "host.docker.internal"
DEFAULT_WEAVIATE_PORT = 8080
DEFAULT_WEAVIATE_GRPC_PORT = 50051
DEFAULT_OLLAMA_URL = "http://host.docker.internal:11434"
DEFAULT_EMBED_MODEL = "nomic-embed-text:latest"
DEFAULT_LM_STUDIO_URL = "http://host.docker.internal:1234/v1"
DEFAULT_BATCH_SIZE = 200
DEFAULT_JOBS = 4
DEFAULT_CACHE_PATH = "scripts/.llm_cache.sqlite"
CHECKPOINT_FILE = "scripts/.summarize_checkpoint.json"

# Maximum cell summaries per notebook chunk in the map step.
NOTEBOOK_CHUNK_SIZE = 20
DEFAULT_CONTEXT_SIZE = 8192

log = logging.getLogger("summarize-kg")


# ─── Data classes ────────────────────────────────────────────────────


@dataclass
class CellRecord:
    """Cell data fetched from Weaviate for summarization."""

    notebook_source: str
    cell_index: int
    cell_type: str
    code_text: str
    comment_text: str
    package: str
    is_portcullis: bool
    symbol_names: list[str] = field(default_factory=list)
    symbol_kinds: list[str] = field(default_factory=list)
    dep_names: list[str] = field(default_factory=list)


@dataclass
class SummaryResult:
    """Parsed what/why/how from an LLM call."""

    what: str = ""
    why: str = ""
    how: str = ""


# ─── Tool-calling models for batched cell summaries ─────────────────


class ReportWhat(BaseModel):
    """Report what a notebook cell does — its functional behaviour."""

    cell_number: int = Field(description="The 0-based cell index")
    summary: str = Field(
        description="Concise description of what the cell does (1-3 sentences)"
    )


class ReportWhy(BaseModel):
    """Report the purpose or goal of a notebook cell."""

    cell_number: int = Field(description="The 0-based cell index")
    summary: str = Field(description="The purpose or goal (1-3 sentences)")


class ReportHow(BaseModel):
    """Report usage instructions for a notebook cell."""

    cell_number: int = Field(description="The 0-based cell index")
    summary: str = Field(
        description="Brief usage instructions (1-3 sentences)"
    )


class ContinuationContext(BaseModel):
    """Provide context to carry forward when the notebook is split across batches."""

    context: str = Field(
        description="Key context from this batch needed to understand subsequent cells"
    )


CELL_TOOLS = [ReportWhat, ReportWhy, ReportHow, ContinuationContext]


class SummaryWhat(BaseModel):
    """Report what a notebook or directory provides — its overall functionality."""

    summary: str = Field(
        description="Concise description of overall functionality (2-4 sentences)"
    )


class SummaryWhy(BaseModel):
    """Report the purpose or goal of a notebook or directory."""

    summary: str = Field(
        description="The broader purpose or goal (2-4 sentences)"
    )


class SummaryHow(BaseModel):
    """Report usage instructions for a notebook or directory."""

    summary: str = Field(
        description="Usage instructions — include-book path, key functions/macros (2-4 sentences)"
    )


SUMMARY_TOOLS = [SummaryWhat, SummaryWhy, SummaryHow]


# ─── LLM Call Memoization (SQLite) ──────────────────────────────────


class LLMCache:
    """Content-addressable LLM call cache backed by SQLite."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS llm_cache (
                    prompt_hash TEXT PRIMARY KEY,
                    prompt_text TEXT,
                    response    TEXT,
                    model       TEXT,
                    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self._conn.commit()
        return self._conn

    def get(self, prompt: str) -> str | None:
        """Return cached response or None."""
        conn = self._connect()
        h = str(generate_uuid5(prompt))
        row = conn.execute(
            "SELECT response FROM llm_cache WHERE prompt_hash = ?", (h,)
        ).fetchone()
        return row[0] if row else None

    def put(self, prompt: str, response: str, model: str) -> None:
        """Store an LLM response."""
        conn = self._connect()
        h = str(generate_uuid5(prompt))
        conn.execute(
            "INSERT OR REPLACE INTO llm_cache (prompt_hash, prompt_text, response, model) VALUES (?, ?, ?, ?)",
            (h, prompt, response, model),
        )
        conn.commit()

    def clear(self) -> None:
        """Drop and recreate the cache table."""
        conn = self._connect()
        conn.execute("DROP TABLE IF EXISTS llm_cache")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS llm_cache (
                prompt_hash TEXT PRIMARY KEY,
                prompt_text TEXT,
                response    TEXT,
                model       TEXT,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

    def count(self) -> int:
        conn = self._connect()
        row = conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()
        return row[0] if row else 0

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


# ─── LLM Studio auto-detect ─────────────────────────────────────────


def detect_lm_studio_model(base_url: str) -> str:
    """Auto-detect the first loaded model in LM Studio."""
    url = base_url.rstrip("/").replace("/v1", "/v1") + "/models"
    if "/v1/v1" in url:
        url = url.replace("/v1/v1", "/v1")
    try:
        resp = urllib.request.urlopen(url, timeout=5)
        data = json.loads(resp.read())
        models = data.get("data", [])
        if models:
            model_id = models[0]["id"]
            log.info("Auto-detected LM Studio model: %s", model_id)
            return model_id
    except Exception as e:
        log.warning("Could not auto-detect LM Studio model: %s", e)
    return "local-model"


# ─── Prompt templates ────────────────────────────────────────────────

BATCH_CELL_PROMPT = """\
/no_think
You are an expert in ACL2 (A Computational Logic for Applicative Common Lisp) \
and formal verification.  Below are cells from the notebook ``{source_file}``.

For each substantive cell, call the appropriate tool(s):
- report_what: describe what the cell does (functionality / behaviour)
- report_why: explain the purpose or goal
- report_how: provide usage instructions (only if applicable)

Use the cell_number argument to identify which cell you are annotating.
Skip trivial cells (bare include-book, in-package, or very short boilerplate).
Keep each summary to a few sentences.  Be precise and use ACL2 terminology correctly.
{continuation_section}
--- Cells ---
{cells_text}"""

NOTEBOOK_CHUNK_PROMPT = """\
/no_think
You are summarizing a group of ACL2 notebook cells.  Below are individual \
cell summaries from the same notebook file ``{source_file}``.

Call each tool once to summarize this group:
- summary_what: What this group of definitions/theorems accomplishes.
- summary_why: The broader purpose or goal.
- summary_how: How to use the facilities defined here.

Keep each to 2-4 sentences.  Be precise.

--- Cell Summaries ---
{cell_summaries}"""

NOTEBOOK_REDUCE_PROMPT = """\
/no_think
You are summarizing an ACL2 notebook file ``{source_file}``.
Below are intermediate summaries from different sections of this notebook.

Call each tool once to produce the combined summary:
- summary_what: What this file defines or proves, overall.
- summary_why: The purpose of this file in the library.
- summary_how: How to use the facilities it provides (include-book path, key functions/macros).

Keep each to 2-4 sentences.

--- Section Summaries ---
{section_summaries}"""

DIRECTORY_REDUCE_PROMPT = """\
/no_think
You are summarizing the ACL2 library directory ``{directory}``.
Below are summaries of the notebooks and subdirectories it contains.

Call each tool once to produce the combined summary:
- summary_what: What this directory provides.
- summary_why: Its purpose in the broader ACL2 library.
- summary_how: How to use it (key include-book paths, primary entry points).

Keep each to 3-5 sentences.

--- Contents ---
{contents}"""


# ─── Weaviate data fetching ──────────────────────────────────────────


def _fetch_cells_for_notebook(
    client: weaviate.WeaviateClient,
    notebook_source: str,
) -> list[CellRecord]:
    """Fetch all cells for a notebook from Weaviate, with symbol info."""
    cell_coll = client.collections.get(COLLECTION_CELL)

    cells: list[CellRecord] = []
    # Fetch cells matching this notebook.
    result = cell_coll.query.fetch_objects(
        filters=Filter.by_property("notebook_source").equal(notebook_source),
        limit=10000,
        return_properties=[
            "notebook_source", "cell_index", "cell_type",
            "code_text", "comment_text", "package", "is_portcullis",
        ],
        return_references=weaviate.classes.query.QueryReference(
            link_on="definesSymbols",
            return_properties=["qualified_name", "kind"],
            return_references=weaviate.classes.query.QueryReference(
                link_on="dependsOn",
                return_properties=["qualified_name"],
            ),
        ),
    )

    for obj in result.objects:
        props = obj.properties
        # Post-filter: Weaviate TEXT tokenization can match similar paths
        if props.get("notebook_source") != notebook_source:
            continue

        sym_names = []
        sym_kinds = []
        dep_names = []
        refs = obj.references
        if refs and "definesSymbols" in refs:
            for sym_obj in refs["definesSymbols"].objects:
                sp = sym_obj.properties
                sym_names.append(sp.get("qualified_name", ""))
                sym_kinds.append(sp.get("kind", "unknown"))
                # Gather deps from this symbol
                if sym_obj.references and "dependsOn" in sym_obj.references:
                    for dep_obj in sym_obj.references["dependsOn"].objects:
                        dp = dep_obj.properties
                        dep_names.append(dp.get("qualified_name", ""))

        cells.append(CellRecord(
            notebook_source=props.get("notebook_source", ""),
            cell_index=props.get("cell_index", 0),
            cell_type=props.get("cell_type", ""),
            code_text=props.get("code_text", ""),
            comment_text=props.get("comment_text", ""),
            package=props.get("package", ""),
            is_portcullis=props.get("is_portcullis", False),
            symbol_names=sym_names,
            symbol_kinds=sym_kinds,
            dep_names=list(set(dep_names)),
        ))

    cells.sort(key=lambda c: c.cell_index)
    return cells


def _fetch_all_notebook_sources(
    client: weaviate.WeaviateClient,
    source_dir: str | None = None,
    no_recurse: bool = False,
) -> list[str]:
    """Return all notebook source_file values, optionally filtered by prefix.

    If *no_recurse* is True and *source_dir* is set, only return notebooks
    whose parent directory exactly matches *source_dir* (no subdirectories).
    """
    nb_coll = client.collections.get(COLLECTION_NOTEBOOK)
    sources: list[str] = []

    for obj in nb_coll.iterator(
        return_properties=["source_file"],
    ):
        src = obj.properties.get("source_file", "")
        if source_dir:
            if no_recurse:
                # Match only notebooks directly in source_dir
                parent = str(Path(src).parent)
                if parent != source_dir and parent != source_dir.rstrip("/"):
                    continue
            else:
                if not src.startswith(source_dir):
                    continue
        sources.append(src)

    sources.sort()
    return sources


# ─── LLM invocation (with caching) ──────────────────────────────────


def _summary_tools_to_result(tool_calls: list[dict]) -> SummaryResult:
    """Convert summary tool calls into a SummaryResult."""
    result = SummaryResult()
    for tc in tool_calls:
        name = tc.get("name", "")
        text = tc.get("args", {}).get("summary", "")
        if name == "SummaryWhat":
            result.what = text
        elif name == "SummaryWhy":
            result.why = text
        elif name == "SummaryHow":
            result.how = text
    return result


# ─── Phase 1: Cell Summaries (batched tool calling) ─────────────────


def _build_batch_cells_text(cells: list[CellRecord]) -> str:
    """Format a batch of cells for the batch prompt."""
    parts: list[str] = []
    for c in cells:
        content = c.code_text if c.cell_type == "code" else c.comment_text
        if not content:
            content = c.code_text or c.comment_text or "(empty)"
        header = f"[Cell {c.cell_index}] ({c.cell_type}, package: {c.package or 'ACL2'})"
        if c.symbol_names:
            syms = ", ".join(
                f"{n} ({k})" for n, k in zip(c.symbol_names, c.symbol_kinds)
            )
            header += f"\nDefines: {syms}"
        parts.append(f"{header}\n{content}")
    return "\n\n".join(parts)


def _batch_cells_by_size(
    cells: list[CellRecord], max_bytes: int,
) -> list[list[CellRecord]]:
    """Split cells into batches whose combined text fits within *max_bytes*."""
    batches: list[list[CellRecord]] = []
    current_batch: list[CellRecord] = []
    current_size = 0

    for cell in cells:
        content = cell.code_text if cell.cell_type == "code" else cell.comment_text
        cell_size = len((content or "").encode("utf-8")) + 120  # header overhead
        if current_batch and current_size + cell_size > max_bytes:
            batches.append(current_batch)
            current_batch = []
            current_size = 0
        current_batch.append(cell)
        current_size += cell_size

    if current_batch:
        batches.append(current_batch)
    return batches


async def _cached_tool_call(
    prompt: str,
    llm_with_tools,
    model: str,
    cache: LLMCache | None,
    sem: asyncio.Semaphore,
) -> tuple[list[dict], bool]:
    """Invoke the LLM with tool calling and SQLite caching.

    Returns ``(tool_calls, was_cached)`` where *tool_calls* is a list
    of dicts: ``[{"name": ..., "args": {...}}, ...]``.
    """
    if cache is not None:
        cached = cache.get(prompt)
        if cached is not None:
            return json.loads(cached), True

    async with sem:
        response = await llm_with_tools.ainvoke([HumanMessage(content=prompt)])

    tool_calls = [
        {"name": tc["name"], "args": tc["args"]}
        for tc in (response.tool_calls or [])
    ]

    if cache is not None:
        cache.put(prompt, json.dumps(tool_calls), model)

    return tool_calls, False


def _tool_calls_to_summaries(
    tool_calls: list[dict],
) -> tuple[dict[int, SummaryResult], str]:
    """Parse tool-call dicts into per-cell SummaryResults and continuation context."""
    summaries: dict[int, SummaryResult] = {}
    continuation = ""

    for tc in tool_calls:
        name = tc.get("name", "")
        args = tc.get("args", {})

        if name == "ContinuationContext":
            continuation = args.get("context", "")
            continue

        cell_num = args.get("cell_number")
        if cell_num is None:
            continue

        if cell_num not in summaries:
            summaries[cell_num] = SummaryResult()

        text = args.get("summary", "")
        if name == "ReportWhat":
            summaries[cell_num].what = text
        elif name == "ReportWhy":
            summaries[cell_num].why = text
        elif name == "ReportHow":
            summaries[cell_num].how = text

    return summaries, continuation


async def summarize_cells(
    client: weaviate.WeaviateClient,
    notebook_sources: list[str],
    llm: ChatOpenAI,
    model: str,
    cache: LLMCache | None,
    sem: asyncio.Semaphore,
    batch_size: int,
    checkpoint: dict,
    context_size: int = DEFAULT_CONTEXT_SIZE,
    dry_run: bool = False,
) -> dict[str, list[tuple[int, SummaryResult]]]:
    """Phase 1: Generate cell-level summaries via batched tool calling.

    Cells are grouped into batches that fit within *context_size* bytes.
    The LLM receives each batch and calls report_what / report_why /
    report_how tools for substantive cells.  A continuation context is
    passed between batches within the same notebook.
    """
    llm_with_tools = llm.bind_tools(CELL_TOOLS) if llm else None
    summary_coll = client.collections.get(COLLECTION_SUMMARY) if not dry_run else None

    total_cells = 0
    total_batches = 0
    total_skipped_cp = 0
    total_cached = 0
    total_llm = 0
    all_results: dict[str, list[tuple[int, SummaryResult]]] = {}

    for nb_idx, nb_src in enumerate(notebook_sources, 1):
        cells = _fetch_cells_for_notebook(client, nb_src)
        total_cells += len(cells)

        if nb_idx % 50 == 0 or nb_idx == len(notebook_sources):
            log.info(
                "  Cell scan: %d/%d notebooks, %d cells, %d batches so far",
                nb_idx, len(notebook_sources), total_cells, total_batches,
            )

        if dry_run:
            all_results[nb_src] = [
                (c.cell_index, SummaryResult()) for c in cells
            ]
            continue

        # Split cells into context-sized batches.
        batches = _batch_cells_by_size(cells, context_size)
        total_batches += len(batches)

        nb_results: list[tuple[int, SummaryResult]] = []
        continuation_context = ""

        for batch_cells in batches:
            batch_key = (
                f"{nb_src}:batch:"
                f"{batch_cells[0].cell_index}-{batch_cells[-1].cell_index}"
            )

            # Check checkpoint.
            if batch_key in checkpoint.get("cell_batches", set()):
                total_skipped_cp += 1
                continue

            cells_text = _build_batch_cells_text(batch_cells)

            cont_section = ""
            if continuation_context:
                cont_section = (
                    "\n--- Context from previous cells ---\n"
                    + continuation_context + "\n"
                )

            prompt = BATCH_CELL_PROMPT.format(
                source_file=nb_src,
                continuation_section=cont_section,
                cells_text=cells_text,
            )

            tool_calls, was_cached = await _cached_tool_call(
                prompt, llm_with_tools, model, cache, sem,
            )
            if was_cached:
                total_cached += 1
            else:
                total_llm += 1

            summaries, continuation_context = _tool_calls_to_summaries(
                tool_calls,
            )

            # Merge into notebook results.
            for cell_idx, summary in summaries.items():
                nb_results.append((cell_idx, summary))

            # Upsert to Weaviate.
            if summary_coll is not None and summaries:
                upsert_items: list[dict] = []
                for cell_idx, summary in summaries.items():
                    ref_key = f"{nb_src}:{cell_idx}"
                    uuid = str(generate_uuid5(f"summary:cell:{ref_key}"))
                    nb_uuid = str(generate_uuid5(f"notebook:{nb_src}"))
                    cell_uuid = str(
                        generate_uuid5(f"cell:{nb_src}:{cell_idx}")
                    )
                    cell_rec = next(
                        (c for c in batch_cells if c.cell_index == cell_idx),
                        None,
                    )
                    upsert_items.append({
                        "uuid": uuid,
                        "properties": {
                            "scope": "cell",
                            "ref_key": ref_key,
                            "what_summary": summary.what or "",
                            "why_summary": summary.why or "",
                            "how_summary": summary.how or "",
                            "source_file": nb_src,
                            "cell_index": cell_idx,
                            "directory": str(Path(nb_src).parent),
                            "symbol_names": (
                                cell_rec.symbol_names if cell_rec else []
                            ),
                        },
                        "references": {
                            "sourceNotebook": nb_uuid,
                            "sourceCell": cell_uuid,
                        },
                    })

                    # Mark cell in checkpoint (Phase 2 compatibility).
                    checkpoint.setdefault("cells", set()).add(ref_key)

                with summary_coll.batch.fixed_size(
                    batch_size=batch_size,
                ) as wb:
                    for item in upsert_items:
                        wb.add_object(
                            properties=item["properties"],
                            uuid=item["uuid"],
                            references=item["references"],
                        )

            # Mark batch in checkpoint.
            checkpoint.setdefault("cell_batches", set()).add(batch_key)

        all_results[nb_src] = nb_results

        if nb_idx % 10 == 0:
            log.info(
                "  Cells: %d/%d notebooks, %d batches, %d cached, %d LLM",
                nb_idx, len(notebook_sources), total_batches,
                total_cached, total_llm,
            )

    log.info(
        "Phase 1 complete: %d cells across %d batches, "
        "%d checkpointed, %d cached, %d LLM calls",
        total_cells, total_batches, total_skipped_cp,
        total_cached, total_llm,
    )

    return all_results


# ─── Phase 2: Notebook Summaries ─────────────────────────────────────


async def summarize_notebooks(
    client: weaviate.WeaviateClient,
    notebook_sources: list[str],
    cell_summaries: dict[str, list[tuple[int, SummaryResult]]],
    llm: ChatOpenAI,
    model: str,
    cache: LLMCache | None,
    sem: asyncio.Semaphore,
    batch_size: int,
    checkpoint: dict,
    dry_run: bool = False,
) -> dict[str, SummaryResult]:
    """Phase 2: Generate notebook-level summaries via map-reduce over cell summaries."""
    summary_coll = client.collections.get(COLLECTION_SUMMARY) if not dry_run else None
    nb_summaries: dict[str, SummaryResult] = {}

    total_skipped = 0
    total_done = 0

    for nb_idx, nb_src in enumerate(notebook_sources, 1):
        ref_key = nb_src

        # Skip if checkpointed.
        if ref_key in checkpoint.get("notebooks", set()):
            total_skipped += 1
            continue

        # Gather cell summaries for this notebook.
        cell_sums = cell_summaries.get(nb_src, [])

        # If no cell summaries available, try to reconstruct from Weaviate.
        if not cell_sums and not dry_run:
            cell_sums = _load_cell_summaries_from_weaviate(client, nb_src)

        # Filter out empty summaries.
        non_empty = [(idx, s) for idx, s in cell_sums if s.what or s.why or s.how]

        if not non_empty:
            log.debug("No cell summaries for %s, skipping notebook summary", nb_src)
            continue

        if dry_run:
            nb_summaries[nb_src] = SummaryResult()
            continue

        # Map: chunk cell summaries and summarize each chunk.
        chunks = _chunk_list(non_empty, NOTEBOOK_CHUNK_SIZE)
        intermediates: list[SummaryResult] = []

        llm_with_summary_tools = llm.bind_tools(SUMMARY_TOOLS)

        for chunk in chunks:
            cell_text = _format_cell_summaries(chunk)
            prompt = NOTEBOOK_CHUNK_PROMPT.format(
                source_file=nb_src,
                cell_summaries=cell_text,
            )
            tcs, _ = await _cached_tool_call(
                prompt, llm_with_summary_tools, model, cache, sem,
            )
            intermediates.append(_summary_tools_to_result(tcs))

        # Reduce: combine intermediates (or use directly if only one chunk).
        if len(intermediates) == 1:
            final = intermediates[0]
        else:
            section_text = _format_intermediates(intermediates)
            prompt = NOTEBOOK_REDUCE_PROMPT.format(
                source_file=nb_src,
                section_summaries=section_text,
            )
            tcs, _ = await _cached_tool_call(
                prompt, llm_with_summary_tools, model, cache, sem,
            )
            final = _summary_tools_to_result(tcs)

        nb_summaries[nb_src] = final

        # Upsert to Weaviate.
        if summary_coll is not None:
            uuid = str(generate_uuid5(f"summary:notebook:{ref_key}"))
            nb_uuid = str(generate_uuid5(f"notebook:{nb_src}"))       
            with summary_coll.batch.fixed_size(batch_size=batch_size) as batch:
                batch.add_object(
                    properties={
                        "scope": "notebook",
                        "ref_key": ref_key,
                        "what_summary": final.what or "",
                        "why_summary": final.why or "",
                        "how_summary": final.how or "",
                        "source_file": nb_src,
                        "cell_index": -1,
                        "directory": str(Path(nb_src).parent),
                        "symbol_names": [],
                    },
                    uuid=uuid,
                    references={"sourceNotebook": nb_uuid},
                )

        checkpoint.setdefault("notebooks", set()).add(ref_key)
        total_done += 1

        if nb_idx % 10 == 0:
            log.info("  Notebooks: %d/%d processed, %d skipped",
                     nb_idx, len(notebook_sources), total_skipped)

    log.info("Phase 2 complete: %d notebook summaries, %d skipped",
             total_done, total_skipped)

    return nb_summaries


def _load_cell_summaries_from_weaviate(
    client: weaviate.WeaviateClient,
    notebook_source: str,
) -> list[tuple[int, SummaryResult]]:
    """Load previously stored cell summaries from Weaviate."""
    summary_coll = client.collections.get(COLLECTION_SUMMARY)
    results: list[tuple[int, SummaryResult]] = []

    response = summary_coll.query.fetch_objects(
        filters=(
            Filter.by_property("scope").equal("cell")
            & Filter.by_property("source_file").equal(notebook_source)
        ),
        limit=10000,
        return_properties=["cell_index", "what_summary", "why_summary", "how_summary"],
    )

    for obj in response.objects:
        p = obj.properties
        # Post-filter for exact source match
        if p.get("source_file") != notebook_source:
            continue
        results.append((
            p.get("cell_index", 0),
            SummaryResult(
                what=p.get("what_summary", ""),
                why=p.get("why_summary", ""),
                how=p.get("how_summary", ""),
            ),
        ))

    results.sort(key=lambda x: x[0])
    return results


def _chunk_list(items: list, size: int) -> list[list]:
    """Split a list into chunks of at most *size*."""
    return [items[i:i + size] for i in range(0, len(items), size)]


def _format_cell_summaries(cells: list[tuple[int, SummaryResult]]) -> str:
    """Format cell summaries into a text block for the notebook prompt."""
    parts = []
    for idx, s in cells:
        entry = f"Cell {idx}:"
        if s.what:
            entry += f"\n  what: {s.what}"
        if s.why:
            entry += f"\n  why: {s.why}"
        if s.how:
            entry += f"\n  how: {s.how}"
        parts.append(entry)
    return "\n\n".join(parts)


def _format_intermediates(intermediates: list[SummaryResult]) -> str:
    """Format intermediate summaries for the reduce prompt."""
    parts = []
    for i, s in enumerate(intermediates, 1):
        entry = f"Section {i}:"
        if s.what:
            entry += f"\n  what: {s.what}"
        if s.why:
            entry += f"\n  why: {s.why}"
        if s.how:
            entry += f"\n  how: {s.how}"
        parts.append(entry)
    return "\n\n".join(parts)


# ─── Phase 3: Directory Summaries ────────────────────────────────────


async def summarize_directories(
    client: weaviate.WeaviateClient,
    notebook_sources: list[str],
    nb_summaries: dict[str, SummaryResult],
    llm: ChatOpenAI,
    model: str,
    cache: LLMCache | None,
    sem: asyncio.Semaphore,
    batch_size: int,
    checkpoint: dict,
    dry_run: bool = False,
) -> dict[str, SummaryResult]:
    """Phase 3: Bottom-up directory summaries."""
    summary_coll = client.collections.get(COLLECTION_SUMMARY) if not dry_run else None

    # Build directory tree: dir → list of notebook sources in that dir (non-recursive).
    dir_notebooks: dict[str, list[str]] = defaultdict(list)
    all_dirs: set[str] = set()
    for src in notebook_sources:
        d = str(Path(src).parent)
        dir_notebooks[d].append(src)
        # Register all ancestor directories up to "books".
        parts = Path(d).parts
        for i in range(len(parts)):
            ancestor = str(Path(*parts[:i + 1]))
            all_dirs.add(ancestor)

    # Sort directories bottom-up (longest paths first = deepest first).
    sorted_dirs = sorted(all_dirs, key=lambda d: d.count("/"), reverse=True)

    dir_summaries: dict[str, SummaryResult] = {}
    total_done = 0
    total_skipped = 0

    for d_idx, directory in enumerate(sorted_dirs, 1):
        ref_key = directory

        if ref_key in checkpoint.get("directories", set()):
            total_skipped += 1
            continue

        # Collect content summaries for this directory.
        contents_parts: list[str] = []

        # Notebook summaries in this directory (non-recursive).
        for nb_src in dir_notebooks.get(directory, []):
            s = nb_summaries.get(nb_src)
            if s is None and not dry_run:
                s = _load_notebook_summary_from_weaviate(client, nb_src)
            if s and (s.what or s.why or s.how):
                entry = f"File: {Path(nb_src).name}"
                if s.what:
                    entry += f"\n  what: {s.what}"
                if s.why:
                    entry += f"\n  why: {s.why}"
                if s.how:
                    entry += f"\n  how: {s.how}"
                contents_parts.append(entry)

        # Child directory summaries.
        for child_dir, child_sum in dir_summaries.items():
            if str(Path(child_dir).parent) == directory:
                entry = f"Subdirectory: {Path(child_dir).name}/"
                if child_sum.what:
                    entry += f"\n  what: {child_sum.what}"
                if child_sum.why:
                    entry += f"\n  why: {child_sum.why}"
                if child_sum.how:
                    entry += f"\n  how: {child_sum.how}"
                contents_parts.append(entry)

        if not contents_parts:
            log.debug("No content for directory %s, skipping", directory)
            continue

        if dry_run:
            dir_summaries[directory] = SummaryResult()
            continue

        # Build and invoke prompt.
        llm_with_summary_tools = llm.bind_tools(SUMMARY_TOOLS)
        prompt = DIRECTORY_REDUCE_PROMPT.format(
            directory=directory,
            contents="\n\n".join(contents_parts),
        )
        tcs, _ = await _cached_tool_call(
            prompt, llm_with_summary_tools, model, cache, sem,
        )
        final = _summary_tools_to_result(tcs)
        dir_summaries[directory] = final

        # Upsert to Weaviate.
        if summary_coll is not None:
            uuid = str(generate_uuid5(f"summary:directory:{ref_key}"))
            with summary_coll.batch.fixed_size(batch_size=batch_size) as batch:
                batch.add_object(
                    properties={
                        "scope": "directory",
                        "ref_key": ref_key,
                        "what_summary": final.what or "",
                        "why_summary": final.why or "",
                        "how_summary": final.how or "",
                        "source_file": "",
                        "cell_index": -1,
                        "directory": directory,
                        "symbol_names": [],
                    },
                    uuid=uuid,
                )

        checkpoint.setdefault("directories", set()).add(ref_key)
        total_done += 1

        if d_idx % 20 == 0:
            log.info("  Directories: %d/%d processed, %d skipped",
                     d_idx, len(sorted_dirs), total_skipped)

    log.info("Phase 3 complete: %d directory summaries, %d skipped",
             total_done, total_skipped)

    return dir_summaries


def _load_notebook_summary_from_weaviate(
    client: weaviate.WeaviateClient,
    notebook_source: str,
) -> SummaryResult | None:
    """Load a previously stored notebook summary from Weaviate."""
    summary_coll = client.collections.get(COLLECTION_SUMMARY)
    response = summary_coll.query.fetch_objects(
        filters=(
            Filter.by_property("scope").equal("notebook")
            & Filter.by_property("source_file").equal(notebook_source)
        ),
        limit=5,
        return_properties=["what_summary", "why_summary", "how_summary", "source_file"],
    )

    for obj in response.objects:
        p = obj.properties
        if p.get("source_file") != notebook_source:
            continue
        return SummaryResult(
            what=p.get("what_summary", ""),
            why=p.get("why_summary", ""),
            how=p.get("how_summary", ""),
        )
    return None


# ─── Collection schema ───────────────────────────────────────────────


def ensure_summary_collection(
    client: weaviate.WeaviateClient,
    ollama_url: str,
    embed_model: str,
    recreate: bool = False,
) -> None:
    """Create or recreate the ACL2Summary collection."""
    if recreate and client.collections.exists(COLLECTION_SUMMARY):
        log.info("Deleting collection %s", COLLECTION_SUMMARY)
        client.collections.delete(COLLECTION_SUMMARY)

    if client.collections.exists(COLLECTION_SUMMARY):
        log.info("Collection %s already exists, skipping creation", COLLECTION_SUMMARY)
        return

    log.info("Creating collection %s", COLLECTION_SUMMARY)
    client.collections.create(
        COLLECTION_SUMMARY,
        vectorizer_config=[
            Configure.NamedVectors.text2vec_ollama(
                name="what_vector",
                api_endpoint=ollama_url,
                model=embed_model,
                source_properties=["what_summary"],
            ),
            Configure.NamedVectors.text2vec_ollama(
                name="why_vector",
                api_endpoint=ollama_url,
                model=embed_model,
                source_properties=["why_summary"],
            ),
            Configure.NamedVectors.text2vec_ollama(
                name="how_vector",
                api_endpoint=ollama_url,
                model=embed_model,
                source_properties=["how_summary"],
            ),
        ],
        properties=[
            Property(name="scope", data_type=DataType.TEXT,
                     skip_vectorization=True,
                     description="cell, notebook, or directory"),
            Property(name="ref_key", data_type=DataType.TEXT,
                     skip_vectorization=True,
                     description="Deterministic key for this summary"),
            Property(name="what_summary", data_type=DataType.TEXT,
                     description="What it does"),
            Property(name="why_summary", data_type=DataType.TEXT,
                     description="Purpose / goal"),
            Property(name="how_summary", data_type=DataType.TEXT,
                     description="Usage instructions"),
            Property(name="source_file", data_type=DataType.TEXT,
                     skip_vectorization=True,
                     description="Parent notebook path"),
            Property(name="cell_index", data_type=DataType.INT,
                     description="Cell index (-1 for non-cell scopes)"),
            Property(name="directory", data_type=DataType.TEXT,
                     skip_vectorization=True,
                     description="Containing directory path"),
            Property(name="symbol_names", data_type=DataType.TEXT_ARRAY,
                     skip_vectorization=True,
                     description="Symbols defined (cell scope)"),
        ],
        references=[
            ReferenceProperty(
                name="sourceNotebook",
                target_collection=COLLECTION_NOTEBOOK,
            ),
            ReferenceProperty(
                name="sourceCell",
                target_collection=COLLECTION_CELL,
            ),
        ],
    )


# ─── Checkpoint ──────────────────────────────────────────────────────


def _load_checkpoint(path: str) -> dict:
    try:
        with open(path) as f:
            data = json.load(f)
        # Convert lists back to sets for fast lookup.
        for key in ("cells", "notebooks", "directories", "cell_batches"):
            if key in data:
                data[key] = set(data[key])
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_checkpoint(path: str, data: dict) -> None:
    # Convert sets to sorted lists for JSON serialization.
    serializable = {}
    for key, val in data.items():
        if isinstance(val, set):
            serializable[key] = sorted(val)
        else:
            serializable[key] = val

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(serializable, f)


# ─── Dry-run report ──────────────────────────────────────────────────


def _dry_run_report(
    notebook_sources: list[str],
    cell_summaries: dict[str, list[tuple[int, SummaryResult]]],
) -> None:
    """Print a summary of what would be processed."""
    total_cells = sum(len(v) for v in cell_summaries.values())

    # Directory count.
    all_dirs: set[str] = set()
    for src in notebook_sources:
        d = str(Path(src).parent)
        parts = Path(d).parts
        for i in range(len(parts)):
            all_dirs.add(str(Path(*parts[:i + 1])))

    print("\n=== DRY-RUN SUMMARY ===")
    print(f"Notebooks:            {len(notebook_sources)}")
    print(f"Total cells:          {total_cells}")
    print(f"Directories:          {len(all_dirs)}")
    print(f"Est. LLM calls:")
    print(f"  Cell batches:       (depends on --context-size)")
    # Rough estimate: 1 map call per NOTEBOOK_CHUNK_SIZE cells + 1 reduce per notebook
    map_calls = sum(
        max(1, len(v) // NOTEBOOK_CHUNK_SIZE + (1 if len(v) % NOTEBOOK_CHUNK_SIZE else 0))
        for v in cell_summaries.values() if v
    )
    reduce_calls = sum(1 for v in cell_summaries.values()
                       if v and len(v) > NOTEBOOK_CHUNK_SIZE)
    print(f"  Notebook map:       {map_calls}")
    print(f"  Notebook reduce:    {reduce_calls}")
    print(f"  Directory reduce:   {len(all_dirs)}")
    print(f"  Total (est.):       (cell batches + {map_calls} + {reduce_calls} + {len(all_dirs)})")
    print()


# ─── CLI + main ──────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Generate what/why/how summaries for ACL2 KG",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--scope", default="all",
        choices=["cell", "notebook", "directory", "all"],
        help="Which scope(s) to process (default: all)",
    )
    p.add_argument(
        "--source-dir", default=None,
        help="Limit to notebooks under this prefix (e.g. books/defsort)",
    )
    p.add_argument(
        "--no-recurse", action="store_true",
        help="Only process notebooks directly in --source-dir (no subdirectories)",
    )
    p.add_argument(
        "--weaviate-host", default=DEFAULT_WEAVIATE_HOST,
        help=f"Weaviate host (default: {DEFAULT_WEAVIATE_HOST})",
    )
    p.add_argument(
        "--port", type=int, default=DEFAULT_WEAVIATE_PORT,
        help=f"Weaviate REST port (default: {DEFAULT_WEAVIATE_PORT})",
    )
    p.add_argument(
        "--grpc-port", type=int, default=DEFAULT_WEAVIATE_GRPC_PORT,
        help=f"Weaviate gRPC port (default: {DEFAULT_WEAVIATE_GRPC_PORT})",
    )
    p.add_argument(
        "--ollama-url", default=DEFAULT_OLLAMA_URL,
        help=f"Ollama API URL (default: {DEFAULT_OLLAMA_URL})",
    )
    p.add_argument(
        "--embed-model", default=DEFAULT_EMBED_MODEL,
        help=f"Ollama embedding model (default: {DEFAULT_EMBED_MODEL})",
    )
    p.add_argument(
        "--lm-studio-url", default=DEFAULT_LM_STUDIO_URL,
        help=f"LM Studio base URL (default: {DEFAULT_LM_STUDIO_URL})",
    )
    p.add_argument(
        "--model", default=None,
        help="LLM model name (auto-detected from LM Studio if not set)",
    )
    p.add_argument(
        "--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
        help=f"Weaviate batch size (default: {DEFAULT_BATCH_SIZE})",
    )
    p.add_argument(
        "-j", "--jobs", type=int, default=DEFAULT_JOBS,
        help=f"Concurrent LLM requests (default: {DEFAULT_JOBS})",
    )
    p.add_argument(
        "--context-size", type=int, default=DEFAULT_CONTEXT_SIZE,
        help=f"Max batch size in bytes for cell groups (default: {DEFAULT_CONTEXT_SIZE})",
    )
    p.add_argument(
        "--recreate", action="store_true",
        help="Drop and recreate ACL2Summary collection",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Report counts without calling the LLM",
    )
    p.add_argument(
        "--no-cache", action="store_true",
        help="Bypass the LLM memoization cache",
    )
    p.add_argument(
        "--clear-cache", action="store_true",
        help="Clear the LLM cache before starting",
    )
    p.add_argument(
        "--cache-path", default=DEFAULT_CACHE_PATH,
        help=f"LLM cache SQLite path (default: {DEFAULT_CACHE_PATH})",
    )
    p.add_argument(
        "--restart", action="store_true",
        help="Clear checkpoint and start fresh",
    )
    p.add_argument(
        "--checkpoint", default=CHECKPOINT_FILE,
        help=f"Checkpoint file path (default: {CHECKPOINT_FILE})",
    )
    p.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable debug logging",
    )
    return p


async def async_main(args: argparse.Namespace) -> int:
    """Async entry point — runs the summarization pipeline."""

    # ── LLM cache setup ──────────────────────────────────────────────
    cache: LLMCache | None = None
    if not args.no_cache:
        cache = LLMCache(args.cache_path)
        if args.clear_cache:
            cache.clear()
            log.info("LLM cache cleared")
        log.info("LLM cache: %s (%d entries)", args.cache_path, cache.count())
    else:
        log.info("LLM cache disabled")

    # ── LLM setup ────────────────────────────────────────────────────
    model = args.model or os.environ.get("LM_STUDIO_MODEL")
    if not model and not args.dry_run:
        model = detect_lm_studio_model(args.lm_studio_url)

    llm: ChatOpenAI | None = None
    if not args.dry_run:
        llm = ChatOpenAI(
            base_url=args.lm_studio_url,
            api_key="lm-studio",
            model=model or "local-model",
            temperature=0.1,
        )

    sem = asyncio.Semaphore(args.jobs)

    # ── Checkpoint ───────────────────────────────────────────────────
    if args.restart:
        checkpoint: dict = {}
        log.info("Checkpoint cleared (--restart)")
    else:
        checkpoint = _load_checkpoint(args.checkpoint)
        if checkpoint:
            cells_done = len(checkpoint.get("cells", set()))
            nbs_done = len(checkpoint.get("notebooks", set()))
            dirs_done = len(checkpoint.get("directories", set()))
            log.info("Resuming from checkpoint: %d cells, %d notebooks, %d directories done",
                     cells_done, nbs_done, dirs_done)

    # ── Connect to Weaviate ──────────────────────────────────────────
    log.info("Connecting to Weaviate at %s:%d...", args.weaviate_host, args.port)
    client = weaviate.connect_to_local(
        host=args.weaviate_host,
        port=args.port,
        grpc_port=args.grpc_port,
    )

    try:
        if not client.is_ready():
            log.error("Weaviate is not ready")
            return 1
        log.info("Connected to Weaviate")

        # ── Schema ───────────────────────────────────────────────────
        ensure_summary_collection(
            client, args.ollama_url, args.embed_model,
            recreate=args.recreate,
        )

        # ── Discover notebooks ───────────────────────────────────────
        notebook_sources = _fetch_all_notebook_sources(
            client, args.source_dir, no_recurse=args.no_recurse,
        )
        log.info("Found %d notebooks%s%s", len(notebook_sources),
                 f" under {args.source_dir}" if args.source_dir else "",
                 " (no recurse)" if args.no_recurse else "")

        if not notebook_sources:
            log.warning("No notebooks found, nothing to do")
            return 0

        run_cell = args.scope in ("all", "cell")
        run_notebook = args.scope in ("all", "notebook")
        run_directory = args.scope in ("all", "directory")

        # ── Phase 1: Cell summaries ──────────────────────────────────
        cell_summaries: dict[str, list[tuple[int, SummaryResult]]] = {}
        if run_cell:
            log.info("=== Phase 1: Cell Summaries ===")
            cell_summaries = await summarize_cells(
                client, notebook_sources, llm, model, cache, sem,
                args.batch_size, checkpoint,
                context_size=args.context_size,
                dry_run=args.dry_run,
            )

        # ── Phase 2: Notebook summaries ──────────────────────────────
        nb_summaries: dict[str, SummaryResult] = {}
        if run_notebook:
            log.info("=== Phase 2: Notebook Summaries ===")
            nb_summaries = await summarize_notebooks(
                client, notebook_sources, cell_summaries,
                llm, model, cache, sem,
                args.batch_size, checkpoint, args.dry_run,
            )

        # ── Phase 3: Directory summaries ─────────────────────────────
        if run_directory:
            log.info("=== Phase 3: Directory Summaries ===")
            await summarize_directories(
                client, notebook_sources, nb_summaries,
                llm, model, cache, sem,
                args.batch_size, checkpoint, args.dry_run,
            )

        # ── Dry-run report ───────────────────────────────────────────
        if args.dry_run:
            _dry_run_report(notebook_sources, cell_summaries)

        # ── Save checkpoint ──────────────────────────────────────────
        if not args.dry_run:
            _save_checkpoint(args.checkpoint, checkpoint)
            log.info("Checkpoint saved to %s", args.checkpoint)

        # ── Final stats ──────────────────────────────────────────────
        if not args.dry_run:
            try:
                coll = client.collections.get(COLLECTION_SUMMARY)
                total = coll.aggregate.over_all(total_count=True).total_count
                log.info("ACL2Summary collection now has %d objects", total)
            except Exception:
                pass

    finally:
        client.close()
        if cache:
            cache.close()

    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    return asyncio.run(async_main(args))


if __name__ == "__main__":
    sys.exit(main())
