#!/usr/bin/env python3
"""Ingest executed ACL2 Jupyter notebooks into Weaviate.

Walks .ipynb files under a source directory, parses notebook and cell
metadata (provenance, symbols, dependencies), and upserts structured
objects into three Weaviate v4 collections:

  * **ACL2Notebook** — one object per notebook (source path, portcullis, etc.)
  * **ACL2Cell**     — one object per cell with named vectors for
                       independent comment vs. code search
  * **ACL2Symbol**   — one object per unique PACKAGE::NAME with a
                       dependency graph via cross-references

Deterministic UUID5 keys make the import idempotent — every run
processes all notebooks and upserts everything, so transient failures
(e.g. Ollama vectorization hiccups) are self-healing on the next run.

Usage examples::

    # Dry-run: parse and report, don't write to Weaviate
    python scripts/ingest_notebooks.py --dry-run

    # Full import from scratch (drop and rebuild collections)
    python scripts/ingest_notebooks.py --recreate

    # Incremental upsert (idempotent, fills any gaps)
    python scripts/ingest_notebooks.py

    # Include execution outputs (stdout, execute_result) — omitted by default
    python scripts/ingest_notebooks.py --include-outputs
"""

from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import nbformat
import weaviate
from weaviate.classes.config import (
    Configure,
    DataType,
    Property,
    ReferenceProperty,
)
from weaviate.classes.aggregate import GroupByAggregate
from weaviate.util import generate_uuid5

# ─── Constants ───────────────────────────────────────────────────────

EVENTS_MIME = "application/vnd.acl2.events+json"

COLLECTION_NOTEBOOK = "ACL2Notebook"
COLLECTION_CELL = "ACL2Cell"
COLLECTION_SYMBOL = "ACL2Symbol"

# Symbol-kind precedence (lower index = more specific).
KIND_PRECEDENCE = [
    "function",
    "macro",
    "theorem",
    "constant",
    "stobj",
    "variable",
    "raw-function",
    "special-form",
    "unknown",
]

DEFAULT_SOURCE_DIR = "data/home/acl2"
DEFAULT_SOURCE_PREFIX = "/home/acl2/"
DEFAULT_WEAVIATE_HOST = "host.docker.internal"
DEFAULT_WEAVIATE_PORT = 8080
DEFAULT_WEAVIATE_GRPC_PORT = 50051
DEFAULT_OLLAMA_URL = "http://host.docker.internal:11434"
DEFAULT_EMBED_MODEL = "nomic-embed-text:latest"

# nomic-embed-text has an 8192-token context window (~4 chars/token).
# Text longer than this must be chunked and embedded manually.
MAX_VECTORIZER_CHARS = 1_000
DEFAULT_BATCH_SIZE = 200
DEFAULT_JOBS = min(multiprocessing.cpu_count(), 8)

log = logging.getLogger("ingest-notebooks")


# ─── Data classes ────────────────────────────────────────────────────


@dataclass
class SymbolInfo:
    """Aggregated information about a single ACL2 symbol."""

    name: str
    package: str
    qualified_name: str  # PACKAGE::NAME
    kind: str = "unknown"
    is_operator: bool = False
    # Cells (by UUID key) where this symbol is *defined*.
    defining_cell_keys: list[str] = field(default_factory=list)
    # Qualified names of symbols this one depends on.
    depends_on: list[str] = field(default_factory=list)


@dataclass
class CellInfo:
    """Parsed information for a single notebook cell."""

    cell_index: int
    cell_type: str  # code | markdown | raw
    source: str
    package: str = ""
    execution_count: int = -1
    provenance_start: int = -1
    provenance_end: int = -1
    is_portcullis: bool = False
    stdout: str = ""
    execute_result: str = ""
    # Qualified names of symbols *defined* in this cell.
    defined_symbols: list[str] = field(default_factory=list)


@dataclass
class NotebookInfo:
    """Parsed information for a single notebook."""

    path: Path
    source_file: str  # relative .lisp path (prefix stripped)
    stem: str
    is_bootstrap: bool
    source_type: str = ""
    acl2_version: str = ""
    portcullis: list[dict[str, str]] = field(default_factory=list)
    cells: list[CellInfo] = field(default_factory=list)
    # All symbols encountered in this notebook (by qualified_name).
    symbols: dict[str, SymbolInfo] = field(default_factory=dict)


# ─── Notebook parsing ────────────────────────────────────────────────


def _strip_source_prefix(raw_source_file: str, prefix: str) -> str:
    """Convert ``file:///home/acl2/books/foo.lisp`` → ``books/foo.lisp``."""
    path = urlparse(raw_source_file).path if raw_source_file.startswith("file://") else raw_source_file
    if path.startswith(prefix):
        path = path[len(prefix):]
    return path


def _more_specific_kind(existing: str, candidate: str) -> str:
    """Return the more specific of two symbol kinds."""
    try:
        ei = KIND_PRECEDENCE.index(existing)
    except ValueError:
        ei = len(KIND_PRECEDENCE)
    try:
        ci = KIND_PRECEDENCE.index(candidate)
    except ValueError:
        ci = len(KIND_PRECEDENCE)
    return existing if ei <= ci else candidate


