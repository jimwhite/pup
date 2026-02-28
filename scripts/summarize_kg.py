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

CELL_SUMMARY_PROMPT = """\
/no_think
You are an expert in ACL2 (A Computational Logic for Applicative Common Lisp) \
and formal verification.  Analyze the following ACL2 notebook cell and produce \
a JSON object with up to three fields:

- "what": A concise description of what this code or text does.
- "why": The purpose or goal — why this exists.
- "how": Brief instructions on how to use it (if applicable).

Omit any field that does not apply (e.g. a pure comment cell may only have "what").
Keep each field to 1-3 sentences.  Be precise and use ACL2 terminology correctly.

--- Cell Context ---
Package: {package}
Cell type: {cell_type}
{symbols_section}
{deps_section}
--- Cell Content ---
{content}

Respond with ONLY a valid JSON object, no markdown fences."""

NOTEBOOK_CHUNK_PROMPT = """\
/no_think
You are summarizing a group of ACL2 notebook cells.  Below are individual \
cell summaries from the same notebook file ``{source_file}``.

Combine them into a single JSON object with up to three fields:
- "what": What this group of definitions/theorems accomplishes.
- "why": The broader purpose or goal.
- "how": How to use the facilities defined here.

Keep each field to 2-4 sentences.  Be precise.

--- Cell Summaries ---
{cell_summaries}

Respond with ONLY a valid JSON object, no markdown fences."""

NOTEBOOK_REDUCE_PROMPT = """\
/no_think
You are summarizing an ACL2 notebook file ``{source_file}``.
Below are intermediate summaries from different sections of this notebook.

Combine them into a single JSON object with three fields:
- "what": What this file defines or proves, overall.
- "why": The purpose of this file in the library.
- "how": How to use the facilities it provides (include-book path, key functions/macros).

Keep each field to 2-4 sentences.

--- Section Summaries ---
{section_summaries}

Respond with ONLY a valid JSON object, no markdown fences."""

DIRECTORY_REDUCE_PROMPT = """\
/no_think
You are summarizing the ACL2 library directory ``{directory}``.
Below are summaries of the notebooks and subdirectories it contains.

Combine them into a single JSON object with three fields:
- "what": What this directory provides.
- "why": Its purpose in the broader ACL2 library.
- "how": How to use it (key include-book paths, primary entry points).

Keep each field to 3-5 sentences.

--- Contents ---
{contents}

Respond with ONLY a valid JSON object, no markdown fences."""


# ─── Heuristic cell filter ──────────────────────────────────────────


def cell_is_trivial(cell: CellRecord) -> bool:
    """Return True if the cell is too trivial to summarize."""
    if cell.is_portcullis:
        return True

    content = (cell.code_text or "") + (cell.comment_text or "")
    content_stripped = content.strip()

    if not content_stripped:
        return True

    if len(content_stripped) < 40:
        return True

    # Common boilerplate forms.
    lower = content_stripped.lower()
    if lower.startswith("(include-book") or lower.startswith("(in-package"):
        return True
    if lower.startswith("(local (include-book"):
        return True

    return False


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
) -> list[str]:
    """Return all notebook source_file values, optionally filtered by prefix."""
    nb_coll = client.collections.get(COLLECTION_NOTEBOOK)
    sources: list[str] = []

    # Iterate all notebooks.
    for obj in nb_coll.iterator(
        return_properties=["source_file"],
    ):
        src = obj.properties.get("source_file", "")
        if source_dir and not src.startswith(source_dir):
            continue
        sources.append(src)

    sources.sort()
    return sources


# ─── LLM invocation (with caching) ──────────────────────────────────


async def _cached_llm_call(
    prompt: str,
    llm: ChatOpenAI,
    model: str,
    cache: LLMCache | None,
    sem: asyncio.Semaphore,
) -> str:
    """Invoke the LLM with SQLite caching and semaphore-limited concurrency."""
    # Check cache first (no semaphore needed for a local SQLite read).
    if cache is not None:
        cached = cache.get(prompt)
        if cached is not None:
            return cached

    async with sem:
        response = await llm.ainvoke(prompt)
        text = response.content if hasattr(response, "content") else str(response)

    # Store in cache.
    if cache is not None:
        cache.put(prompt, text, model)

    return text


