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
import hashlib
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

import jinja2

import weaviate
from weaviate.classes.config import (
    Configure,
    DataType,
    Property,
    ReferenceProperty,
)
from weaviate.classes.query import Filter
from weaviate.util import generate_uuid5

from typing import Callable

from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
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
PROMPTS_DIR = Path(__file__).parent / "prompts"

# Maximum cell summaries per notebook chunk in the map step.
NOTEBOOK_CHUNK_SIZE = 20
DEFAULT_CONTEXT_SIZE = 8192

log = logging.getLogger("summarize-kg")

# ─── Summary version registry ───────────────────────────────────────
#
# Each entry maps a version label to the model it was produced with
# and the prompt template directory under scripts/prompts/.
# The version label is stored on every ACL2Summary object so that
# summaries from different models/prompts can coexist and be filtered.

SUMMARY_VERSIONS: dict[str, dict] = {
    "v1-qwen3-coder": {
        "model": "qwen/qwen3-coder-next",
        "prompts": "v1",
        "mode": "tools",
        "description": "Initial LM Studio run with qwen3-coder-next",
    },
    "v2-groq-gpt-oss": {
        "model": "openai/gpt-oss-120b",
        "prompts": "v3",
        "mode": "json_schema",
        "description": "Groq API with gpt-oss-120b, structured JSON output",
    },
}


def _load_prompt_templates(
    prompts_label: str,
) -> jinja2.Environment:
    """Load Jinja2 prompt templates from ``scripts/prompts/{label}/``."""
    template_dir = PROMPTS_DIR / prompts_label
    if not template_dir.is_dir():
        raise FileNotFoundError(
            f"Prompt template directory not found: {template_dir}"
        )
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(template_dir)),
        keep_trailing_newline=True,
        undefined=jinja2.StrictUndefined,
    )


def _render_prompt(
    jinja_env: jinja2.Environment | None,
    template_name: str,
    fallback_template: str,
    **kwargs,
) -> str:
    """Render a prompt from a Jinja template, falling back to str.format()."""
    if jinja_env is not None:
        tmpl = jinja_env.get_template(template_name)
        return tmpl.render(**kwargs)
    return fallback_template.format(**kwargs)


# ─── Topic context for targeted prompts ──────────────────────────────
#
# Maps directory prefixes to (topic_label, description, focus_guidance).
# The description tells the LLM what this area of the ACL2 library is about.
# The focus_guidance tells it what to emphasise in summaries — what details
# will be most useful for someone learning to use this library or writing
# teaching material about it.
#
# Lookup is longest-prefix-match, so "books/centaur/fty" beats "books/centaur".