def _extract_events_metadata(cell: nbformat.NotebookNode) -> dict | None:
    """Find the ``display_data`` output carrying ACL2 event metadata."""
    for output in getattr(cell, "outputs", []):
        if output.get("output_type") != "display_data":
            continue
        data = output.get("data", {})
        if EVENTS_MIME in data:
            return data[EVENTS_MIME]
    return None


def _extract_stdout(cell: nbformat.NotebookNode) -> str:
    """Concatenate all stream/stdout outputs."""
    parts: list[str] = []
    for output in getattr(cell, "outputs", []):
        if output.get("output_type") == "stream" and output.get("name") == "stdout":
            text = output.get("text", "")
            if isinstance(text, list):
                text = "".join(text)
            parts.append(text)
    return "".join(parts)


def _extract_execute_result(cell: nbformat.NotebookNode) -> str:
    """Extract text/plain from execute_result output."""
    for output in getattr(cell, "outputs", []):
        if output.get("output_type") == "execute_result":
            text = output.get("data", {}).get("text/plain", "")
            if isinstance(text, list):
                text = "".join(text)
            return text
    return ""


def parse_notebook(
    path: Path,
    source_prefix: str,
    include_outputs: bool = False,
) -> NotebookInfo:
    """Parse a single ``.ipynb`` file into a :class:`NotebookInfo`."""
    nb = nbformat.read(path, as_version=4)
    meta = nb.metadata

    # Notebook-level fields.
    raw_source = meta.get("source_file", "")
    source_file = _strip_source_prefix(raw_source, source_prefix)
    stem = Path(source_file).stem
    is_bootstrap = "books/" not in source_file

    source_type = ""
    boot = meta.get("acl2_boot_strap", {})
    if boot:
        source_type = boot.get("source_type", "")

    acl2_version = meta.get("language_info", {}).get("version", "")

    portcullis: list[dict[str, str]] = []
    raw_portcullis = meta.get("acl2_portcullis", {})
    for filename, content in raw_portcullis.items():
        portcullis.append({"filename": filename, "content": content})

    info = NotebookInfo(
        path=path,
        source_file=source_file,
        stem=stem,
        is_bootstrap=is_bootstrap,
        source_type=source_type,
        acl2_version=acl2_version,
        portcullis=portcullis,
    )

    # Per-cell parsing.
    for idx, cell in enumerate(nb.cells):
        prov = cell.metadata.get("provenance", {})

        ci = CellInfo(
            cell_index=idx,
            cell_type=cell.cell_type,
            source=cell.source or "",
            provenance_start=prov.get("start", -1),
            provenance_end=prov.get("end", -1),
            is_portcullis=bool(prov.get("portcullis", False)),
            execution_count=cell.get("execution_count") if cell.get("execution_count") is not None else -1,
        )

        # Outputs (optional).
        if include_outputs and cell.cell_type == "code":
            ci.stdout = _extract_stdout(cell)
            ci.execute_result = _extract_execute_result(cell)

        # ACL2 event metadata from display_data.
        evts = _extract_events_metadata(cell)
        if evts:
            ci.package = evts.get("package", "")

            # Symbols.
            for sym in evts.get("symbols", []):
                qn = f"{sym['package']}::{sym['name']}"
                kind = sym.get("kind", "unknown")
                is_op = bool(sym.get("operator", False))

                if qn in info.symbols:
                    existing = info.symbols[qn]
                    existing.kind = _more_specific_kind(existing.kind, kind)
                    existing.is_operator = existing.is_operator or is_op
                else:
                    info.symbols[qn] = SymbolInfo(
                        name=sym["name"],
                        package=sym["package"],
                        qualified_name=qn,
                        kind=kind,
                        is_operator=is_op,
                    )

            # Dependencies → defines which symbols and their edges.
            for defined_qn, ref_qns in evts.get("dependencies", {}).items():
                ci.defined_symbols.append(defined_qn)
                if defined_qn in info.symbols:
                    sym_info = info.symbols[defined_qn]
                else:
                    # Symbol might not have appeared in the symbols list
                    # (edge case); create a stub.
                    pkg, name = defined_qn.split("::", 1) if "::" in defined_qn else ("ACL2", defined_qn)
                    sym_info = SymbolInfo(
                        name=name, package=pkg, qualified_name=defined_qn,
                    )
                    info.symbols[defined_qn] = sym_info

                sym_info.depends_on = list(set(sym_info.depends_on) | set(ref_qns))

                cell_key = _cell_uuid_key(source_file, idx)
                if cell_key not in sym_info.defining_cell_keys:
                    sym_info.defining_cell_keys.append(cell_key)

        info.cells.append(ci)

    return info


# ─── UUID helpers ────────────────────────────────────────────────────


