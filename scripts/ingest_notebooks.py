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

Deterministic UUID5 keys make the import idempotent.  A JSON checkpoint
file makes it restartable — only notebooks that have changed or not yet
been processed are re-ingested.

Usage examples::

    # Dry-run: parse and report, don't write to Weaviate
    python scripts/ingest_notebooks.py --dry-run

    # Full import from scratch
    python scripts/ingest_notebooks.py --recreate

    # Incremental update (skips checkpointed notebooks)
    python scripts/ingest_notebooks.py

    # Include execution outputs (stdout, execute_result) — omitted by default
    python scripts/ingest_notebooks.py --include-outputs
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
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
DEFAULT_BATCH_SIZE = 200
CHECKPOINT_FILE = "scripts/.ingest_checkpoint.json"

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


def find_notebooks(source_dir: Path) -> list[Path]:
    """Recursively find all ``.ipynb`` files, skipping placeholders."""
    notebooks: list[Path] = []
    for root, _dirs, files in os.walk(source_dir):
        for name in sorted(files):
            if not name.endswith(".ipynb"):
                continue
            path = Path(root) / name
            # Quick check: skip placeholder notebooks.
            try:
                with open(path) as f:
                    # Only read enough to check metadata.placeholder.
                    raw = json.load(f)
                if raw.get("metadata", {}).get("placeholder"):
                    log.debug("Skipping placeholder: %s", path)
                    continue
            except (json.JSONDecodeError, OSError):
                log.warning("Skipping unreadable file: %s", path)
                continue
            notebooks.append(path)
    notebooks.sort()
    return notebooks


# ─── Checkpoint ──────────────────────────────────────────────────────


def _load_checkpoint(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_checkpoint(path: str, data: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# ─── Schema creation ────────────────────────────────────────────────


def ensure_collections(
    client: weaviate.WeaviateClient,
    ollama_url: str,
    embed_model: str,
    recreate: bool = False,
    include_outputs: bool = False,
) -> None:
    """Create (or recreate) the three ACL2 collections."""
    collections = [COLLECTION_NOTEBOOK, COLLECTION_CELL, COLLECTION_SYMBOL]

    if recreate:
        for name in collections:
            if client.collections.exists(name):
                log.info("Deleting collection %s", name)
                client.collections.delete(name)

    # ── ACL2Notebook ─────────────────────────────────────────────────
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

    # ── ACL2Cell ─────────────────────────────────────────────────────
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

    # ── ACL2Symbol ───────────────────────────────────────────────────
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
                ReferenceProperty(
                    name="definedInCell",
                    target_collection=COLLECTION_CELL,
                ),
            ],
        )
    else:
        log.info("Collection %s already exists, skipping", COLLECTION_SYMBOL)


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
    log.info("  Notebooks done: %d in %.1fs (%d failed)",
             len(notebooks), elapsed, len(failed))
    if failed:
        for obj in failed[:5]:
            log.error("  Failed notebook: %s", obj)


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
    log.info("  Symbols done: %d in %.1fs (%d failed)",
             len(global_syms), elapsed, len(failed))
    if failed:
        for obj in failed[:5]:
            log.error("  Failed symbol: %s", obj)