TOPIC_CONTEXTS: list[tuple[str, str, str, str]] = [
    # ── FTY type system ──────────────────────────────────────────────
    ("books/centaur/fty",
     "FTY Fixtype Framework",
     "The FTY (fixtype) library is ACL2's primary type-definition framework. "
     "It provides macros like defprod (product types), deftagsum (tagged sum/union types), "
     "deflist (typed lists), defalist (typed alists), defoption (option/maybe types), "
     "deftypes (mutual recursion), and defflexsum. Each type gets automatic fixing functions, "
     "equivalence relations, accessor/constructor macros, and supporting theorems.",
     "Focus on: what type is being defined, the fix/equiv pattern it establishes, "
     "what fields/variants it has, what fixing function and equivalence relation are generated, "
     "and how other code should use these types. Note any defvisitor or bitstruct patterns."),

    # ── std/basic ────────────────────────────────────────────────────
    ("books/std/basic",
     "Standard Basic Types & Arithmetic Equivalences",
     "Fundamental type-fixing functions and arithmetic equivalences used pervasively "
     "in ACL2: nfix, ifix, pos-fix, realfix, rfix (rationals), bit types (bytep, nibblep), "
     "maybe-natp, mbt$, and arith-equivs (int-equiv, nat-equiv, bit-equiv). These establish "
     "the fix/equiv discipline that all modern ACL2 code follows.",
     "Focus on: what equivalence or fixing function is defined, what congruence rules it "
     "enables, and how it fits into the broader fix/equiv discipline. "
     "Highlight any :congruence or :fixing-function-related theorems."),

    # ── std/util — definition macros ─────────────────────────────────
    ("books/std/util",
     "Standard Utility Macros (define, b*, defenum, etc.)",
     "The std/util library provides the most-used definition macros in modern ACL2: "
     "define (enhanced defun with guards, returns specs, and xdoc), "
     "b* (structured binding with pattern matching), defrule (enhanced defthm), "
     "defines (mutual recursion), deflist, defalist, defprojection, defaggregate, "
     "defenum, defval, defconsts, defarbrec, and defmapping. "
     "These macros are essential for writing idiomatic ACL2 code.",
     "Focus on: what macro is being defined or demonstrated, its syntax and options, "
     "what code it generates (recognizers, fixers, theorems), and usage patterns. "
     "For tests files, emphasise the usage examples as teaching material."),

    # ── std/lists ────────────────────────────────────────────────────
    ("books/std/lists",
     "Standard List Operations Library",
     "Theorems about built-in and extended list operations: append, nth, nthcdr, take, "
     "rev, member, remove, subsetp, no-duplicatesp, list-fix, prefixp, suffixp, "
     "flatten, set-difference, intersection, union, etc. Establishes list-equiv "
     "congruences and provides rewrite rules for compositional reasoning about lists.",
     "Focus on: what list operations are covered, key rewrite rules and their "
     "directions, any congruence rules, and practical lemma patterns for "
     "reasoning about list-manipulating functions."),

    # ── std/alists ───────────────────────────────────────────────────
    ("books/std/alists",
     "Standard Alist (Association List) Library",
     "Theorems about association list operations: assoc, put-assoc, remove-assoc, "
     "strip-cars, strip-cdrs, alist-keys, alist-vals, hons-assoc-equal (fast alists), "
     "alist-fix, alist-equiv, and alist map operations. Essential for reasoning about "
     "key-value data structures in ACL2.",
     "Focus on: what alist operation is formalised, the difference between regular "
     "alists and fast alists (hons-based), key lemma patterns, and compatibility theorems."),

    # ── std/osets ────────────────────────────────────────────────────
    ("books/std/osets",
     "Ordered Sets (Osets) Library",
     "Finite ordered sets with a canonical representation (elements sorted by <<). "
     "Provides in, insert, delete, union, intersect, difference, cardinality, "
     "subset, mergesort, quantification (defquant), and set-equiv. Uses pick-a-point "
     "proof strategy for membership reasoning.",
     "Focus on: what set operation or theorem is defined, the pick-a-point strategy, "
     "computed hints for set reasoning, quantification patterns, and how osets "
     "differ from list-based set operations."),

    # ── std/strings ──────────────────────────────────────────────────
    ("books/std/strings",
     "Standard String Processing Library",
     "String operations: concatenation (cat, str::cat), case conversion, "
     "character classification (char-kinds), numeric parsing (decimal, hex, octal, binary), "
     "base64, string searching, and abbreviations. Provides both executable functions "
     "and reasoning support.",
     "Focus on: what string operation is provided, its executable behaviour, "
     "and key theorems for reasoning about strings and character lists."),

    # ── std/io ───────────────────────────────────────────────────────
    ("books/std/io",
     "Standard I/O Library",
     "File I/O operations: read-file-characters, read-file-lines, read-file-bytes, "
     "read-file-objects, print-objects, serialization, read-string, and channel management. "
     "Bridges ACL2's logical world with actual file system operations.",
     "Focus on: what I/O operation is defined, its guard obligations, state threading, "
     "and how it relates to ACL2's logical file model."),

    # ── std/stobjs ───────────────────────────────────────────────────
    ("books/std/stobjs",
     "Standard Stobj (Single-Threaded Objects) Library",
     "Utilities for defining and reasoning about stobjs: typed arrays (1d-arr, 2d-arr), "
     "hash tables (def-hash), stobj cloning, nested stobjs, abstract stobjs (nicestobj), "
     "and updater-independence reasoning. Stobjs provide mutable state with logical soundness.",
     "Focus on: what stobj pattern or utility is defined, how it maintains the "
     "single-threaded discipline, the abstract-vs-concrete stobj correspondence, "
     "and updater independence proofs."),

    # ── std/typed-lists ──────────────────────────────────────────────
    ("books/std/typed-lists",
     "Typed List Recognizers",
     "Recognisers and theorems for homogeneous lists: nat-listp, integer-listp, "
     "string-listp, symbol-listp, boolean-listp, character-listp, "
     "signed-byte-listp, unsigned-byte-listp, etc.",
     "Focus on: what typed-list recognizer is defined, its relationship to the "
     "element predicate, and forwarding/rewriting theorems."),

    # ── std/bitsets ──────────────────────────────────────────────────
    ("books/std/bitsets",
     "Bitset Operations Library",
     "Efficient set representations using bitmasks and bignum extraction. "
     "Provides bitset-insert, bitset-member, bitset-union, bitset-intersect, "
     "bitset-difference, and sparse bitset variants (sbitsets).",
     "Focus on: the bitset representation, operation semantics, "
     "correspondence with logical set operations, and performance considerations."),

    # ── ordinals ─────────────────────────────────────────────────────
    ("books/ordinals",
     "Ordinal Arithmetic for Termination Proofs",
     "Ordinal arithmetic (addition, multiplication, exponentiation) and well-foundedness "
     "proofs for ACL2's termination checker. Every recursive function in ACL2 must have "
     "a measure that decreases in the ordinal ordering <o. Also covers lexicographic "
     "orderings for multi-argument measures.",
     "Focus on: what ordinal operation or theorem is proved, how it relates to "
     "termination proofs, measure functions, and lexicographic ordering patterns."),

    # ── textbook ─────────────────────────────────────────────────────
    ("books/textbook",
     "ACL2 Textbook Exercises & Solutions",
     "Worked exercises from 'Computer-Aided Reasoning: An Approach' by Kaufmann, "
     "Manolios, and Moore. Covers chapters 3-11: function definitions, recursion, "
     "induction, logic-mode vs program-mode, sorting algorithms (insertion sort, "
     "mergesort, quicksort), tautology checking, compression, finite sets, "
     "and encapsulation.",
     "Focus on: the exercise being solved, the proof strategy used (induction scheme, "
     "lemma decomposition, hint usage), common pitfalls, and how the solution "
     "demonstrates ACL2 proof methodology."),

    # ── proofstyles ──────────────────────────────────────────────────
    ("books/proofstyles",
     "Proof Style Comparisons (Clock vs Invariant)",
     "Systematic comparison of proof methodologies for program verification: "
     "clock functions vs invariant-based proofs, partial vs total correctness, "
     "soundness vs completeness. Demonstrates how to convert between proof styles "
     "and when each is appropriate.",
     "Focus on: which proof style is being used or compared, the key structural "
     "differences between clock and invariant approaches, the conversion technique, "
     "and guidance on when to prefer which style."),

    # ── demos/marktoberdorf ──────────────────────────────────────────
    ("books/demos/marktoberdorf-08",
     "Marktoberdorf 2008 ACL2 Lectures (J S Moore)",
     "J Strother Moore's Marktoberdorf Summer School 2008 tutorial on ACL2. "
     "Five lectures covering: ACL2 fundamentals, the M1 machine model (JVM-like), "
     "operational semantics, fast execution verification, and compiler correctness proof.",
     "Focus on: the pedagogical progression — what concept each lecture/file teaches, "
     "the M1 machine model, how to define and verify bytecode programs, "
     "and the clock-function proof methodology."),

    # ── demos (general) ──────────────────────────────────────────────
    ("books/demos",
     "ACL2 Demonstrations & Examples",
     "Practical demonstrations of ACL2 features and techniques: "
     "BRR (break-rewrite-rule) debugging, abstract stobjs (defabsstobj), "
     "congruent stobjs, loop verification (loop-primer), floating point reasoning, "
     "generalized equivalences (geneqv), GL bit-blasting, and more.",
     "Focus on: what ACL2 feature or technique is demonstrated, the complete "
     "usage pattern shown, key takeaways for practitioners, and any debugging "
     "or investigation methodology (especially for BRR demos)."),

    # ── hints ────────────────────────────────────────────────────────
    ("books/hints",
     "ACL2 Hint Mechanism",
     "The hint system guides ACL2's prover: :use (apply a lemma), :in-theory "
     "(enable/disable rules), :expand (force expansion), :cases (case splitting), "
     ":induct (select induction scheme), computed hints (programmatic hints), "
     "consider hints, subgoal identification, and hint merging.",
     "Focus on: what hint type or pattern is demonstrated, when to use it, "
     "common pitfalls, and the interaction between different hint types. "
     "For basic-tests.lisp, catalogue all hint types shown."),
    ("books/kestrel/hints",
     "Kestrel Hint Utilities",
     "Hint manipulation utilities: combining hints, removing hints from events, "
     "renaming hints, case-split helpers, and goal specification.",
     "Focus on: what hint utility is provided, its API, and practical use cases."),

    # ── clause-processors ────────────────────────────────────────────
    ("books/clause-processors",
     "Clause Processors (Proof Extensions)",
     "Clause processors extend ACL2's prover with custom proof procedures. "
     "Includes: basic clause processor examples, SAT solving (SULFA), "
     "BV (bit-vector) reasoning, constant propagation, let abstraction, "
     "just-expand, generalization, induction, and equality reasoning CPs.",
     "Focus on: what clause processor is defined, its correctness proof pattern "
     "(the evaluator requirement), how to attach it via :clause-processor hints, "
     "and what class of goals it handles."),

    # ── arithmetic-5 ─────────────────────────────────────────────────
    ("books/arithmetic-5",
     "Arithmetic-5 Reasoning Library",
     "Automatic reasoning about arithmetic: integer type reasoning, normalization, "
     "simplification, exponentiation, linear and non-linear arithmetic. "
     "Provides rewrite rules and meta-functions for arithmetic expressions.",
     "Focus on: what arithmetic reasoning capability is provided, "
     "key rewrite rules and their trigger patterns, the theory structure "
     "(which rules are enabled by default), and the meta-function approach."),

    # ── data-structures ──────────────────────────────────────────────
    ("books/data-structures",
     "Classic Data Structure Libraries",
     "Older but comprehensive data structure libraries: lists (with 251 theorems), "
     "alists, arrays, records, memory models (memtree), deflist, defalist, "
     "structures, sets, and no-duplicates reasoning.",
     "Focus on: what data structure is formalised, its representation, "
     "key operations and their properties, and how it compares to the "
     "newer std/ equivalents."),

    # ── kestrel/apt ──────────────────────────────────────────────────
    ("books/kestrel/apt",
     "APT (Automated Program Transformations)",
     "Verified program transformations: restrict (domain restriction), parteval "
     "(partial evaluation), casesplit, isodata/expdata (data representation change), "
     "finite-difference, lift-iso, propagate-iso, schemalg (algorithmic schemas), "
     "rename-params, rename-calls, and drop-irrelevant-params.",
     "Focus on: what transformation is defined, its input/output contract, "
     "the correctness theorem it proves, how to invoke it, and what class of "
     "programs it applies to. For test files, emphasise the usage examples."),

    # ── codewalker ───────────────────────────────────────────────────
    ("books/projects/codewalker",
     "Codewalker — Symbolic Execution & Decompilation",
     "Symbolic execution framework for the M1 JVM-like machine model. "
     "Provides codewalker (the core engine), terminatricks (termination analysis), "
     "M1 machine model v3, and demo verifications of bytecode programs "
     "(factorial, count-up). Also used for x86 ISA proofs.",
     "Focus on: the symbolic execution methodology, how codewalker works "
     "(state abstraction, path exploration), the M1 machine model, "
     "clock-function proofs, and how to verify a new bytecode program."),

    # ── abnf ─────────────────────────────────────────────────────────
    ("books/projects/abnf",
     "ABNF Grammar Parsing & Verification",
     "Verified ABNF grammar parsing: grammar definition (defgrammar), "
     "tree operations (deftreeops), parser construction (defdefparse), "
     "executable parser, and correctness verification. Includes grammars for "
     "URI, HTTP, IMAP, SMTP, IMF (email), and PDF.",
     "Focus on: the grammar definition pattern, how parsing is formalised, "
     "the parser correctness theorem structure, and the defdefparse macro "
     "for building verified parsers."),

    # ── coi ──────────────────────────────────────────────────────────
    ("books/coi",
     "COI (Community of Interest) Libraries",
     "Foundational libraries: bags (multisets with extensive metatheoretic reasoning), "
     "alist equivalences (keyquiv, bindequiv, subkeyquiv), adviser (proof advising), "
     "paths, records, and more. Heavy use of meta-functions and bind-free rules.",
     "Focus on: what abstraction is formalised, the meta-reasoning approach "
     "(meta-functions, bind-free), the algebraic properties proved, "
     "and practical usage patterns."),

    # ── centaur/misc ─────────────────────────────────────────────────
    ("books/centaur/misc",
     "Centaur Miscellaneous Utilities",
     "Utility libraries from the Centaur hardware verification team: "
     "bound-rewriter, context-sensitive rewriting, DAG/DFS algorithms, "
     "fast alists, evaluator metatheorems, graph operations, and more.",
     "Focus on: what utility is provided, its API and intended use case, "
     "and any novel proof techniques (especially meta-theorems and context rewriting)."),

    # ── milawa ───────────────────────────────────────────────────────
    ("books/projects/milawa",
     "Milawa — Verified Theorem Prover",
     "Milawa is a verified theorem prover for a simple first-order logic, "
     "built and verified in ACL2. Includes the core logic, proof checker, "
     "rewrite tactics, and bootstrapping process.",
     "Focus on: the logical foundation, proof checker structure, how tactics "
     "are implemented and verified, and the bootstrapping methodology."),

    # ── x86isa ───────────────────────────────────────────────────────
    ("books/projects/x86isa",
     "x86 ISA Formal Model",
     "Formal model of the x86 instruction set architecture. Includes "
     "instruction semantics, memory model, paging, and proof infrastructure "
     "for verifying x86 machine code programs.",
     "Focus on: what x86 feature is modelled, the state representation, "
     "instruction semantics, and proof methodology for machine code."),

    # ── Broad fallbacks (less specific) ──────────────────────────────
    ("books/centaur",
     "Centaur Hardware Verification Libraries",
     "Libraries developed by the Centaur Technology hardware verification team. "
     "Includes FTY types, VL (Verilog), SV (SystemVerilog), GL/FGL "
     "(bit-level symbolic execution), bitops, and misc utilities.",
     "Focus on: the hardware verification methodology, bit-level reasoning, "
     "and how the library supports industrial-scale verification."),

    ("books/kestrel",
     "Kestrel Institute Libraries",
     "Libraries from Kestrel Institute for program synthesis and transformation. "
     "Includes APT transformations, event macros, Java/C code generation, "
     "executable parsers, and general utilities.",
     "Focus on: the program transformation approach, synthesis methodology, "
     "and how the tools support verified software development."),

    ("books/projects",
     "ACL2 Community Projects",
     "Verification projects contributed by the ACL2 community: formal models of "
     "ISAs (x86, ARM), verified parsers (ABNF), theorem provers (Milawa), "
     "symbolic execution (codewalker), and more.",
     "Focus on: what is being verified, the proof architecture, key lemmas, "
     "and the practical verification methodology."),

    ("books/std",
     "ACL2 Standard Libraries",
     "The standard libraries providing foundational data structures, types, "
     "and utilities for ACL2 development.",
     "Focus on: what operation or type is formalised, key rewrite rules, "
     "and idiomatic usage patterns."),

    ("books/workshops",
     "ACL2 Workshop Papers & Supporting Code",
     "Code accompanying papers presented at ACL2 workshops. Contains "
     "diverse verification examples, novel techniques, and tool demonstrations.",
     "Focus on: what technique or tool is demonstrated, the verification "
     "approach, and novel proof strategies."),

    ("books/system",
     "ACL2 System Extensions & Utilities",
     "Extensions to the ACL2 system itself: verified termination, "
     "guard verification for system functions, and meta-level utilities.",
     "Focus on: what system function is being verified or extended, "
     "and the guard/termination proof strategy."),

    ("books",
     "ACL2 Community Books",
     "The ACL2 community books library — a large collection of verified "
     "libraries, tools, and projects contributed by ACL2 users.",
     "Focus on: what the code defines or proves, its purpose in the broader "
     "library, and practical usage guidance."),
]