def _notebook_uuid(source_file: str) -> str:
    return str(generate_uuid5(f"notebook:{source_file}"))


def _cell_uuid_key(source_file: str, cell_index: int) -> str:
    return f"cell:{source_file}:{cell_index}"


def _cell_uuid(source_file: str, cell_index: int) -> str:
    return str(generate_uuid5(_cell_uuid_key(source_file, cell_index)))


def _symbol_uuid(qualified_name: str) -> str:
    return str(generate_uuid5(f"symbol:{qualified_name}"))


# ─── Discovery ───────────────────────────────────────────────────────


# Byte-level marker for placeholder notebooks — avoids a full JSON parse.
_PLACEHOLDER_MARKER = b'"placeholder": true'
# Read at most this many bytes for the placeholder check.  The metadata
# block is near the end of the file, but placeholder notebooks are tiny
# (< 1 KB), so reading the first 4 KB is sufficient.
_PLACEHOLDER_READ_SIZE = 4096


def _is_placeholder(path: Path) -> bool:
    """Fast check: is this a tiny placeholder notebook?"""
    try:
        size = path.stat().st_size
        if size > _PLACEHOLDER_READ_SIZE:
            return False  # real notebooks are larger
        with open(path, "rb") as f:
            head = f.read(_PLACEHOLDER_READ_SIZE)
        return _PLACEHOLDER_MARKER in head
    except OSError:
        return False


def find_notebooks(source_dir: Path) -> list[Path]:
    """Recursively find all ``.ipynb`` files, skipping placeholders."""
    notebooks: list[Path] = []
    for root, _dirs, files in os.walk(source_dir):
        for name in sorted(files):
            if not name.endswith(".ipynb"):
                continue
            path = Path(root) / name
            if _is_placeholder(path):
                continue
            notebooks.append(path)
    notebooks.sort()
    return notebooks


# ─── Batch retry ─────────────────────────────────────────────────────


def _retry_failed_objects(
    collection,
    failed: list,
    entity_name: str = "object",
    max_retries: int = 3,
    retry_delay: float = 2.0,
) -> int:
    """Retry failed batch objects individually with backoff.

    When Weaviate's ``text2vec-ollama`` vectorizer is temporarily
    unavailable (or overwhelmed), entire sub-batches of objects are
    silently dropped.  This function retries each failed object one at
    a time so that a transient Ollama hiccup doesn't permanently lose
    data.

    Returns the count of *permanently* failed objects (after all
    retries are exhausted).
    """
    if not failed:
        return 0

    log.warning("  %d %s(s) failed in batch — retrying individually",
                len(failed), entity_name)

    remaining = list(failed)
    for attempt in range(1, max_retries + 1):
        if not remaining:
            return 0

        if attempt > 1:
            delay = retry_delay * attempt
            log.info("  Retry attempt %d/%d (after %.0fs delay)...",
                     attempt, max_retries, delay)
            time.sleep(delay)

        still_failed = []
        for fail_obj in remaining:
            try:
                obj = fail_obj.object_
                insert_kwargs: dict = {
                    "properties": obj.properties,
                    "uuid": obj.uuid,
                }
                if obj.references:
                    insert_kwargs["references"] = obj.references
                collection.data.insert(**insert_kwargs)
            except Exception as exc:
                still_failed.append(fail_obj)
                log.debug("  Retry insert failed for %s: %s",
                          getattr(obj, 'uuid', '?'), exc)

        recovered = len(remaining) - len(still_failed)
        if recovered:
            log.info("  Attempt %d: recovered %d/%d %s(s)",
                     attempt, recovered, len(remaining), entity_name)
        remaining = still_failed

    if remaining:
        log.error("  %d %s(s) permanently failed after %d retries:",
                  len(remaining), entity_name, max_retries)
        for fail_obj in remaining:
            msg = getattr(fail_obj, 'message', str(fail_obj))
            uuid = getattr(getattr(fail_obj, 'object_', None), 'uuid', '?')
            log.error("    UUID %s: %s", uuid, msg)

    return len(remaining)


# ─── Schema creation ────────────────────────────────────────────────