def _parse_summary_json(raw: str) -> SummaryResult:
    """Parse a JSON summary response, tolerating markdown fences and <think> blocks."""
    text = raw.strip()
    # Strip <think>...</think> blocks (Qwen3 reasoning mode leakage).
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    # Strip markdown code fences if present.
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    try:
        d = json.loads(text)
    except json.JSONDecodeError:
        log.warning("Failed to parse LLM JSON: %.120s...", text)
        # Attempt to salvage: treat entire text as "what"
        return SummaryResult(what=raw.strip())

    return SummaryResult(
        what=d.get("what", ""),
        why=d.get("why", ""),
        how=d.get("how", ""),
    )


# ─── Phase 1: Cell Summaries ────────────────────────────────────────


def _build_cell_prompt(cell: CellRecord) -> str:
    """Build the LLM prompt for a single cell."""
    content = cell.code_text if cell.cell_type == "code" else cell.comment_text
    if not content:
        content = cell.code_text or cell.comment_text or "(empty cell)"

    symbols_section = ""
    if cell.symbol_names:
        parts = []
        for name, kind in zip(cell.symbol_names, cell.symbol_kinds):
            parts.append(f"  {name} ({kind})")
        symbols_section = "Symbols defined:\n" + "\n".join(parts)

    deps_section = ""
    if cell.dep_names:
        deps_section = "Dependencies: " + ", ".join(cell.dep_names[:30])
        if len(cell.dep_names) > 30:
            deps_section += f" ... and {len(cell.dep_names) - 30} more"

    return CELL_SUMMARY_PROMPT.format(
        package=cell.package or "ACL2",
        cell_type=cell.cell_type,
        symbols_section=symbols_section,
        deps_section=deps_section,
        content=content[:4000],  # Truncate very large cells
    )