def _get_topic_context(source_file: str) -> tuple[str, str, str]:
    """Return (topic_label, description, focus_guidance) for a source file.

    Uses longest-prefix matching against TOPIC_CONTEXTS.
    Returns empty strings if no match.
    """
    best_prefix = ""
    best_match: tuple[str, str, str] = ("", "", "")

    for prefix, label, desc, focus in TOPIC_CONTEXTS:
        if source_file.startswith(prefix) and len(prefix) > len(best_prefix):
            best_prefix = prefix
            best_match = (label, desc, focus)

    return best_match


def _format_topic_section(source_file: str) -> str:
    """Format the topic context section for injection into prompts.

    Returns an empty string if no topic context is available.
    NOTE: Currently disabled while tuning cell-level coverage.
    """
    return ""
    # label, desc, focus = _get_topic_context(source_file)
    # if not label:
    #     return ""
    #
    # return (
    #     f"\n--- Library Context ---\n"
    #     f"Topic area: {label}\n"
    #     f"{desc}\n"
    #     f"{focus}\n"
    # )


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
    symbol: str = ""


# ─── Tool-calling models for batched cell summaries ─────────────────


class ReportWhat(BaseModel):
    """Report what a notebook cell does — its functional behaviour."""

    cell_number: int = Field(description="The 0-based cell index")
    summary: str = Field(
        description="What this cell defines, proves, or configures.  "
        "1-3 precise sentences covering one distinct idea."
    )
    symbol: str | None = Field(
        default=None,
        description="The specific symbol (from the Defines: header) "
        "this idea pertains to, if applicable.",
    )


class ReportWhy(BaseModel):
    """Report the purpose or goal of a notebook cell."""

    cell_number: int = Field(description="The 0-based cell index")
    summary: str = Field(
        description="Why this matters — the goal, problem solved, or proof "
        "obligation discharged.  1-3 sentences for one distinct idea."
    )
    symbol: str | None = Field(
        default=None,
        description="The specific symbol (from the Defines: header) "
        "this idea pertains to, if applicable.",
    )


class ReportHow(BaseModel):
    """Report usage instructions for a notebook cell."""

    cell_number: int = Field(description="The 0-based cell index")
    summary: str = Field(
        description="Usage instructions — how to call/invoke/include.  "
        "1-3 sentences for one distinct idea."
    )
    symbol: str | None = Field(
        default=None,
        description="The specific symbol (from the Defines: header) "
        "this idea pertains to, if applicable.",
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
        description="Description of overall functionality.  2-4 sentences.  "
        "Start with the single most important capability."
    )


class SummaryWhy(BaseModel):
    """Report the purpose or goal of a notebook or directory."""

    summary: str = Field(
        description="The broader purpose or goal.  2-4 sentences.  "
        "Start with the primary purpose."
    )


class SummaryHow(BaseModel):
    """Report usage instructions for a notebook or directory."""

    summary: str = Field(
        description="Usage instructions — include-book path, key functions/macros, "
        "and typical invocation patterns.  2-4 sentences."
    )


SUMMARY_TOOLS = [SummaryWhat, SummaryWhy, SummaryHow]


# ─── JSON-schema response models (for with_structured_output) ───────


class CellIdea(BaseModel):
    """A single atomic idea extracted from a notebook cell."""

    cell_number: int = Field(description="The 0-based cell index")
    what: str = Field(
        description="What this cell defines, proves, or configures.  "
        "1-3 precise sentences covering one distinct idea."
    )
    why: str = Field(
        default="",
        description="Why this matters — the goal, problem solved, or proof "
        "obligation discharged.  1-3 sentences.",
    )
    how: str = Field(
        default="",
        description="Usage instructions — how to call/invoke/include.  "
        "1-3 sentences.  Omit if not applicable.",
    )
    symbol: str = Field(
        default="",
        description="The specific symbol (from the Defines: header) "
        "this idea pertains to, if applicable.",
    )


class CellBatchResponse(BaseModel):
    """Structured response for a batch of notebook cells."""

    ideas: list[CellIdea] = Field(
        description="One or more atomic ideas extracted from the cells.  "
        "Each cell may have multiple ideas."
    )
    continuation: str = Field(
        default="",
        description="Key context from this batch needed to understand "
        "subsequent cells.  Empty if this is the last batch.",
    )


class NotebookSummaryResponse(BaseModel):
    """Structured what/why/how summary for a notebook or directory."""

    what: str = Field(
        description="Description of overall functionality.  2-4 sentences.  "
        "Start with the single most important capability."
    )
    why: str = Field(
        default="",
        description="The broader purpose or goal.  2-4 sentences.  "
        "Start with the primary purpose.",
    )
    how: str = Field(
        default="",
        description="Usage instructions — include-book path, key functions/macros, "
        "and typical invocation patterns.  2-4 sentences.",
    )


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

    def get(self, prompt: str, model: str = "") -> str | None:
        """Return cached response or None."""
        conn = self._connect()
        h = str(generate_uuid5(f"{model}\n{prompt}"))
        row = conn.execute(
            "SELECT response FROM llm_cache WHERE prompt_hash = ?", (h,)
        ).fetchone()
        return row[0] if row else None

    def put(self, prompt: str, response: str, model: str) -> None:
        """Store an LLM response."""
        conn = self._connect()
        h = str(generate_uuid5(f"{model}\n{prompt}"))
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

    def delete_matching(self, pattern: str) -> int:
        """Delete cache entries whose prompt_text contains *pattern*."""
        conn = self._connect()
        cur = conn.execute(
            "DELETE FROM llm_cache WHERE prompt_text LIKE ?",
            (f"%{pattern}%",),
        )
        conn.commit()
        return cur.rowcount


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
You are an expert in ACL2 (A Computational Logic for Applicative Common Lisp) \
and formal verification.  Below are cells from the notebook ``{source_file}``.
{topic_section}
You MUST process EVERY cell.

For EACH cell:

1. Identify ALL meaningful elements, including:
   - Definitions (functions, theorems, macros, events)
   - Comments with technical content
   - Preconditions, postconditions, invariants
   - Proof strategies or hints
   - Imports / includes with semantic significance
   - Configuration affecting semantics
   - Design rationale statements
   - Assumptions or constraints
   - Usage instructions
   - Warnings or limitations

2. Decompose the cell into ATOMIC IDEAS.
   - An atomic idea is the smallest independently meaningful technical \
claim, definition, or rationale.
   - If a paragraph contains 3 claims, extract 3 ideas.
   - If a function defines behavior and constraints, extract separate ideas.

3. For EACH atomic idea:
   - Call report_what
   - Call report_why (if inferable)
   - Call report_how (if mechanism/implementation exists)

4. Do NOT merge unrelated ideas.
5. Do NOT skip ideas.
6. Do NOT summarize the whole cell as one idea unless it truly contains only one.

7. Only omit report_how if:
   - The idea contains no implementation or mechanism detail.

8. Only use the cell number in the cell_number field.  Do NOT include the cell number in the summary text.

9. Only provide descriptive information.  Do NOT include boilerplate phrases like "This cell...", "This is markdown that...", or "The following code...".

Minimum expectation:
- Any non-trivial function definition => at least 2 ideas
- Any theorem with proof hints => at least 2 ideas
- Any technical comment paragraph => 1 idea per technical claim
- Any include/import => 1 idea explaining its purpose

If you extract fewer than 2 ideas from a non-trivial cell, re-evaluate and decompose further.

Use the cell_number argument (0-based cell index) to identify each cell.
Use correct ACL2 terminology — name specific functions, macros, theorems, and rules rather than speaking generically.
{continuation_section}
--- Cells ---
{cells_text}"""

NOTEBOOK_CHUNK_PROMPT = """\
You are summarizing a group of ACL2 notebook cells.  Below are individual \
cell summaries from the same notebook file ``{source_file}``.
{topic_section}
Call each tool once to summarize this group:
- summary_what: What this group of definitions/theorems accomplishes. \
Name the key functions, macros, types, or theorems defined.
- summary_why: The broader purpose — what capability this enables or \
what verification goal it supports.
- summary_how: How to use the facilities defined here — include-book paths, \
key entry points, typical invocation patterns.

Keep each to 2-4 sentences.  Be specific: name things rather than \
speaking generically about "various definitions".

--- Cell Summaries ---
{cell_summaries}"""

NOTEBOOK_REDUCE_PROMPT = """\
You are summarizing an ACL2 notebook file ``{source_file}``.
Below are intermediate summaries from different sections of this notebook.
{topic_section}
Call each tool once to produce the combined summary:
- summary_what: What this file defines or proves, overall. \
Name the primary functions, macros, types, or theorem families.
- summary_why: The purpose of this file in the library — what it enables \
for downstream users or what verification obligation it fulfils.
- summary_how: How to use it — the include-book path, key entry-point \
functions/macros, and any prerequisites or companion books.

Keep each to 2-4 sentences.  Be concrete and name specific things.