def ensure_collections(
    client: weaviate.WeaviateClient,
    ollama_url: str,
    embed_model: str,
    recreate: bool = False,
    include_outputs: bool = False,
) -> None:
    """Create (or recreate) the three ACL2 collections.

    Creation order matters: Notebook first (no deps), then Symbol (self-ref
    only), then Cell (refs both Notebook and Symbol).  The Symbol→Cell
    back-reference (``definedInCell``) is added after Cell exists.
    """
    collections = [COLLECTION_NOTEBOOK, COLLECTION_SYMBOL, COLLECTION_CELL]

    if recreate:
        # Delete in reverse dependency order.
        for name in reversed(collections):
            if client.collections.exists(name):
                log.info("Deleting collection %s", name)
                client.collections.delete(name)

    # ── ACL2Notebook (no deps) ───────────────────────────────────────
    if not client.collections.exists(COLLECTION_NOTEBOOK):
        log.info("Creating collection %s", COLLECTION_NOTEBOOK)
        client.collections.create(
            COLLECTION_NOTEBOOK,
            vectorizer_config=Configure.Vectorizer.none(),
            properties=[
                Property(name="source_file", data_type=DataType.TEXT,
                         description="Relative .lisp path (prefix stripped)"),
                Property(name="stem", data_type=DataType.TEXT,
                         description="Filename stem"),
                Property(name="is_bootstrap", data_type=DataType.BOOL,
                         description="True for boot-strap source notebooks"),
                Property(name="source_type", data_type=DataType.TEXT,
                         description="acl2_source or raw_common_lisp"),
                Property(name="acl2_version", data_type=DataType.TEXT,
                         description="ACL2 version from language_info"),
                Property(name="cell_count", data_type=DataType.INT,
                         description="Total number of cells"),
                Property(name="code_cell_count", data_type=DataType.INT,
                         description="Number of code cells"),
                Property(
                    name="portcullis",
                    data_type=DataType.OBJECT_ARRAY,
                    description="ACL2 portcullis companion files",
                    nested_properties=[
                        Property(name="filename", data_type=DataType.TEXT),
                        Property(name="content", data_type=DataType.TEXT),
                    ],
                ),
            ],
        )
    else:
        log.info("Collection %s already exists, skipping", COLLECTION_NOTEBOOK)

    # ── ACL2Symbol (self-ref only; definedInCell added after Cell) ────
    if not client.collections.exists(COLLECTION_SYMBOL):
        log.info("Creating collection %s", COLLECTION_SYMBOL)
        client.collections.create(
            COLLECTION_SYMBOL,
            vectorizer_config=[
                Configure.NamedVectors.text2vec_ollama(
                    name="symbol_vector",
                    api_endpoint=ollama_url,
                    model=embed_model,
                    source_properties=["qualified_name"],
                ),
            ],
            properties=[
                Property(name="name", data_type=DataType.TEXT,
                         description="Symbol name"),
                Property(name="package", data_type=DataType.TEXT,
                         description="Symbol package",
                         skip_vectorization=True),
                Property(name="qualified_name", data_type=DataType.TEXT,
                         description="PACKAGE::NAME canonical form"),
                Property(name="kind", data_type=DataType.TEXT,
                         description="function/macro/theorem/constant/stobj/variable/etc.",
                         skip_vectorization=True),
                Property(name="is_operator", data_type=DataType.BOOL,
                         description="True if ever appeared in operator position"),
            ],
            references=[
                ReferenceProperty(
                    name="dependsOn",
                    target_collection=COLLECTION_SYMBOL,
                ),
            ],
        )
    else:
        log.info("Collection %s already exists, skipping", COLLECTION_SYMBOL)

    # ── ACL2Cell (refs Notebook + Symbol) ────────────────────────────
    if not client.collections.exists(COLLECTION_CELL):
        log.info("Creating collection %s", COLLECTION_CELL)

        cell_properties = [
            Property(name="notebook_source", data_type=DataType.TEXT,
                     description="Parent notebook source_file",
                     skip_vectorization=True),
            Property(name="cell_index", data_type=DataType.INT,
                     description="0-based position in notebook"),
            Property(name="cell_type", data_type=DataType.TEXT,
                     description="code, markdown, or raw",
                     skip_vectorization=True),
            Property(name="comment_text", data_type=DataType.TEXT,
                     description="Cell source (markdown cells only)"),
            Property(name="code_text", data_type=DataType.TEXT,
                     description="Cell source (code cells only)"),
            Property(name="package", data_type=DataType.TEXT,
                     description="ACL2 package after execution",
                     skip_vectorization=True),
            Property(name="execution_count", data_type=DataType.INT,
                     description="Jupyter execution count (-1 if unexecuted)"),
            Property(name="provenance_start", data_type=DataType.INT,
                     description="Byte offset in original source (-1 if absent)"),
            Property(name="provenance_end", data_type=DataType.INT,
                     description="Byte offset end (-1 if absent)"),
            Property(name="is_portcullis", data_type=DataType.BOOL,
                     description="True if injected portcullis cell"),
        ]

        if include_outputs:
            cell_properties.extend([
                Property(name="stdout", data_type=DataType.TEXT,
                         description="Concatenated stream outputs",
                         skip_vectorization=True),
                Property(name="execute_result", data_type=DataType.TEXT,
                         description="text/plain from execute_result",
                         skip_vectorization=True),
            ])

        client.collections.create(
            COLLECTION_CELL,
            vectorizer_config=[
                Configure.NamedVectors.text2vec_ollama(
                    name="comment_vector",
                    api_endpoint=ollama_url,
                    model=embed_model,
                    source_properties=["comment_text"],
                ),
                Configure.NamedVectors.text2vec_ollama(
                    name="code_vector",
                    api_endpoint=ollama_url,
                    model=embed_model,
                    source_properties=["code_text"],
                ),
            ],
            properties=cell_properties,
            references=[
                ReferenceProperty(
                    name="belongsToNotebook",
                    target_collection=COLLECTION_NOTEBOOK,
                ),
                ReferenceProperty(
                    name="definesSymbols",
                    target_collection=COLLECTION_SYMBOL,
                ),
            ],
        )
    else:
        log.info("Collection %s already exists, skipping", COLLECTION_CELL)

    # ── Add Symbol→Cell back-reference (now that Cell exists) ────────
    sym_collection = client.collections.get(COLLECTION_SYMBOL)
    existing_refs = {
        p.name for p in sym_collection.config.get().references
    }
    if "definedInCell" not in existing_refs:
        log.info("Adding definedInCell reference to %s", COLLECTION_SYMBOL)
        sym_collection.config.add_reference(
            ReferenceProperty(
                name="definedInCell",
                target_collection=COLLECTION_CELL,
            ),
        )