def _upsert_cells(
    client: weaviate.WeaviateClient,
    notebooks: list[NotebookInfo],
    batch_size: int,
    include_outputs: bool = False,
) -> int:
    """Phase 3: upsert ACL2Cell objects with notebook cross-ref."""
    collection = client.collections.get(COLLECTION_CELL)
    total_cells = sum(len(nb.cells) for nb in notebooks)
    log.info("Upserting %d cells across %d notebooks...", total_cells, len(notebooks))
    t0 = time.time()
    count = 0

    with collection.batch.fixed_size(batch_size=batch_size) as batch:
        for nb_info in notebooks:
            nb_uuid = _notebook_uuid(nb_info.source_file)
            for ci in nb_info.cells:
                props: dict = {
                    "notebook_source": nb_info.source_file,
                    "cell_index": ci.cell_index,
                    "cell_type": ci.cell_type,
                    "comment_text": ci.source if ci.cell_type == "markdown" else "",
                    "code_text": ci.source if ci.cell_type == "code" else "",
                    "package": ci.package,
                    "execution_count": ci.execution_count,
                    "provenance_start": ci.provenance_start,
                    "provenance_end": ci.provenance_end,
                    "is_portcullis": ci.is_portcullis,
                }
                if include_outputs:
                    props["stdout"] = ci.stdout
                    props["execute_result"] = ci.execute_result

                # Inline cross-ref to parent notebook.
                refs: dict = {"belongsToNotebook": nb_uuid}

                # Cross-ref to defined symbols.
                if ci.defined_symbols:
                    refs["definesSymbols"] = [
                        _symbol_uuid(qn) for qn in ci.defined_symbols
                    ]

                batch.add_object(
                    properties=props,
                    uuid=_cell_uuid(nb_info.source_file, ci.cell_index),
                    references=refs,
                )
                count += 1

    failed = collection.batch.failed_objects
    elapsed = time.time() - t0
    log.info("  Cells done: %d in %.1fs (%d failed)", count, elapsed, len(failed))
    if failed:
        for obj in failed[:5]:
            log.error("  Failed cell: %s", obj)
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
        "--recreate", action="store_true",
        help="Drop and recreate all collections",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Ignore checkpoint, reprocess all notebooks",
    )
    p.add_argument(
        "--include-outputs", action="store_true",
        help="Include stdout and execute_result in cells (omitted by default)",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Parse notebooks and report stats without writing to Weaviate",
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

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

    # ── Checkpoint filtering ─────────────────────────────────────────
    checkpoint = {} if args.force or args.recreate else _load_checkpoint(args.checkpoint)
    paths_to_process: list[Path] = []
    for p in notebook_paths:
        # Use mtime to decide staleness.
        mtime = p.stat().st_mtime
        cp_entry = checkpoint.get(str(p))
        if cp_entry and cp_entry.get("mtime", 0) >= mtime:
            log.debug("Skipping (checkpointed): %s", p)
            continue
        paths_to_process.append(p)

    if not paths_to_process and not args.force and not args.recreate:
        log.info("All %d notebooks already checkpointed, nothing to do "
                 "(use --force to reprocess)", len(notebook_paths))
        return 0

    log.info("Processing %d notebooks (%d skipped via checkpoint)",
             len(paths_to_process), len(notebook_paths) - len(paths_to_process))

    # ── Parse ────────────────────────────────────────────────────────
    log.info("Parsing notebooks...")
    t0 = time.time()
    parsed: list[NotebookInfo] = []
    errors = 0
    for i, path in enumerate(paths_to_process, 1):
        try:
            nb_info = parse_notebook(path, args.source_prefix, args.include_outputs)
            parsed.append(nb_info)
        except Exception as exc:
            log.error("Failed to parse %s: %s", path, exc)
            errors += 1
        if i % 100 == 0:
            log.info("  Parsed %d / %d ...", i, len(paths_to_process))

    elapsed = time.time() - t0
    log.info("Parsed %d notebooks in %.1fs (%d errors)", len(parsed), elapsed, errors)

    # ── Collect global symbols ───────────────────────────────────────
    global_syms = _collect_global_symbols(parsed)
    log.info("Unique symbols: %d", len(global_syms))

    # ── Dry run ──────────────────────────────────────────────────────
    if args.dry_run:
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

        # ── Phase 1: Notebooks ───────────────────────────────────────
        _upsert_notebooks(client, parsed, args.batch_size)

        # ── Phase 2: Symbols ─────────────────────────────────────────
        _upsert_symbols(client, global_syms, args.batch_size)

        # ── Phase 3: Cells ───────────────────────────────────────────
        _upsert_cells(client, parsed, args.batch_size, args.include_outputs)

        # ── Phase 4: Symbol cross-references ─────────────────────────
        _insert_symbol_cross_refs(client, global_syms, args.batch_size)

        # ── Update checkpoint ────────────────────────────────────────
        for nb_info in parsed:
            checkpoint[str(nb_info.path)] = {
                "source_file": nb_info.source_file,
                "notebook_uuid": _notebook_uuid(nb_info.source_file),
                "cell_count": len(nb_info.cells),
                "symbol_count": len(nb_info.symbols),
                "mtime": nb_info.path.stat().st_mtime,
                "timestamp": time.time(),
            }
        _save_checkpoint(args.checkpoint, checkpoint)
        log.info("Checkpoint saved to %s", args.checkpoint)

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