--- Section Summaries ---
{section_summaries}"""

DIRECTORY_REDUCE_PROMPT = """\
You are summarizing the ACL2 library directory ``{directory}``.
Below are summaries of the notebooks and subdirectories it contains.
{topic_section}
Call each tool once to produce the combined summary:
- summary_what: What this directory provides — the main capabilities, \
types, theorems, or tools defined.
- summary_why: Its purpose in the broader ACL2 library — what problems \
it solves or what user needs it addresses.
- summary_how: How to use it — the primary include-book path (usually top.lisp), \
key entry-point macros/functions, and important sub-libraries.

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

    # Filter out portcullis cells — they are boilerplate guard events
    # (e.g. in-package, certify-book) that don't need summarization.
    n_before = len(cells)
    cells = [c for c in cells if not c.is_portcullis]
    n_dropped = n_before - len(cells)
    if n_dropped:
        log.debug(
            "%s: dropped %d portcullis cells (%d remaining)",
            cells[0].notebook_source if cells else notebook_source,
            n_dropped, len(cells),
        )

    return cells


# Sentinel value: user explicitly asked for the repo root (no books/ prefix).
_ROOT_SENTINEL = "__ROOT__"


def _normalize_source_dir(source_dir: str | None) -> str | None:
    """Normalize a --source-dir value to match stored source_file paths.

    Stored source_file values are relative, e.g. ``books/std/lists/list-defuns.lisp``.
    Users may pass filesystem paths like ``data/home/acl2/books/std`` or absolute
    paths.  This function strips known prefixes so the value lines up.

    Returns ``_ROOT_SENTINEL`` when the user pointed at the ACL2 root itself
    (e.g. ``data/home/acl2``) — meaning "only top-level files, not under books/".
    """
    if not source_dir:
        return None
    sd = source_dir.rstrip("/")
    # Strip absolute workspace prefix if present
    workspace = "/workspaces/pup/"
    if sd.startswith(workspace):
        sd = sd[len(workspace):]
    # Strip data/home/acl2 filesystem prefix (mirrors ingest source_prefix)
    for prefix in ("data/home/acl2/", "data/home/acl2"):
        if sd.startswith(prefix):
            sd = sd[len(prefix.rstrip("/")) + 1:] if sd != prefix.rstrip("/") else ""
            break
    sd = sd.strip("/")
    if not sd:
        # User pointed at ACL2 root (e.g. data/home/acl2) — return sentinel
        return _ROOT_SENTINEL
    return sd


def _fetch_all_notebook_sources(
    client: weaviate.WeaviateClient,
    source_dir: str | None = None,
    no_recurse: bool = False,
    notebook_list: list[str] | None = None,
) -> list[str]:
    """Return all notebook source_file values, optionally filtered.

    If *notebook_list* is provided, only return notebooks whose source_file
    is in that explicit set (takes precedence over *source_dir*).
    If *no_recurse* is True and *source_dir* is set, only return notebooks
    whose parent directory exactly matches *source_dir* (no subdirectories).
    """
    source_dir = _normalize_source_dir(source_dir)
    log.debug("_fetch_all_notebook_sources: normalized source_dir=%r, no_recurse=%s, notebook_list=%d entries",
             source_dir, no_recurse, len(notebook_list) if notebook_list else 0)

    # Build an allowlist set when an explicit notebook list is provided
    allowset: set[str] | None = set(notebook_list) if notebook_list else None

    nb_coll = client.collections.get(COLLECTION_NOTEBOOK)
    sources: list[str] = []

    for obj in nb_coll.iterator(
        return_properties=["source_file"],
    ):
        src = obj.properties.get("source_file", "")
        # Explicit allowlist takes precedence
        if allowset is not None:
            if src not in allowset:
                continue
            sources.append(src)
            continue
        if source_dir == _ROOT_SENTINEL:
            # Root-level files only: no "/" in path means top-level.
            if no_recurse:
                if "/" in src:
                    continue
            # Without --no-recurse on root → all notebooks (no filter)
        elif source_dir:
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


def _strip_markdown_fences(text: str) -> str:
    """Strip wrapping markdown code fences from comment text.

    The notebook converter wraps Lisp comments in bare ``` fences.
    These are formatting artifacts — we don't want the LLM to see them.

    TODO: Fix at ingest time — store only the raw comment text from the
    .lisp file using the provenance comment_span, so fences never enter
    the KG in the first place.
    """
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        # Remove opening fence line and closing fence
        lines = text.splitlines(keepends=True)
        # Find first ``` line
        start = 0
        for i, line in enumerate(lines):
            if line.strip().startswith("```"):
                start = i + 1
                break
        # Find last ``` line
        end = len(lines)
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip() == "```":
                end = i
                break
        if start < end:
            return "".join(lines[start:end])
    return text


def _build_batch_cells_text(cells: list[CellRecord]) -> str:
    """Format a batch of cells for the batch prompt."""
    parts: list[str] = []
    for c in cells:
        content = c.code_text if c.cell_type == "code" else c.comment_text
        if not content:
            content = c.code_text or c.comment_text or "(empty)"
        # Strip markdown fences from comment cells.
        if c.cell_type != "code" and content:
            content = _strip_markdown_fences(content)
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
    tool_response_fn: (
        Callable[[list[dict], list[dict]], list[str]] | None
    ) = None,
    max_turns: int = 10,
) -> tuple[list[dict], bool]:
    """Invoke the LLM with tool calling and SQLite caching.

    When *tool_response_fn* is provided, runs a multi-turn loop:
    send the prompt, collect tool calls, respond with ``ToolMessage``s
    containing per-call feedback from *tool_response_fn*, and continue
    until the model sends no more tool calls or *max_turns* is reached.

    *tool_response_fn(all_tool_calls, turn_tool_calls)* receives the
    cumulative list of all tool calls and the current turn's calls,
    and must return a list of strings — one response per turn call.

    When *tool_response_fn* is ``None``, behaves as single-shot
    (one ``ainvoke``, collect tool calls, done).

    Returns ``(tool_calls, was_cached)`` where *tool_calls* is a list
    of dicts: ``[{"name": ..., "args": {...}}, ...]``.
    """
    if cache is not None:
        cached = cache.get(prompt, model)
        if cached is not None:
            return json.loads(cached), True

    all_tool_calls: list[dict] = []
    messages: list = [HumanMessage(content=prompt)]

    # Log the initial prompt (truncate for readability).
    prompt_preview = prompt[:500] + "..." if len(prompt) > 500 else prompt
    log.debug(">>> HumanMessage (%d chars):\n%s", len(prompt), prompt_preview)

    stall_count = 0  # consecutive turns with no new unique tool calls

    async with sem:
        for turn in range(max_turns):
            t0 = time.monotonic()
            response = await llm_with_tools.ainvoke(messages)
            elapsed = time.monotonic() - t0

            if not response.tool_calls:
                # Log any trailing text content from the model.
                tail = getattr(response, "content", "") or ""
                log.debug(
                    "<<< AIMessage turn %d (%.1fs, 0 tool calls)%s",
                    turn + 1, elapsed,
                    f": {tail[:200]}" if tail.strip() else "",
                )
                break  # Model is done — no more tool calls.

            # Collect this turn's tool calls.
            turn_calls = [
                {"name": tc["name"], "args": tc["args"]}
                for tc in response.tool_calls
            ]

            # Detect stalling: if every call in this turn is an
            # exact duplicate of something already collected, the
            # model is stuck in a loop.  Break after 2 consecutive
            # stalled turns to be generous.
            existing = {
                (tc["name"], json.dumps(tc["args"], sort_keys=True))
                for tc in all_tool_calls
            }
            new_calls = [
                tc for tc in turn_calls
                if (tc["name"], json.dumps(tc["args"], sort_keys=True))
                not in existing
            ]
            if not new_calls:
                stall_count += 1
                if stall_count >= 2:
                    log.debug(
                        "<<< AIMessage turn %d (%.1fs): stalled %d turns "
                        "with only duplicate calls — stopping multi-turn",
                        turn + 1, elapsed, stall_count,
                    )
                    break
            else:
                stall_count = 0

            all_tool_calls.extend(turn_calls)

            # Log every tool call from this turn.
            log.debug(
                "<<< AIMessage turn %d (%.1fs, %d tool calls, %d total):",
                turn + 1, elapsed, len(turn_calls), len(all_tool_calls),
            )
            for tc in turn_calls:
                args_str = json.dumps(tc["args"], ensure_ascii=False)
                if len(args_str) > 300:
                    args_str = args_str[:300] + "..."
                log.debug("    tool: %s(%s)", tc["name"], args_str)

            # Single-shot mode: one turn only.
            if tool_response_fn is None:
                break

            # Multi-turn: append AIMessage + ToolMessages so the
            # model can continue.
            messages.append(response)  # The AIMessage with tool_calls.

            responses = tool_response_fn(all_tool_calls, turn_calls)
            for i, tc in enumerate(response.tool_calls):
                content = (
                    responses[i] if i < len(responses) else "Recorded."
                )
                messages.append(ToolMessage(
                    content=content,
                    tool_call_id=tc["id"],
                ))

            for i, r in enumerate(responses):
                log.debug(">>> ToolMessage[%d]: %s", i, r)

    if cache is not None:
        cache.put(prompt, json.dumps(all_tool_calls), model)

    return all_tool_calls, False