# ─── Upsert phases ──────────────────────────────────────────────────


def _upsert_notebooks(
    client: weaviate.WeaviateClient,
    notebooks: list[NotebookInfo],
    batch_size: int,
) -> None:
    """Phase 1: upsert ACL2Notebook objects."""
    collection = client.collections.get(COLLECTION_NOTEBOOK)
    log.info("Upserting %d notebooks...", len(notebooks))
    t0 = time.time()

    with collection.batch.fixed_size(batch_size=batch_size) as batch:
        for nb_info in notebooks:
            props = {
                "source_file": nb_info.source_file,
                "stem": nb_info.stem,
                "is_bootstrap": nb_info.is_bootstrap,
                "source_type": nb_info.source_type,
                "acl2_version": nb_info.acl2_version,
                "cell_count": len(nb_info.cells),
                "code_cell_count": sum(1 for c in nb_info.cells if c.cell_type == "code"),
                "portcullis": nb_info.portcullis,
            }
            batch.add_object(
                properties=props,
                uuid=_notebook_uuid(nb_info.source_file),
            )

    failed = collection.batch.failed_objects
    elapsed = time.time() - t0
    log.info("  Notebooks done: %d in %.1fs (%d failed in batch)",
             len(notebooks), elapsed, len(failed))
    _retry_failed_objects(collection, failed, "notebook")


def _collect_global_symbols(notebooks: list[NotebookInfo]) -> dict[str, SymbolInfo]:
    """Merge symbols across all notebooks, keeping the most specific kind."""
    global_syms: dict[str, SymbolInfo] = {}
    for nb_info in notebooks:
        for qn, sym in nb_info.symbols.items():
            if qn in global_syms:
                existing = global_syms[qn]
                existing.kind = _more_specific_kind(existing.kind, sym.kind)
                existing.is_operator = existing.is_operator or sym.is_operator
                # Merge defining cells and deps.
                for ck in sym.defining_cell_keys:
                    if ck not in existing.defining_cell_keys:
                        existing.defining_cell_keys.append(ck)
                existing.depends_on = list(set(existing.depends_on) | set(sym.depends_on))
            else:
                global_syms[qn] = SymbolInfo(
                    name=sym.name,
                    package=sym.package,
                    qualified_name=sym.qualified_name,
                    kind=sym.kind,
                    is_operator=sym.is_operator,
                    defining_cell_keys=list(sym.defining_cell_keys),
                    depends_on=list(sym.depends_on),
                )
    return global_syms


def _upsert_symbols(
    client: weaviate.WeaviateClient,
    global_syms: dict[str, SymbolInfo],
    batch_size: int,
) -> None:
    """Phase 2: upsert ACL2Symbol objects (no cross-refs yet)."""
    collection = client.collections.get(COLLECTION_SYMBOL)
    log.info("Upserting %d symbols...", len(global_syms))
    t0 = time.time()

    with collection.batch.fixed_size(batch_size=batch_size) as batch:
        for sym in global_syms.values():
            props = {
                "name": sym.name,
                "package": sym.package,
                "qualified_name": sym.qualified_name,
                "kind": sym.kind,
                "is_operator": sym.is_operator,
            }
            batch.add_object(
                properties=props,
                uuid=_symbol_uuid(sym.qualified_name),
            )

    failed = collection.batch.failed_objects
    elapsed = time.time() - t0
    log.info("  Symbols done: %d in %.1fs (%d failed in batch)",
             len(global_syms), elapsed, len(failed))
    _retry_failed_objects(collection, failed, "symbol")


