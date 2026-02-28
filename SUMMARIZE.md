# ACL2 KG Summarization Pipeline — Design Plan

## Overview

Build a LangChain-based script (`scripts/summarize_kg.py`) that generates self-contained
**what / why / how** text summaries at three resolutions — cell, notebook, and directory —
using a map-reduce pattern. Summaries are stored in a single new `ACL2Summary` Weaviate
collection with three named vectors (`what_vector`, `why_vector`, `how_vector`) so each
dimension is independently searchable.

LM Studio provides the LLM (OpenAI-compatible at `:1234`); Ollama `nomic-embed-text`
handles embeddings. A heuristic filter skips trivial cells. 4 concurrent LLM requests,
checkpoint-based restartability, and **LLM call memoization** to avoid redundant generation
on re-ingestion.

---

## ACL2Summary Collection Schema

Single collection, `scope` discriminator, three named vectors:

| Property | Type | Vectorized | Description |
|---|---|---|---|
| `scope` | TEXT | no | `"cell"`, `"notebook"`, or `"directory"` |
| `ref_key` | TEXT | no | Deterministic key (see below) |
| `what_summary` | TEXT | yes (what_vector) | What it does |
| `why_summary` | TEXT | yes (why_vector) | Purpose / goal |
| `how_summary` | TEXT | yes (how_vector) | Usage instructions |
| `source_file` | TEXT | no | Parent notebook path (cells & notebooks) |
| `cell_index` | INT | no | 0-based cell index; -1 for non-cell scopes |
| `directory` | TEXT | no | Containing directory path |
| `symbol_names` | TEXT_ARRAY | no | Symbols defined (cell-scope) |

**Named Vectors** (all via `text2vec-ollama` / `nomic-embed-text:latest`):

| Vector Name | Source Property |
|---|---|
| `what_vector` | `what_summary` |
| `why_vector` | `why_summary` |
| `how_vector` | `how_summary` |

**Cross-references**: `sourceNotebook` → ACL2Notebook, `sourceCell` → ACL2Cell (cell-scope).

**UUID key**: `generate_uuid5("summary:" + scope + ":" + ref_key)`

**`ref_key` patterns**:

| Scope | ref_key example |
|---|---|
| cell | `books/kestrel/bv/bitor.lisp:9` |
| notebook | `books/kestrel/bv/bitor.lisp` |
| directory | `books/kestrel/bv` |

---

## LLM Call Memoization

Since summarization is expensive and slow, all LLM calls are memoized at the raw call
level using a content-addressable cache keyed by `UUID5(full_prompt_text)`.

### Design

- **Cache store**: SQLite database at `scripts/.llm_cache.sqlite` with schema:
  ```sql
  CREATE TABLE IF NOT EXISTS llm_cache (
      prompt_hash TEXT PRIMARY KEY,   -- UUID5 of the full prompt string
      prompt_text TEXT,               -- the full prompt (for debugging)
      response    TEXT,               -- the LLM JSON response
      model       TEXT,               -- model name used
      created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
  );
  ```
- **Keying**: Before each LLM call, compute `generate_uuid5(prompt_text)`. If the hash
  exists in the cache, return the stored response immediately — no LLM call made.
- **Write-through**: After a successful LLM call, store the response in the cache.
- **Invalidation**: If the cell content changes (different `code_text` / `comment_text`),
  the prompt changes, the hash changes, and a fresh call is made automatically. Old
  entries remain in the cache (harmless) and can be pruned with a CLI flag.
- **Benefits**: Re-ingestion after schema changes, Weaviate rebuilds, or code fixes
  reuses all prior LLM work. Only new or changed cells trigger LLM calls.
- **CLI flags**:
  - `--no-cache` — bypass the cache entirely (always call the LLM)
  - `--clear-cache` — drop and recreate the cache table before starting
  - `--cache-path` — override the default `.llm_cache.sqlite` location

### Implementation

Wrap the LLM invocation in a `cached_llm_call(prompt: str, llm, model: str) -> str`
function that checks the SQLite cache first. This sits below the LangChain chain, so the
chain logic itself doesn't need to know about caching. The wrapper handles serialization,
hashing, and cache reads/writes. Thread-safe via SQLite's built-in locking.

---

## Heuristic Cell Filter

Skip cells whose content is unlikely to benefit from a what/why/how summary:

- Starts with `(include-book` or `(in-package`
- Starts with `(local (include-book`
- Total text length < 40 characters
- `is_portcullis == True`
- Both `code_text` and `comment_text` are empty

This should reduce ~507K cells to roughly ~200–250K substantive cells.

---

## Pipeline Phases

### Phase 1 — Cell Summaries (Map)

For each qualifying cell, build a prompt with:

- The cell's `code_text` or `comment_text`
- The cell's `package` context
- Names and kinds of symbols defined in this cell (from `definesSymbols` cross-ref)
- Dependency symbol names (from the symbol graph)

Prompt instructs the LLM to produce JSON: `{"what": "...", "why": "...", "how": "..."}`
where any field is omitted if not applicable. Use `ChatOpenAI` with
`response_format={"type": "json_object"}`.

Run with 4 concurrent `asyncio` tasks via a semaphore-limited gather pattern.

### Phase 2 — Notebook Summaries (Reduce)

For each notebook, collect its cell summaries, then:

1. **Chunk** into groups of ~20 cell summaries if the notebook has many cells
2. **Map**: summarize each chunk into an intermediate what/why/how
3. **Reduce**: combine intermediates into a single notebook-level what/why/how
4. Include notebook metadata (source_file, portcullis imports, bootstrap status) as context

### Phase 3 — Directory Summaries (Reduce, bottom-up)

For each directory in the `books/` tree, processed bottom-up (leaves first):

1. Collect notebook summaries within that directory (non-recursive)
2. Plus child directory summaries (already computed)
3. Reduce into a single directory-level what/why/how

---

## CLI Interface

```
python scripts/summarize_kg.py [options]
```

| Flag | Default | Description |
|---|---|---|
| `--scope` | `all` | `cell`, `notebook`, `directory`, or `all` (runs in order) |
| `--source-dir` | (all) | Limit to a subtree (e.g. `books/defsort`) |
| `--jobs` | `4` | LLM concurrency |
| `--lm-studio-url` | `http://host.docker.internal:1234/v1` | LM Studio endpoint |
| `--model` | (auto-detect) | Override model name |
| `--batch-size` | `200` | Weaviate upsert batch size |
| `--recreate` | false | Drop and rebuild ACL2Summary collection |
| `--dry-run` | false | Report counts without calling the LLM |
| `--no-cache` | false | Bypass LLM memoization cache |
| `--clear-cache` | false | Clear the cache before starting |
| `--cache-path` | `scripts/.llm_cache.sqlite` | Cache file location |
| `--restart` | false | Clear checkpoint and start fresh |

---

## Checkpointing

Track progress in `scripts/.summarize_checkpoint.json`:

- Sets of completed cell keys, notebook keys, directory keys
- Resume from where it left off on restart
- `--restart` flag to clear checkpoint and start fresh
- Separate from the LLM cache — checkpoint tracks what's been upserted to Weaviate,
  cache tracks what LLM responses we've already generated

---

## Verification

1. `--dry-run` on `books/defsort` (6 notebooks) — verify cell filtering and counting
2. Full run on `books/defsort` — cell + notebook + directory summaries
3. Query `ACL2Summary` — filter by `scope="cell"`, verify what/why/how fields
4. Semantic search: `near_text("how to sort", target_vector="how_vector")` → defsort results
5. Browser integration shows summaries inline on symbol, notebook, and directory pages

---

## Dependency Additions

Add to `scripts/requirements.txt`:

```
langchain>=0.3.0
langchain-openai
langchain-ollama
```

---

## Key Decisions

| Decision | Rationale |
|---|---|
| **One collection** (`ACL2Summary`) with `scope` discriminator | Simpler schema; single cross-scope query if needed; three named vectors give independent search |
| **LM Studio** via `ChatOpenAI` | Matches existing `rag_qa.py` pattern in `external/ontological-engineer/` |
| **Heuristic filter** | Avoid wasting LLM calls on trivial include-book / in-package cells |
| **4 concurrent** LLM requests | Moderate parallelism, safe for typical GPU |
| **Bottom-up directory reduce** | Leaf dirs first; parents incorporate child summaries |
| **JSON structured output** | Cleaner than parsing free text; LM Studio supports `response_format` |
| **SQLite LLM memoization** | Content-addressable by prompt hash; survives re-ingestion; no external deps; thread-safe |
| **Cache separate from checkpoint** | Cache = "have we computed this answer?"; checkpoint = "have we stored it in Weaviate?" |