async def _cached_json_call(
    prompt: str,
    structured_llm,
    model: str,
    cache: LLMCache | None,
    sem: asyncio.Semaphore,
) -> tuple[BaseModel, bool]:
    """Invoke an LLM with structured output (json_schema) and caching.

    Unlike ``_cached_tool_call``, this is single-shot — one ``ainvoke``,
    one response.  The model returns a validated Pydantic object directly
    because ``with_structured_output`` handles parsing.

    Returns ``(response_model, was_cached)`` where *response_model* is
    a Pydantic ``BaseModel`` instance (e.g. CellBatchResponse or
    NotebookSummaryResponse).
    """
    if cache is not None:
        cached = cache.get(prompt, model)
        if cached is not None:
            # Reconstruct the Pydantic model from cached JSON.
            # We store the raw JSON dict; the caller's schema is used
            # to reconstruct via the structured_llm's bound schema.
            data = json.loads(cached)
            # structured_llm is llm.with_structured_output(SomeModel),
            # but we can't easily get SomeModel back.  Store as dict
            # and return a thin wrapper — callers accept BaseModel.
            # Instead, cache the class name alongside data so we can
            # reconstruct.  For simplicity, return the dict and let
            # callers detect dict vs BaseModel.
            return data, True

    prompt_preview = prompt[:500] + "..." if len(prompt) > 500 else prompt
    log.debug(">>> HumanMessage [json_schema] (%d chars):\n%s",
              len(prompt), prompt_preview)

    async with sem:
        t0 = time.monotonic()
        response = await structured_llm.ainvoke(prompt)
        elapsed = time.monotonic() - t0

    log.debug("<<< Structured response (%.1fs): %s",
              elapsed, type(response).__name__)

    # Cache the response as JSON.
    if cache is not None:
        if isinstance(response, BaseModel):
            cache.put(prompt, response.model_dump_json(), model)
        else:
            cache.put(prompt, json.dumps(response), model)

    return response, False


def _tool_calls_to_summaries(
    tool_calls: list[dict],
) -> tuple[dict[int, list[SummaryResult]], str]:
    """Parse tool-call dicts into per-cell lists of SummaryResults.

    The LLM may call report_what/report_why/report_how multiple times
    for the same cell to cover distinct ideas.  A new ``ReportWhat``
    for a cell that already has a non-empty ``what`` starts a fresh
    SummaryResult (ordering-based grouping).

    Exact-duplicate tool calls (same cell_number, name, and text) are
    silently dropped so they do not create duplicate summary objects.

    Returns ``(summaries, continuation)`` where *summaries* maps
    each cell index to a list of SummaryResult objects.
    """
    summaries: dict[int, list[SummaryResult]] = {}
    continuation = ""
    seen: set[tuple] = set()  # (cell_number, name, text)

    for tc in tool_calls:
        name = tc.get("name", "")
        args = tc.get("args", {})

        if name == "ContinuationContext":
            continuation = args.get("context", "")
            continue

        cell_num = args.get("cell_number")
        if cell_num is None:
            continue

        text = args.get("summary", "")

        # Skip exact duplicates.
        if name in ("ReportWhat", "ReportWhy", "ReportHow") and text:
            key = (cell_num, name, text)
            if key in seen:
                continue
            seen.add(key)

        if cell_num not in summaries:
            summaries[cell_num] = [SummaryResult()]

        current = summaries[cell_num][-1]

        symbol = args.get("symbol") or ""

        if name == "ReportWhat":
            # A new ReportWhat when we already have one → new idea.
            if current.what:
                summaries[cell_num].append(SummaryResult())
                current = summaries[cell_num][-1]
            current.what = text
            if symbol:
                current.symbol = symbol
        elif name == "ReportWhy":
            if current.why:
                summaries[cell_num].append(SummaryResult())
                current = summaries[cell_num][-1]
            current.why = text
            if symbol and not current.symbol:
                current.symbol = symbol
        elif name == "ReportHow":
            if current.how:
                summaries[cell_num].append(SummaryResult())
                current = summaries[cell_num][-1]
            current.how = text
            if symbol and not current.symbol:
                current.symbol = symbol

    return summaries, continuation


def _json_response_to_summaries(
    response: CellBatchResponse | dict,
) -> tuple[dict[int, list[SummaryResult]], str]:
    """Convert a CellBatchResponse into per-cell SummaryResult lists.

    Mirrors ``_tool_calls_to_summaries`` but for json_schema responses.
    Each ``CellIdea`` becomes one ``SummaryResult``.
    """
    if isinstance(response, dict):
        response = CellBatchResponse(**response)

    summaries: dict[int, list[SummaryResult]] = {}
    for idea in response.ideas:
        sr = SummaryResult(
            what=idea.what,
            why=idea.why,
            how=idea.how,
            symbol=idea.symbol,
        )
        summaries.setdefault(idea.cell_number, []).append(sr)

    return summaries, response.continuation