def _embed_chunked(
    text: str,
    ollama_url: str,
    embed_model: str,
    chunk_size: int = MAX_VECTORIZER_CHARS,
) -> list[float]:
    """Split *text* into chunks, embed each via Ollama, return the mean vector."""
    import urllib.request

    chunks = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]
    all_vecs: list[list[float]] = []
    for chunk in chunks:
        body = json.dumps({"model": embed_model, "input": chunk}).encode()
        req = urllib.request.Request(
            f"{ollama_url}/api/embed",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
        all_vecs.append(data["embeddings"][0])

    if len(all_vecs) == 1:
        return all_vecs[0]
    # Average across chunks.
    dim = len(all_vecs[0])
    avg = [0.0] * dim
    for vec in all_vecs:
        for j in range(dim):
            avg[j] += vec[j]
    n = len(all_vecs)
    return [v / n for v in avg]


def _upsert_cells(
    client: weaviate.WeaviateClient,
    notebooks: list[NotebookInfo],
    batch_size: int,
    include_outputs: bool = False,
    ollama_url: str = DEFAULT_OLLAMA_URL,
    embed_model: str = DEFAULT_EMBED_MODEL,
    max_embed_chars: int = MAX_VECTORIZER_CHARS,
) -> int:
    """Phase 3: upsert ACL2Cell objects with notebook cross-ref."""
    collection = client.collections.get(COLLECTION_CELL)
    total_cells = sum(len(nb.cells) for nb in notebooks)
    log.info("Upserting %d cells across %d notebooks...", total_cells, len(notebooks))
    t0 = time.time()
    count = 0
    oversized: list[tuple[dict, str, dict, str]] = []  # (props, uuid, refs, vector_name)

    with collection.batch.fixed_size(batch_size=batch_size) as batch:
        for nb_info in notebooks:
            nb_uuid = _notebook_uuid(nb_info.source_file)
            for ci in nb_info.cells:
                comment = ci.source if ci.cell_type == "markdown" else ""
                code = ci.source if ci.cell_type == "code" else ""

                props: dict = {
                    "notebook_source": nb_info.source_file,
                    "cell_index": ci.cell_index,
                    "cell_type": ci.cell_type,
                    "comment_text": comment,
                    "code_text": code,
                    "package": ci.package,
                    "execution_count": ci.execution_count,
                    "provenance_start": ci.provenance_start,
                    "provenance_end": ci.provenance_end,
                    "is_portcullis": ci.is_portcullis,
                }
                if include_outputs:
                    props["stdout"] = ci.stdout
                    props["execute_result"] = ci.execute_result

                refs: dict = {"belongsToNotebook": nb_uuid}
                if ci.defined_symbols:
                    refs["definesSymbols"] = [
                        _symbol_uuid(qn) for qn in ci.defined_symbols
                    ]

                cell_uid = _cell_uuid(nb_info.source_file, ci.cell_index)

                # Check if text exceeds the vectorizer context limit.
                vec_text = comment or code
                if len(vec_text) > max_embed_chars:
                    vec_name = "comment_vector" if comment else "code_vector"
                    oversized.append((props, cell_uid, refs, vec_name))
                    log.info("  Deferring oversized cell %s:%d "
                             "(%d chars, will chunk-embed)",
                             nb_info.source_file, ci.cell_index,
                             len(vec_text))
                else:
                    batch.add_object(
                        properties=props,
                        uuid=cell_uid,
                        references=refs,
                    )
                count += 1

    failed = collection.batch.failed_objects
    elapsed = time.time() - t0
    log.info("  Cells done: %d in %.1fs (%d failed in batch, %d oversized)",
             count, elapsed, len(failed), len(oversized))
    _retry_failed_objects(collection, failed, "cell")

    # Insert oversized cells one-by-one with manually chunked embeddings.
    if oversized:
        log.info("  Embedding %d oversized cell(s) via chunked Ollama calls...",
                 len(oversized))
        for props, uid, refs, vec_name in oversized:
            text = props.get("comment_text") or props.get("code_text")
            try:
                vec = _embed_chunked(text, ollama_url, embed_model,
                                     chunk_size=max_embed_chars)
                # Provide explicit vector; the other named vector stays empty.
                other = "code_vector" if vec_name == "comment_vector" else "comment_vector"
                vecs = {vec_name: vec, other: [0.0] * len(vec)}
                if collection.data.exists(uid):
                    collection.data.replace(
                        properties=props, uuid=uid,
                        references=refs, vector=vecs,
                    )
                else:
                    collection.data.insert(
                        properties=props, uuid=uid,
                        references=refs, vector=vecs,
                    )
                log.info("    Upserted oversized cell %s:%d OK",
                         props["notebook_source"], props["cell_index"])
            except Exception as exc:
                log.error("    Failed to embed/insert oversized cell %s:%d: %s",
                          props["notebook_source"], props["cell_index"], exc)
    return count


def _insert_symbol_cross_refs(
    client: weaviate.WeaviateClient,
    global_syms: dict[str, SymbolInfo],
    batch_size: int,
) -> None:
    """Phase 4: insert dependsOn and definedInCell cross-references on symbols."""
    collection = client.collections.get(COLLECTION_SYMBOL)

    # Count total refs to insert.
    dep_count = sum(len(s.depends_on) for s in global_syms.values())
    cell_ref_count = sum(len(s.defining_cell_keys) for s in global_syms.values())
    log.info("Inserting symbol cross-refs: %d dependsOn, %d definedInCell...",
             dep_count, cell_ref_count)
    t0 = time.time()

    with collection.batch.fixed_size(batch_size=batch_size) as batch:
        for sym in global_syms.values():
            from_uuid = _symbol_uuid(sym.qualified_name)

            # dependsOn edges.
            for dep_qn in sym.depends_on:
                to_uuid = _symbol_uuid(dep_qn)
                batch.add_reference(
                    from_uuid=from_uuid,
                    from_property="dependsOn",
                    to=to_uuid,
                )

            # definedInCell edges.
            for cell_key in sym.defining_cell_keys:
                to_uuid = str(generate_uuid5(cell_key))
                batch.add_reference(
                    from_uuid=from_uuid,
                    from_property="definedInCell",
                    to=to_uuid,
                )

    failed = collection.batch.failed_references
    elapsed = time.time() - t0
    log.info("  Cross-refs done in %.1fs (%d failed)", elapsed, len(failed))
    if failed:
        for ref in failed[:5]:
            log.error("  Failed ref: %s", ref)


# ─── Repair mode ─────────────────────────────────────────────────────


def _find_notebooks_needing_repair(
    client: weaviate.WeaviateClient,
    parsed: list[NotebookInfo],
) -> list[NotebookInfo]:
    """Check each notebook's expected cell UUIDs against Weaviate and return those with missing cells."""
    cells_col = client.collections.get("ACL2Cell")
    needs_repair: list[NotebookInfo] = []

    for nb in parsed:
        missing = 0
        for i in range(len(nb.cells)):
            uid = _cell_uuid(nb.source_file, i)
            if not cells_col.data.exists(uid):
                missing += 1
        if missing:
            log.warning(
                "REPAIR: %s — %d/%d cells missing",
                nb.source_file, missing, len(nb.cells),
            )
            needs_repair.append(nb)

    log.info(
        "Repair scan: %d/%d notebooks need repair",
        len(needs_repair), len(parsed),
    )
    return needs_repair


# ─── Dry-run reporting ───────────────────────────────────────────────


def _dry_run_report(notebooks: list[NotebookInfo], global_syms: dict[str, SymbolInfo]) -> None:
    """Print a summary of what would be ingested."""
    total_cells = sum(len(nb.cells) for nb in notebooks)
    code_cells = sum(sum(1 for c in nb.cells if c.cell_type == "code") for nb in notebooks)
    md_cells = sum(sum(1 for c in nb.cells if c.cell_type == "markdown") for nb in notebooks)
    dep_edges = sum(len(s.depends_on) for s in global_syms.values())
    cell_edges = sum(len(s.defining_cell_keys) for s in global_syms.values())

    kinds: dict[str, int] = {}
    for s in global_syms.values():
        kinds[s.kind] = kinds.get(s.kind, 0) + 1

    print("\n=== DRY-RUN SUMMARY ===")
    print(f"Notebooks:      {len(notebooks)}")
    print(f"  bootstrap:    {sum(1 for nb in notebooks if nb.is_bootstrap)}")
    print(f"  books:        {sum(1 for nb in notebooks if not nb.is_bootstrap)}")
    print(f"  w/portcullis: {sum(1 for nb in notebooks if nb.portcullis)}")
    print(f"Cells:          {total_cells}")
    print(f"  code:         {code_cells}")
    print(f"  markdown:     {md_cells}")
    print(f"  raw:          {total_cells - code_cells - md_cells}")
    print(f"Symbols:        {len(global_syms)}")
    for kind in KIND_PRECEDENCE:
        if kind in kinds:
            print(f"  {kind:16s} {kinds[kind]}")
    print(f"Dependency edges: {dep_edges}")
    print(f"definedInCell edges: {cell_edges}")
    print()


# ─── CLI + main ──────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Ingest ACL2 notebooks into Weaviate",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--source-dir", default=DEFAULT_SOURCE_DIR,
        help=f"Root directory of .ipynb files (default: {DEFAULT_SOURCE_DIR})",
    )
    p.add_argument(
        "--source-prefix", default=DEFAULT_SOURCE_PREFIX,
        help=f"Prefix stripped from source_file URIs (default: {DEFAULT_SOURCE_PREFIX})",
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
        "--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
        help=f"Weaviate batch size (default: {DEFAULT_BATCH_SIZE})",
    )
    p.add_argument(
        "--max-embed-chars", type=int, default=MAX_VECTORIZER_CHARS,
        help=f"Max chars before chunked embedding (default: {MAX_VECTORIZER_CHARS})",
    )
    p.add_argument(
        "--recreate", action="store_true",
        help="Drop and recreate all collections",
    )
    p.add_argument(
        "--repair", action="store_true",
        help="Only re-ingest notebooks with missing cells in Weaviate",
    )
    p.add_argument(
        "--include-outputs", action="store_true",
        help="Include stdout and execute_result in cells (omitted by default)",
    )
    p.add_argument(
        "-j", "--jobs", type=int, default=DEFAULT_JOBS,
        help=f"Parallel workers for parsing (default: {DEFAULT_JOBS})",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Parse notebooks and report stats without writing to Weaviate",
    )
    p.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable debug logging",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Suppress per-request HTTP chatter from httpx/httpcore.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # ── Discovery ────────────────────────────────────────────────────
    source_dir = Path(args.source_dir)
    if not source_dir.is_dir():
        log.error("Source directory does not exist: %s", source_dir)
        return 1

    log.info("Scanning for notebooks in %s ...", source_dir)
    notebook_paths = find_notebooks(source_dir)
    log.info("Found %d notebooks", len(notebook_paths))

    if not notebook_paths:
        log.warning("No notebooks found, nothing to do")
        return 0

    log.info("Found %d notebooks", len(notebook_paths))

    # ── Parse (parallel) ─────────────────────────────────────────────
    paths_to_process = notebook_paths
    jobs = max(1, args.jobs)
    log.info("Parsing %d notebooks with %d workers...",
             len(paths_to_process), jobs)
    t0 = time.time()
    parsed: list[NotebookInfo] = []
    errors = 0

    if jobs == 1:
        # Single-process fast path (easier to debug).
        for i, path in enumerate(paths_to_process, 1):
            try:
                nb_info = parse_notebook(path, args.source_prefix,
                                         args.include_outputs)
                parsed.append(nb_info)
            except Exception as exc:
                log.error("Failed to parse %s: %s", path, exc)
                errors += 1
            if i % 500 == 0:
                log.info("  Parsed %d / %d ...", i, len(paths_to_process))
    else:
        # Parallel parsing with ProcessPoolExecutor.
        futures = {}
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            for path in paths_to_process:
                fut = pool.submit(parse_notebook, path,
                                  args.source_prefix, args.include_outputs)
                futures[fut] = path

            done_count = 0
            for fut in as_completed(futures):
                done_count += 1
                try:
                    nb_info = fut.result()
                    parsed.append(nb_info)
                except Exception as exc:
                    log.error("Failed to parse %s: %s",
                              futures[fut], exc)
                    errors += 1
                if done_count % 500 == 0:
                    log.info("  Parsed %d / %d ...",
                             done_count, len(paths_to_process))

    elapsed = time.time() - t0
    rate = len(parsed) / elapsed if elapsed > 0 else 0
    log.info("Parsed %d notebooks in %.1fs (%.0f/s, %d errors)",
             len(parsed), elapsed, rate, errors)

    # ── Dry run (no Weaviate needed) ───────────────────────────────
    if args.dry_run and not args.repair:
        global_syms = _collect_global_symbols(parsed)
        _dry_run_report(parsed, global_syms)
        return 0

    # ── Connect to Weaviate ──────────────────────────────────────────
    log.info("Connecting to Weaviate at %s:%d...", args.weaviate_host, args.port)
    client = weaviate.connect_to_local(
        host=args.weaviate_host,
        port=args.port,
        grpc_port=args.grpc_port,
    )

    try:
        if not client.is_ready():
            log.error("Weaviate is not ready at %s:%d", args.weaviate_host, args.port)
            return 1
        log.info("Connected to Weaviate (ready)")

        # ── Schema ───────────────────────────────────────────────────
        ensure_collections(client, args.ollama_url, args.embed_model,
                           recreate=args.recreate,
                           include_outputs=args.include_outputs)

        # ── Repair filtering ─────────────────────────────────────────
        if args.repair and not args.recreate:
            parsed = _find_notebooks_needing_repair(client, parsed)
            if not parsed:
                log.info("No notebooks need repair — all cell counts match")
                return 0

        # ── Dry run (after repair scan) ──────────────────────────────
        if args.dry_run:
            global_syms = _collect_global_symbols(parsed)
            _dry_run_report(parsed, global_syms)
            return 0

        # ── Collect global symbols ───────────────────────────────────
        global_syms = _collect_global_symbols(parsed)
        log.info("Unique symbols: %d", len(global_syms))

        # ── Phase 1: Notebooks ───────────────────────────────────────
        _upsert_notebooks(client, parsed, args.batch_size)

        # ── Phase 2: Symbols ─────────────────────────────────────────
        _upsert_symbols(client, global_syms, args.batch_size)

        # ── Phase 3: Cells ───────────────────────────────────────────
        _upsert_cells(client, parsed, args.batch_size, args.include_outputs,
                       ollama_url=args.ollama_url, embed_model=args.embed_model,
                       max_embed_chars=args.max_embed_chars)

        # ── Phase 4: Symbol cross-references ─────────────────────────
        _insert_symbol_cross_refs(client, global_syms, args.batch_size)

        # ── Summary ──────────────────────────────────────────────────
        total_cells = sum(len(nb.cells) for nb in parsed)
        dep_edges = sum(len(s.depends_on) for s in global_syms.values())
        log.info("Done! %d notebooks, %d cells, %d symbols, %d dependency edges",
                 len(parsed), total_cells, len(global_syms), dep_edges)

    finally:
        client.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