async def summarize_cells(
    client: weaviate.WeaviateClient,
    notebook_sources: list[str],
    llm: ChatOpenAI,
    model: str,
    cache: LLMCache | None,
    sem: asyncio.Semaphore,
    batch_size: int,
    checkpoint: dict,
    dry_run: bool = False,
) -> dict[str, list[tuple[int, SummaryResult]]]:
    """Phase 1: Generate cell-level summaries.

    Returns a dict mapping notebook_source → list of (cell_index, SummaryResult).
    """
    summary_coll = client.collections.get(COLLECTION_SUMMARY) if not dry_run else None

    total_cells = 0
    total_qualifying = 0
    total_skipped_cp = 0
    total_cached = 0
    total_llm = 0
    all_results: dict[str, list[tuple[int, SummaryResult]]] = {}

    for nb_idx, nb_src in enumerate(notebook_sources, 1):
        cells = _fetch_cells_for_notebook(client, nb_src)
        qualifying = [c for c in cells if not cell_is_trivial(c)]
        total_cells += len(cells)
        total_qualifying += len(qualifying)

        if nb_idx % 50 == 0 or nb_idx == len(notebook_sources):
            log.info("  Cell scan: %d/%d notebooks, %d cells, %d qualifying",
                     nb_idx, len(notebook_sources), total_cells, total_qualifying)

        if dry_run:
            # Record qualifying count but skip LLM calls.
            all_results[nb_src] = [
                (c.cell_index, SummaryResult()) for c in qualifying
            ]
            continue

        # Build tasks for qualifying cells not yet checkpointed.
        tasks: list[tuple[CellRecord, str]] = []
        for cell in qualifying:
            ref_key = f"{nb_src}:{cell.cell_index}"
            if ref_key in checkpoint.get("cells", set()):
                total_skipped_cp += 1
                continue
            prompt = _build_cell_prompt(cell)
            tasks.append((cell, prompt))

        if not tasks:
            # All cells already done; reconstruct from Weaviate later if needed
            all_results[nb_src] = []
            continue

        # Run LLM calls concurrently.
        async def _do_cell(cell_rec: CellRecord, prompt: str) -> tuple[int, SummaryResult]:
            raw = await _cached_llm_call(prompt, llm, model, cache, sem)
            return cell_rec.cell_index, _parse_summary_json(raw)

        coros = [_do_cell(c, p) for c, p in tasks]
        results = await asyncio.gather(*coros, return_exceptions=True)

        nb_results: list[tuple[int, SummaryResult]] = []
        upsert_batch: list[dict] = []

        for i, res in enumerate(results):
            cell_rec = tasks[i][0]
            ref_key = f"{nb_src}:{cell_rec.cell_index}"

            if isinstance(res, Exception):
                log.error("Cell %s failed: %s", ref_key, res)
                continue

            cell_idx, summary = res
            nb_results.append((cell_idx, summary))

            # Track cache hit vs LLM call
            if cache and cache.get(tasks[i][1]) is not None:
                total_cached += 1
            else:
                total_llm += 1

            # Prepare Weaviate upsert.
            uuid = str(generate_uuid5(f"summary:cell:{ref_key}"))
            nb_uuid = str(generate_uuid5(f"notebook:{nb_src}"))
            cell_uuid = str(generate_uuid5(f"cell:{nb_src}:{cell_idx}"))

            upsert_batch.append({
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
                    "symbol_names": cell_rec.symbol_names,
                },
                "references": {
                    "sourceNotebook": nb_uuid,
                    "sourceCell": cell_uuid,
                },
            })

            # Mark in checkpoint.
            checkpoint.setdefault("cells", set()).add(ref_key)

        # Batch upsert to Weaviate.
        if upsert_batch and summary_coll is not None:
            with summary_coll.batch.fixed_size(batch_size=batch_size) as batch:
                for item in upsert_batch:
                    batch.add_object(
                        properties=item["properties"],
                        uuid=item["uuid"],
                        references=item["references"],
                    )

        all_results[nb_src] = nb_results

        if nb_idx % 10 == 0:
            log.info("  Cells: %d/%d notebooks processed, %d cached, %d LLM calls",
                     nb_idx, len(notebook_sources), total_cached, total_llm)

    log.info("Phase 1 complete: %d cells, %d qualifying, %d checkpointed, %d cached, %d LLM calls",
             total_cells, total_qualifying, total_skipped_cp, total_cached, total_llm)

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

        for chunk in chunks:
            cell_text = _format_cell_summaries(chunk)
            prompt = NOTEBOOK_CHUNK_PROMPT.format(
                source_file=nb_src,
                cell_summaries=cell_text,
            )
            raw = await _cached_llm_call(prompt, llm, model, cache, sem)
            intermediates.append(_parse_summary_json(raw))

        # Reduce: combine intermediates (or use directly if only one chunk).
        if len(intermediates) == 1:
            final = intermediates[0]
        else:
            section_text = _format_intermediates(intermediates)
            prompt = NOTEBOOK_REDUCE_PROMPT.format(
                source_file=nb_src,
                section_summaries=section_text,
            )
            raw = await _cached_llm_call(prompt, llm, model, cache, sem)
            final = _parse_summary_json(raw)

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
        prompt = DIRECTORY_REDUCE_PROMPT.format(
            directory=directory,
            contents="\n\n".join(contents_parts),
        )
        raw = await _cached_llm_call(prompt, llm, model, cache, sem)
        final = _parse_summary_json(raw)
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
        for key in ("cells", "notebooks", "directories"):
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
    total_qualifying = sum(len(v) for v in cell_summaries.values())

    # Directory count.
    all_dirs: set[str] = set()
    for src in notebook_sources:
        d = str(Path(src).parent)
        parts = Path(d).parts
        for i in range(len(parts)):
            all_dirs.add(str(Path(*parts[:i + 1])))

    print("\n=== DRY-RUN SUMMARY ===")
    print(f"Notebooks:            {len(notebook_sources)}")
    print(f"Qualifying cells:     {total_qualifying}")
    print(f"Directories:          {len(all_dirs)}")
    print(f"Est. LLM calls:")
    print(f"  Cell summaries:     {total_qualifying}")
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
    print(f"  Total (est.):       {total_qualifying + map_calls + reduce_calls + len(all_dirs)}")
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
        notebook_sources = _fetch_all_notebook_sources(client, args.source_dir)
        log.info("Found %d notebooks%s", len(notebook_sources),
                 f" under {args.source_dir}" if args.source_dir else "")

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
                args.batch_size, checkpoint, args.dry_run,
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