def _json_response_to_result(
    response: NotebookSummaryResponse | dict,
) -> SummaryResult:
    """Convert a NotebookSummaryResponse into a SummaryResult.

    Mirrors ``_summary_tools_to_result`` but for json_schema responses.
    """
    if isinstance(response, dict):
        response = NotebookSummaryResponse(**response)

    return SummaryResult(
        what=response.what,
        why=response.why,
        how=response.how,
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
    context_size: int = DEFAULT_CONTEXT_SIZE,
    dry_run: bool = False,
    jinja_env: jinja2.Environment | None = None,
    version: str = "",
    mode: str = "tools",
) -> dict[str, list[tuple[int, int, SummaryResult]]]:
    """Phase 1: Generate cell-level summaries via batched LLM calls.

    Cells are grouped into batches that fit within *context_size* bytes.

    When *mode* is ``"tools"`` (default), the LLM is invoked with tool
    calling (multi-turn with feedback).  When *mode* is
    ``"json_schema"``, the LLM returns a structured JSON response
    validated against ``CellBatchResponse``.

    Returns a dict mapping notebook source to a list of
    ``(cell_index, summary_index, SummaryResult)`` tuples.  A cell may
    have multiple summaries (one per distinct idea the LLM identified).
    """
    if mode == "json_schema":
        structured_llm = (
            llm.with_structured_output(CellBatchResponse,
                                       method="json_schema", strict=True)
            if llm else None
        )
        llm_with_tools = None
    else:
        llm_with_tools = llm.bind_tools(CELL_TOOLS) if llm else None
        structured_llm = None
    summary_coll = client.collections.get(COLLECTION_SUMMARY) if not dry_run else None

    total_cells = 0
    total_batches = 0
    total_skipped_cp = 0
    total_cached = 0
    total_llm = 0
    all_results: dict[str, list[tuple[int, int, SummaryResult]]] = {}

    # Build a set of notebook sources whose ALL cells are already done.
    # We can skip fetching cells entirely for these.
    done_cells = checkpoint.get("cells", set())
    done_batches = checkpoint.get("cell_batches", set())

    for nb_idx, nb_src in enumerate(notebook_sources, 1):
        # Fast notebook-level skip: if we have a notebook checkpoint marker
        # we can avoid the expensive Weaviate cell fetch entirely.
        if f"nb_done:{nb_src}" in done_batches:
            total_skipped_cp += 1
            if nb_idx % 500 == 0:
                log.info("  Skipping: %d/%d notebooks (already done)",
                         nb_idx, len(notebook_sources))
            continue

        cells = _fetch_cells_for_notebook(client, nb_src)
        total_cells += len(cells)

        if nb_idx % 50 == 0 or nb_idx == len(notebook_sources):
            log.info(
                "  Cell scan: %d/%d notebooks, %d cells, %d batches so far",
                nb_idx, len(notebook_sources), total_cells, total_batches,
            )

        if dry_run:
            all_results[nb_src] = [
                (c.cell_index, 0, SummaryResult()) for c in cells
            ]
            continue

        # Split cells into context-sized batches.
        batches = _batch_cells_by_size(cells, context_size)
        total_batches += len(batches)

        nb_results: list[tuple[int, int, SummaryResult]] = []
        continuation_context = ""

        # Compute notebook-wide cell counts by category for progress.
        n_symbol_cells = sum(
            1 for c in cells if c.symbol_names
        )
        n_code_cells = sum(
            1 for c in cells if c.cell_type == "code" and not c.symbol_names
        )
        n_comment_cells = sum(
            1 for c in cells if c.cell_type == "markdown"
        )
        summarized_cell_indices: set[int] = set()

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

            # Build progress section showing coverage so far.
            done_sym = sum(
                1 for c in cells
                if c.symbol_names and c.cell_index in summarized_cell_indices
            )
            done_code = sum(
                1 for c in cells
                if c.cell_type == "code" and not c.symbol_names
                and c.cell_index in summarized_cell_indices
            )
            done_cmt = sum(
                1 for c in cells
                if c.cell_type == "markdown"
                and c.cell_index in summarized_cell_indices
            )
            progress_section = (
                f"\n--- Progress ---\n"
                f"Cells with at least one summary: "
                f"{done_sym} of {n_symbol_cells} cells with symbol definitions, "
                f"{done_code} of {n_code_cells} code cells, "
                f"{done_cmt} of {n_comment_cells} comment cells.\n"
            )

            prompt = _render_prompt(
                jinja_env,
                "cell_batch.j2",
                BATCH_CELL_PROMPT,
                source_file=nb_src,
                topic_section=_format_topic_section(nb_src),
                continuation_section=cont_section,
                cells_text=cells_text,
            )

            # Build a progress callback for multi-turn tool calling.
            # It computes which cells in this batch have been covered
            # by the tool calls so far, so the model sees real-time
            # coverage in the ToolMessage responses.
            batch_indices = {c.cell_index for c in batch_cells}

            def _make_progress_fn(
                _cells=cells,
                _batch_cells=batch_cells,
                _batch_indices=batch_indices,
            ):
                # Compute batch-local category counts (denominators).
                b_sym = sum(1 for c in _batch_cells if c.symbol_names)
                b_code = sum(
                    1 for c in _batch_cells
                    if c.cell_type == "code" and not c.symbol_names
                )
                b_cmt = sum(
                    1 for c in _batch_cells if c.cell_type == "markdown"
                )
                _min_idx = min(_batch_indices)
                _max_idx = max(_batch_indices)
                _seen: set[tuple] = set()  # (cell_number, name, text)

                # Build a mapping from cell_index → set of defined
                # symbols (bare and qualified) for symbol validation.
                _cell_syms: dict[int, set[str]] = {}
                for _c in _batch_cells:
                    if _c.symbol_names:
                        names: set[str] = set()
                        for sn in _c.symbol_names:
                            names.add(sn.upper())
                            # Also allow the bare name (without package prefix).
                            if "::" in sn:
                                names.add(sn.split("::", 1)[1].upper())
                        _cell_syms[_c.cell_index] = names

                def fn(
                    all_tcs: list[dict],
                    turn_tcs: list[dict],
                ) -> list[str]:
                    # Build per-call responses for this turn.
                    responses: list[str] = []
                    for tc in turn_tcs:
                        name = tc.get("name", "")
                        args = tc.get("args", {})
                        cn = args.get("cell_number")
                        text = args.get("summary", "")

                        if cn is not None and cn not in _batch_indices:
                            responses.append(
                                f"ERROR: cell_number {cn} is out of "
                                f"range. Valid cell numbers in this "
                                f"batch are {_min_idx}\u2013{_max_idx}. "
                                f"Only use cell numbers that appear "
                                f"in the cells provided."
                            )
                        elif name in ("ReportWhat", "ReportWhy",
                                      "ReportHow") and text:
                            key = (cn, name, text)
                            if key in _seen:
                                responses.append(
                                    f"Duplicate: cell {cn} {name} "
                                    f"already recorded with the same "
                                    f"text. Do not repeat summaries."
                                )
                            else:
                                _seen.add(key)
                                msg = f"Recorded cell {cn}."
                                # Validate symbol if supplied.
                                sym = args.get("symbol")
                                if sym and cn in _cell_syms:
                                    sym_up = sym.upper()
                                    if sym_up not in _cell_syms[cn]:
                                        msg += (
                                            f" Note: symbol '{sym}' does "
                                            f"not match any defined symbol "
                                            f"in this cell."
                                        )
                                responses.append(msg)
                        else:
                            responses.append(
                                f"Recorded cell {cn}."
                                if cn is not None
                                else "Recorded."
                            )

                    # Compute coverage for the summary on the
                    # last response.
                    covered = set()
                    for tc in all_tcs:
                        cn = tc.get("args", {}).get("cell_number")
                        if cn is not None and cn in _batch_indices:
                            covered.add(cn)
                    d_sym = sum(
                        1 for c in _batch_cells
                        if c.symbol_names and c.cell_index in covered
                    )
                    d_code = sum(
                        1 for c in _batch_cells
                        if c.cell_type == "code" and not c.symbol_names
                        and c.cell_index in covered
                    )
                    d_cmt = sum(
                        1 for c in _batch_cells
                        if c.cell_type == "markdown"
                        and c.cell_index in covered
                    )
                    batch_done = len(covered)
                    batch_total = len(_batch_indices)
                    progress = (
                        f"{batch_done}/{batch_total} cells "
                        f"(range {_min_idx}\u2013{_max_idx}) covered. "
                        f"Breakdown: {d_sym}/{b_sym} symbol, "
                        f"{d_code}/{b_code} code, {d_cmt}/{b_cmt} comment. "
                        f"Continue with remaining cells."
                    )
                    # Append progress to the last response.
                    if responses:
                        responses[-1] += f" {progress}"
                    return responses
                return fn

            if mode == "json_schema":
                # Single-shot structured JSON call — no multi-turn.
                response, was_cached = await _cached_json_call(
                    prompt, structured_llm, model, cache, sem,
                )
                if was_cached:
                    total_cached += 1
                else:
                    total_llm += 1
                summaries, continuation_context = (
                    _json_response_to_summaries(response)
                )
            else:
                tool_calls, was_cached = await _cached_tool_call(
                    prompt, llm_with_tools, model, cache, sem,
                    tool_response_fn=_make_progress_fn(),
                )
                if was_cached:
                    total_cached += 1
                else:
                    total_llm += 1
                summaries, continuation_context = _tool_calls_to_summaries(
                    tool_calls,
                )

            # Filter out hallucinated cell indices the LLM may have
            # invented (i.e. indices not present in this batch).
            bad_indices = set(summaries.keys()) - batch_indices
            if bad_indices:
                log.warning(
                    "  %s: LLM returned %d invalid cell indices "
                    "(not in batch): %s — skipping",
                    nb_src, len(bad_indices), sorted(bad_indices),
                )
                for bi in bad_indices:
                    del summaries[bi]

            # Track which cells received summaries for progress.
            summarized_cell_indices.update(summaries.keys())

            # Merge into notebook results.
            for cell_idx, sum_list in summaries.items():
                for si, summary in enumerate(sum_list):
                    nb_results.append((cell_idx, si, summary))

            # Upsert to Weaviate.
            if summary_coll is not None and summaries:
                upsert_items: list[dict] = []
                for cell_idx, sum_list in summaries.items():
                    cell_rec = next(
                        (c for c in batch_cells if c.cell_index == cell_idx),
                        None,
                    )
                    if cell_rec is None:
                        # Shouldn't happen after the filter above,
                        # but guard against it anyway.
                        log.warning(
                            "  %s: skipping cell_idx %d (no matching "
                            "ACL2Cell record)",
                            nb_src, cell_idx,
                        )
                        continue
                    nb_uuid = str(generate_uuid5(f"notebook:{nb_src}"))
                    cell_uuid = str(
                        generate_uuid5(f"cell:{nb_src}:{cell_idx}")
                    )
                    for si, summary in enumerate(sum_list):
                        ref_key = f"{nb_src}:{cell_idx}:{si}"
                        uuid = str(generate_uuid5(
                            f"summary:cell:{ref_key}:{version}"
                        ))
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
                                "summary_index": si,
                                "directory": str(Path(nb_src).parent),
                                "symbol_names": (
                                    cell_rec.symbol_names if cell_rec else []
                                ),
                                "version": version,
                                "symbol": summary.symbol or "",
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

        # Mark entire notebook as done so we can fast-skip on restart.
        checkpoint.setdefault("cell_batches", set()).add(f"nb_done:{nb_src}")

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
    cell_summaries: dict[str, list[tuple[int, int, SummaryResult]]],
    llm: ChatOpenAI,
    model: str,
    cache: LLMCache | None,
    sem: asyncio.Semaphore,
    batch_size: int,
    checkpoint: dict,
    dry_run: bool = False,
    context_size: int = DEFAULT_CONTEXT_SIZE,
    jinja_env: jinja2.Environment | None = None,
    version: str = "",
    mode: str = "tools",
) -> dict[str, SummaryResult]:
    """Phase 2: Generate notebook-level summaries via map-reduce over cell summaries."""
    summary_coll = client.collections.get(COLLECTION_SUMMARY) if not dry_run else None
    nb_summaries: dict[str, SummaryResult] = {}

    total_skipped = 0
    total_done = 0

    # Build the LLM wrapper once (tools or structured output).
    if mode == "json_schema":
        structured_llm = (
            llm.with_structured_output(NotebookSummaryResponse,
                                       method="json_schema", strict=True)
            if llm else None
        )
        llm_with_summary_tools = None
    else:
        llm_with_summary_tools = llm.bind_tools(SUMMARY_TOOLS) if llm else None
        structured_llm = None

    async def _invoke_summary(prompt: str) -> SummaryResult:
        """Call the LLM in the appropriate mode and return SummaryResult."""
        if mode == "json_schema":
            resp, _ = await _cached_json_call(
                prompt, structured_llm, model, cache, sem,
            )
            return _json_response_to_result(resp)
        else:
            tcs, _ = await _cached_tool_call(
                prompt, llm_with_summary_tools, model, cache, sem,
            )
            return _summary_tools_to_result(tcs)

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
        non_empty = [(idx, si, s) for idx, si, s in cell_sums if s.what or s.why or s.how]

        if not non_empty:
            log.debug("No cell summaries for %s, skipping notebook summary", nb_src)
            continue

        if dry_run:
            nb_summaries[nb_src] = SummaryResult()
            continue

        # Map: chunk cell summaries by context size.
        chunks = _chunk_summaries_by_size(non_empty, context_size)
        intermediates: list[SummaryResult] = []

        for chunk in chunks:
            cell_text = _format_cell_summaries(chunk)
            prompt = _render_prompt(
                jinja_env,
                "notebook_chunk.j2",
                NOTEBOOK_CHUNK_PROMPT,
                source_file=nb_src,
                topic_section=_format_topic_section(nb_src),
                cell_summaries=cell_text,
            )
            intermediates.append(await _invoke_summary(prompt))

        # Reduce: combine intermediates (or use directly if only one chunk).
        if len(intermediates) == 1:
            final = intermediates[0]
        else:
            section_text = _format_intermediates(intermediates)
            # If reduce text is too large, do hierarchical reduction.
            if len(section_text.encode("utf-8")) > context_size - 400:
                # Re-chunk and reduce iteratively.
                int_pairs = [(i, 0, s) for i, s in enumerate(intermediates)]
                reduce_chunks = _chunk_summaries_by_size(
                    int_pairs, context_size, prompt_overhead=400)
                new_intermediates: list[SummaryResult] = []
                for rc in reduce_chunks:
                    rc_sums = [s for _, _, s in rc]
                    rc_text = _format_intermediates(rc_sums)
                    prompt = _render_prompt(
                        jinja_env,
                        "notebook_reduce.j2",
                        NOTEBOOK_REDUCE_PROMPT,
                        source_file=nb_src,
                        topic_section=_format_topic_section(nb_src),
                        section_summaries=rc_text,
                    )
                    new_intermediates.append(await _invoke_summary(prompt))
                intermediates = new_intermediates
                section_text = _format_intermediates(intermediates)

            prompt = _render_prompt(
                jinja_env,
                "notebook_reduce.j2",
                NOTEBOOK_REDUCE_PROMPT,
                source_file=nb_src,
                topic_section=_format_topic_section(nb_src),
                section_summaries=section_text,
            )
            final = await _invoke_summary(prompt)

        nb_summaries[nb_src] = final

        # Upsert to Weaviate.
        if summary_coll is not None:
            uuid = str(generate_uuid5(
                f"summary:notebook:{ref_key}:{version}"
            ))
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
                        "version": version,
                        "symbol": "",
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
) -> list[tuple[int, int, SummaryResult]]:
    """Load previously stored cell summaries from Weaviate."""
    summary_coll = client.collections.get(COLLECTION_SUMMARY)
    results: list[tuple[int, int, SummaryResult]] = []

    response = summary_coll.query.fetch_objects(
        filters=(
            Filter.by_property("scope").equal("cell")
            & Filter.by_property("source_file").equal(notebook_source)
        ),
        limit=10000,
        return_properties=["cell_index", "summary_index",
                           "what_summary", "why_summary", "how_summary"],
    )

    for obj in response.objects:
        p = obj.properties
        # Post-filter for exact source match
        if p.get("source_file") != notebook_source:
            continue
        results.append((
            p.get("cell_index", 0),
            p.get("summary_index", 0) or 0,
            SummaryResult(
                what=p.get("what_summary", ""),
                why=p.get("why_summary", ""),
                how=p.get("how_summary", ""),
            ),
        ))

    results.sort(key=lambda x: (x[0], x[1]))
    return results


def _chunk_list(items: list, size: int) -> list[list]:
    """Split a list into chunks of at most *size*."""
    return [items[i:i + size] for i in range(0, len(items), size)]


def _chunk_summaries_by_size(
    items: list[tuple[int, int, SummaryResult]],
    max_bytes: int,
    prompt_overhead: int = 400,
) -> list[list[tuple[int, int, SummaryResult]]]:
    """Split cell summaries into chunks that fit within *max_bytes*.

    Each chunk's formatted text plus *prompt_overhead* (for the prompt
    template) stays under the limit.
    """
    limit = max_bytes - prompt_overhead
    batches: list[list[tuple[int, int, SummaryResult]]] = []
    current: list[tuple[int, int, SummaryResult]] = []
    current_size = 0

    for idx, si, s in items:
        entry_size = 20  # "Cell N:"
        if s.what:
            entry_size += len(s.what.encode("utf-8")) + 10
        if s.why:
            entry_size += len(s.why.encode("utf-8")) + 10
        if s.how:
            entry_size += len(s.how.encode("utf-8")) + 10
        if current and current_size + entry_size > limit:
            batches.append(current)
            current = []
            current_size = 0
        current.append((idx, si, s))
        current_size += entry_size

    if current:
        batches.append(current)
    return batches


def _chunk_text_parts_by_size(
    parts: list[str],
    max_bytes: int,
    prompt_overhead: int = 400,
) -> list[list[str]]:
    """Split a list of text parts into chunks that fit within *max_bytes*."""
    limit = max_bytes - prompt_overhead
    batches: list[list[str]] = []
    current: list[str] = []
    current_size = 0

    for part in parts:
        part_size = len(part.encode("utf-8")) + 4  # separator overhead
        if current and current_size + part_size > limit:
            batches.append(current)
            current = []
            current_size = 0
        current.append(part)
        current_size += part_size

    if current:
        batches.append(current)
    return batches


def _format_cell_summaries(cells: list[tuple[int, int, SummaryResult]]) -> str:
    """Format cell summaries into a text block for the notebook prompt."""
    parts = []
    for idx, si, s in cells:
        label = f"Cell {idx}" if si == 0 else f"Cell {idx} (idea {si + 1})"
        entry = f"{label}:"
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
    context_size: int = DEFAULT_CONTEXT_SIZE,
    jinja_env: jinja2.Environment | None = None,
    version: str = "",
    mode: str = "tools",
) -> dict[str, SummaryResult]:
    """Phase 3: Bottom-up directory summaries."""
    summary_coll = client.collections.get(COLLECTION_SUMMARY) if not dry_run else None

    # Build the LLM wrapper once (tools or structured output).
    if mode == "json_schema":
        structured_llm = (
            llm.with_structured_output(NotebookSummaryResponse,
                                       method="json_schema", strict=True)
            if llm else None
        )
        llm_with_summary_tools = None
    else:
        llm_with_summary_tools = llm.bind_tools(SUMMARY_TOOLS) if llm else None
        structured_llm = None

    async def _invoke_summary(prompt: str) -> SummaryResult:
        """Call the LLM in the appropriate mode and return SummaryResult."""
        if mode == "json_schema":
            resp, _ = await _cached_json_call(
                prompt, structured_llm, model, cache, sem,
            )
            return _json_response_to_result(resp)
        else:
            tcs, _ = await _cached_tool_call(
                prompt, llm_with_summary_tools, model, cache, sem,
            )
            return _summary_tools_to_result(tcs)

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

        # Build and invoke prompt, chunking if needed.
        content_chunks = _chunk_text_parts_by_size(
            contents_parts, context_size, prompt_overhead=400)

        dir_topic = _format_topic_section(directory + "/")

        if len(content_chunks) == 1:
            prompt = _render_prompt(
                jinja_env,
                "directory.j2",
                DIRECTORY_REDUCE_PROMPT,
                directory=directory,
                topic_section=dir_topic,
                contents="\n\n".join(content_chunks[0]),
            )
            final = await _invoke_summary(prompt)
        else:
            # Multi-pass: summarize each chunk, then reduce.
            chunk_results: list[SummaryResult] = []
            for cc in content_chunks:
                prompt = _render_prompt(
                    jinja_env,
                    "directory.j2",
                    DIRECTORY_REDUCE_PROMPT,
                    directory=directory,
                    topic_section=dir_topic,
                    contents="\n\n".join(cc),
                )
                chunk_results.append(await _invoke_summary(prompt))
            # Final reduce over chunk results.
            section_text = _format_intermediates(chunk_results)
            prompt = _render_prompt(
                jinja_env,
                "directory.j2",
                DIRECTORY_REDUCE_PROMPT,
                directory=directory,
                topic_section=dir_topic,
                contents=section_text,
            )
            final = await _invoke_summary(prompt)
        dir_summaries[directory] = final

        # Upsert to Weaviate.
        if summary_coll is not None:
            uuid = str(generate_uuid5(
                f"summary:directory:{ref_key}:{version}"
            ))
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
                        "version": version,
                        "symbol": "",
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
        # Ensure summary_index property exists (added in multi-summary update).
        _ensure_summary_index_property(client)
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
            Property(name="summary_index", data_type=DataType.INT,
                     skip_vectorization=True,
                     description="Summary index within cell (0-based, for multi-summary)"),
            Property(name="directory", data_type=DataType.TEXT,
                     skip_vectorization=True,
                     description="Containing directory path"),
            Property(name="symbol_names", data_type=DataType.TEXT_ARRAY,
                     skip_vectorization=True,
                     description="Symbols defined (cell scope)"),
            Property(name="version", data_type=DataType.TEXT,
                     skip_vectorization=True,
                     description="Summary version label (e.g. v1-qwen3-coder)"),
            Property(name="symbol", data_type=DataType.TEXT,
                     skip_vectorization=True,
                     description="Specific symbol this idea pertains to (cell scope)"),
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


def _ensure_summary_index_property(client: weaviate.WeaviateClient) -> None:
    """Add missing properties (``summary_index``, ``version``, ``symbol``) if needed."""
    coll = client.collections.get(COLLECTION_SUMMARY)
    schema = coll.config.get()
    existing_names = {p.name for p in schema.properties}
    new_props = [
        ("summary_index", DataType.INT,
         "Summary index within cell (0-based, for multi-summary)"),
        ("version", DataType.TEXT,
         "Summary version label (e.g. v1-qwen3-coder)"),
        ("symbol", DataType.TEXT,
         "Specific symbol this idea pertains to (cell scope)"),
    ]
    for name, dtype, desc in new_props:
        if name not in existing_names:
            log.info("Adding %s property to %s", name, COLLECTION_SUMMARY)
            coll.config.add_property(
                Property(
                    name=name,
                    data_type=dtype,
                    skip_vectorization=True,
                    description=desc,
                )
            )


def migrate_summary_index(client: weaviate.WeaviateClient) -> int:
    """Migrate existing summaries: add ``summary_index``, delete portcullis.

    For each scope="cell" object:
    - Sets ``summary_index`` to 0 if missing/null.
    - Rewrites ``ref_key`` from ``"nb:idx"`` to ``"nb:idx:0"``.

    Also deletes any cell summaries whose ``sourceCell`` references a
    portcullis cell (``is_portcullis=True`` in ACL2Cell).

    Returns the number of objects migrated + deleted.
    """
    _ensure_summary_index_property(client)
    coll = client.collections.get(COLLECTION_SUMMARY)

    # ── Identify portcullis cells to delete summaries for ────────────
    cell_coll = client.collections.get(COLLECTION_CELL)
    portcullis_uuids: set[str] = set()
    for obj in cell_coll.iterator(
        include_vector=False,
        return_properties=["is_portcullis"],
    ):
        if obj.properties.get("is_portcullis"):
            portcullis_uuids.add(str(obj.uuid))

    log.info("Found %d portcullis cells", len(portcullis_uuids))

    # ── Delete portcullis summaries ──────────────────────────────────
    portcullis_deleted = 0
    if portcullis_uuids:
        to_delete: list[str] = []
        for obj in coll.iterator(
            include_vector=False,
            return_properties=["scope"],
            return_references=weaviate.classes.query.QueryReference(
                link_on="sourceCell",
                return_properties=[],
            ),
        ):
            if obj.properties.get("scope") != "cell":
                continue
            refs = obj.references
            if refs and "sourceCell" in refs:
                for ref_obj in refs["sourceCell"].objects:
                    if str(ref_obj.uuid) in portcullis_uuids:
                        to_delete.append(str(obj.uuid))
                        break

        for uuid_str in to_delete:
            coll.data.delete_by_id(uuid_str)
            portcullis_deleted += 1

        log.info("Deleted %d portcullis-linked summaries", portcullis_deleted)

    # ── Migrate summary_index ────────────────────────────────────────
    migrated = 0
    batch_updates: list[tuple[str, dict]] = []

    for obj in coll.iterator(
        include_vector=False,
        return_properties=["scope", "ref_key", "summary_index"],
    ):
        p = obj.properties
        if p.get("scope") != "cell":
            continue

        si = p.get("summary_index")
        ref_key = p.get("ref_key", "")

        needs_update = False
        new_props: dict = {}

        if si is None:
            new_props["summary_index"] = 0
            needs_update = True

        # Rewrite ref_key: "books/foo.lisp:5" → "books/foo.lisp:5:0"
        # Only if it has exactly 2 colon-delimited segments at the end.
        if ref_key:
            # ref_key format: "path/file.lisp:cell_idx" or "path/file.lisp:cell_idx:si"
            # Split from the right to handle paths with colons.
            parts = ref_key.rsplit(":", 2)
            if len(parts) == 2:
                # Old format: "nb_src:cell_idx" — needs ":0" appended.
                new_props["ref_key"] = f"{ref_key}:0"
                needs_update = True

        if needs_update:
            batch_updates.append((str(obj.uuid), new_props))

    if not batch_updates:
        log.info("No cell summaries need summary_index migration")
    else:
        log.info("Migrating %d cell summaries to add summary_index...", len(batch_updates))
        for uuid_str, props in batch_updates:
            coll.data.update(
                uuid=uuid_str,
                properties=props,
            )
            migrated += 1
        log.info("Migration complete: %d objects updated, %d portcullis deleted",
                 migrated, portcullis_deleted)

    # ── Backfill version on summaries that lack one ──────────────────
    version_backfilled = 0
    default_version = next(iter(SUMMARY_VERSIONS))  # first defined version
    for obj in coll.iterator(
        include_vector=False,
        return_properties=["version"],
    ):
        v = obj.properties.get("version")
        if not v:
            coll.data.update(obj.uuid, properties={"version": default_version})
            version_backfilled += 1
            if version_backfilled % 1000 == 0:
                log.info("  Backfilled version on %d summaries...",
                         version_backfilled)

    if version_backfilled:
        log.info("Backfilled version='%s' on %d summaries",
                 default_version, version_backfilled)

    return migrated + portcullis_deleted + version_backfilled


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
    cell_summaries: dict[str, list[tuple[int, int, SummaryResult]]],
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
        "--notebook-list", default=None, metavar="FILE",
        help="File with one notebook source_file per line (overrides --source-dir)",
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
        "--base-url", "--lm-studio-url",
        dest="base_url",
        default=os.environ.get("LLM_BASE_URL", DEFAULT_LM_STUDIO_URL),
        help=f"LLM API base URL (env: LLM_BASE_URL, default: {DEFAULT_LM_STUDIO_URL})",
    )
    p.add_argument(
        "--api-key",
        default=os.environ.get("LLM_API_KEY", "lm-studio"),
        help="LLM API key (env: LLM_API_KEY, default: lm-studio)",
    )
    p.add_argument(
        "--model", default=os.environ.get("LLM_MODEL"),
        help="LLM model name (env: LLM_MODEL; auto-detected from LM Studio if not set)",
    )
    p.add_argument(
        "--version",
        default=os.environ.get("SUMMARY_VERSION", "v1-qwen3-coder"),
        choices=list(SUMMARY_VERSIONS.keys()),
        help="Summary version label (env: SUMMARY_VERSION, default: v1-qwen3-coder)",
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
        "--migrate", action="store_true",
        help="Migrate existing cell summaries to add summary_index (run once)",
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
        "--overwrite", action="store_true",
        help="Delete existing summaries, cache, and checkpoint for targeted notebooks before processing",
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
    version_label = args.version
    version_cfg = SUMMARY_VERSIONS[version_label]
    model = args.model or version_cfg["model"]
    output_mode = version_cfg.get("mode", "tools")
    if not model and not args.dry_run:
        # Only auto-detect when URL looks like a local LM Studio endpoint.
        if ":1234" in args.base_url:
            model = detect_lm_studio_model(args.base_url)
        else:
            log.error("--model is required for non-LM-Studio endpoints")
            return 1

    # Load Jinja prompt templates for this version.
    jinja_env: jinja2.Environment | None = None
    try:
        jinja_env = _load_prompt_templates(version_cfg["prompts"])
        log.info(
            "Loaded prompt templates from %s/%s",
            PROMPTS_DIR, version_cfg["prompts"],
        )
    except FileNotFoundError:
        log.warning(
            "Prompt template dir %s/%s not found, using inline fallbacks",
            PROMPTS_DIR, version_cfg["prompts"],
        )

    llm: ChatOpenAI | None = None
    if not args.dry_run:
        llm = ChatOpenAI(
            base_url=args.base_url,
            api_key=args.api_key,
            model=model or "local-model",
        )
        log.info(
            "LLM: model=%s, base_url=%s, version=%s, mode=%s",
            model, args.base_url, version_label, output_mode,
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

        # ── Migration ────────────────────────────────────────────────
        if args.migrate:
            migrated = migrate_summary_index(client)
            log.info("Migration complete: %d objects updated", migrated)
            return 0

        # ── Discover notebooks ───────────────────────────────────────
        notebook_list: list[str] | None = None
        if args.notebook_list:
            with open(args.notebook_list) as f:
                notebook_list = [
                    line.strip() for line in f
                    if line.strip() and not line.strip().startswith("#")
                ]
            log.info("Loaded %d notebooks from %s", len(notebook_list),
                     args.notebook_list)

        effective_dir = _normalize_source_dir(args.source_dir)
        display_dir = "(root)" if effective_dir == _ROOT_SENTINEL else (effective_dir or "(all)")
        if args.source_dir and effective_dir != args.source_dir:
            log.info("Normalized --source-dir %r → %r",
                     args.source_dir, display_dir)
        notebook_sources = _fetch_all_notebook_sources(
            client, args.source_dir, no_recurse=args.no_recurse,
            notebook_list=notebook_list,
        )
        log.info("Found %d notebooks%s%s", len(notebook_sources),
                 f" from {args.notebook_list}" if args.notebook_list
                 else (f" under {display_dir}" if args.source_dir else ""),
                 " (no recurse)" if args.no_recurse else "")

        if not notebook_sources:
            log.warning("No notebooks found, nothing to do")
            return 0

        # ── Overwrite: clean existing data for targeted notebooks ─
        if args.overwrite and not args.dry_run:
            log.info("Overwrite mode: clearing data for %d notebooks",
                     len(notebook_sources))
            summary_coll = client.collections.get(COLLECTION_SUMMARY)
            dirs_to_clear: set[str] = set()
            total_deleted = 0
            for nb_src in notebook_sources:
                dirs_to_clear.add(str(Path(nb_src).parent))
                # Delete Weaviate summaries for this notebook.
                while True:
                    result = summary_coll.query.fetch_objects(
                        filters=Filter.by_property("source_file").equal(nb_src),
                        limit=200, return_properties=["ref_key"])
                    if not result.objects:
                        break
                    for obj in result.objects:
                        summary_coll.data.delete_by_id(obj.uuid)
                        total_deleted += 1
                # Clear checkpoint entries for this notebook.
                for key in ("cell_batches", "cells", "notebooks"):
                    entries = checkpoint.get(key)
                    if entries is not None:
                        to_remove = [e for e in entries
                                     if nb_src in e]
                        for e in to_remove:
                            entries.discard(e) if isinstance(entries, set) else None
                            if isinstance(entries, list):
                                entries.remove(e)
            # Delete directory-scope summaries for affected dirs.
            for d in dirs_to_clear:
                while True:
                    result = summary_coll.query.fetch_objects(
                        filters=(Filter.by_property("scope").equal("directory")
                                 & Filter.by_property("directory").equal(d)),
                        limit=100, return_properties=["ref_key"])
                    if not result.objects:
                        break
                    for obj in result.objects:
                        summary_coll.data.delete_by_id(obj.uuid)
                        total_deleted += 1
                # Clear directory checkpoint entries.
                dirs_cp = checkpoint.get("directories")
                if dirs_cp is not None:
                    dirs_cp.discard(d) if isinstance(dirs_cp, set) else None
                    if isinstance(dirs_cp, list) and d in dirs_cp:
                        dirs_cp.remove(d)
            log.info("Overwrite: deleted %d Weaviate objects, cleared checkpoint",
                     total_deleted)

        run_cell = args.scope in ("all", "cell")
        run_notebook = args.scope in ("all", "notebook")
        run_directory = args.scope in ("all", "directory")

        # ── Phase 1: Cell summaries ──────────────────────────────────
        cell_summaries: dict[str, list[tuple[int, int, SummaryResult]]] = {}
        if run_cell:
            log.info("=== Phase 1: Cell Summaries ===")
            cell_summaries = await summarize_cells(
                client, notebook_sources, llm, model, cache, sem,
                args.batch_size, checkpoint,
                context_size=args.context_size,
                dry_run=args.dry_run,
                jinja_env=jinja_env,
                version=version_label,
                mode=output_mode,
            )

        # ── Phase 2: Notebook summaries ──────────────────────────────
        nb_summaries: dict[str, SummaryResult] = {}
        if run_notebook:
            log.info("=== Phase 2: Notebook Summaries ===")
            nb_summaries = await summarize_notebooks(
                client, notebook_sources, cell_summaries,
                llm, model, cache, sem,
                args.batch_size, checkpoint, args.dry_run,
                context_size=args.context_size,
                jinja_env=jinja_env,
                version=version_label,
                mode=output_mode,
            )

        # ── Phase 3: Directory summaries ─────────────────────────────
        if run_directory:
            log.info("=== Phase 3: Directory Summaries ===")
            await summarize_directories(
                client, notebook_sources, nb_summaries,
                llm, model, cache, sem,
                args.batch_size, checkpoint, args.dry_run,
                context_size=args.context_size,
                jinja_env=jinja_env,
                version=version_label,
                mode=output_mode,
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
    # Suppress noisy HTTP-level logging from httpx / httpcore.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    return asyncio.run(async_main(args))


if __name__ == "__main__":
    sys.exit(main())
