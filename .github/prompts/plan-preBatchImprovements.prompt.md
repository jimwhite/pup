# Plan: Pre-Batch Multi-Model Summarization Improvements

All four TODOs are addressed together because they interact: Groq support and model/prompt tracking must land before re-running batches, portcullis filtering avoids wasted LLM calls, and symbol tagging enriches the new summaries.

## Steps

### 1. Generalize LLM endpoint configuration

In [summarize_kg.py](scripts/summarize_kg.py), update `build_parser()` (~line 2248):
- Rename `--lm-studio-url` → `--base-url` (keep `--lm-studio-url` as a deprecated alias via `dest="base_url"`)
- Add `--api-key` arg, defaulting to env var `LLM_API_KEY`, then falling back to `"lm-studio"`
- Add env var fallback for `--base-url`: `LLM_BASE_URL`, defaulting to the current `DEFAULT_LM_STUDIO_URL`
- Add env var fallback for `--model`: `LLM_MODEL`

Update `ChatOpenAI` construction (~line 2368) to use `args.base_url` and `args.api_key`.

Update `detect_lm_studio_model()` to only auto-detect when the base URL looks like LM Studio (has port 1234 or similar), otherwise require `--model`.

### 2. Fix cache key to include model

In `LLMCache.get()` and `LLMCache.put()` (~line 601-618), change the hash from `generate_uuid5(prompt)` to `generate_uuid5(f"{model}\n{prompt}")`. Both methods need a `model` parameter. Update all call sites (in `_cached_tool_call` and anywhere else `cache.get`/`cache.put` are called) to pass the model name.

### 3. Drop portcullis cells from summarization

In `_fetch_cells_for_notebook()` (~line 852), add a filter after `cells.sort(...)`:
```python
cells = [c for c in cells if not c.is_portcullis]
```
Use Python-side filtering (not Weaviate `Filter.by_property`) to keep it simple and log a count of skipped portcullis cells at DEBUG level.

### 4. Add `symbol` field to Report tools (cell level only)

In `ReportWhat`, `ReportWhy`, `ReportHow` (~lines 503-530), add:
- `symbol: str | None = Field(default=None, description="The specific symbol (from the Defines: header) this idea pertains to, if applicable.")`

In `_make_progress_fn()`, validate the reported `symbol` against the batch's defined symbols for that cell. Matching must be flexible — allow with or without the package prefix (e.g. both `"ACL2::MY-FN"` and `"MY-FN"` match `"ACL2::MY-FN"` from the Defines header). The cell's defined symbols are available from `CellRecord.symbol_names`. Three cases:
- Symbol matches a defined symbol → "Recorded cell N." (normal)
- Symbol supplied but doesn't match any defined symbol → accept the summary, but append a note: "Note: symbol '{sym}' does not match any defined symbol in this cell."
- No symbol supplied → "Recorded cell N." (normal)

In `_tool_calls_to_summaries()` (~line 1135), extract `symbol` from each tool call's args and store it on the `SummaryResult`. Add a `symbol` field to the `SummaryResult` dataclass.

In the cell summary upsert (~line 1487), write `symbol` to a new `symbol` property on `ACL2Summary` (singular, TEXT, skip_vectorization) — distinct from the existing `symbol_names` array which lists all cell-level symbols.

### 5. Extract prompts into Jinja template files

Move `BATCH_CELL_PROMPT`, `NOTEBOOK_CHUNK_PROMPT`, `NOTEBOOK_REDUCE_PROMPT`, and the directory prompt out of `summarize_kg.py` into Jinja2 template files organized by labeled directory:

```
scripts/prompts/
  v1/
    cell_batch.j2
    notebook_chunk.j2
    notebook_reduce.j2
    directory.j2
  v2/
    cell_batch.j2
    notebook_chunk.j2
    notebook_reduce.j2
    directory.j2
```

The `v1/` templates are the current prompts extracted verbatim. `v2/` can evolve independently (e.g. adding symbol instructions, refining decomposition guidance).

In `summarize_kg.py`, load templates at startup via `jinja2.FileSystemLoader` pointed at the selected prompt directory. The template variables (`source_file`, `topic_section`, `cells_text`, `continuation_section`, etc.) remain the same — they're passed as Jinja context at render time.

Add `jinja2` to the project dependencies.

### 6. Version label and `SUMMARY_VERSIONS` dictionary

Define a `SUMMARY_VERSIONS` dictionary at module level:
```python
SUMMARY_VERSIONS: dict[str, dict] = {
    "v1-qwen3-coder": {
        "model": "qwen/qwen3-coder-next",
        "prompts": "v1",
        "description": "Initial LM Studio run with qwen3-coder-next",
    },
    "v2-groq-gpt-oss": {
        "model": "gpt-oss-20b",
        "prompts": "v2",
        "description": "Groq API with gpt-oss-20b, symbol tagging",
    },
}
```
The `prompts` value is the directory label under `scripts/prompts/`. Add a `--version` CLI arg that selects a key from this dict. The version label is what gets stored and queried — it encodes both the model and the prompt set used.

### 7. Add `version` and `symbol` to ACL2Summary schema

In `_ensure_summary_collection()` (~line 2033), add:
- `version`: TEXT, `skip_vectorization=True` — the version label (e.g. `"v2-groq-gpt-oss"`)
- `symbol`: TEXT, `skip_vectorization=True` — the specific symbol this idea pertains to (nullable, cell-level only)

When `--migrate` is passed, add these properties to the existing collection via `collection.config.add_property()`. Also during migration, delete any existing summaries whose `sourceCell` references a portcullis cell (query ACL2Cell for `is_portcullis=True`, collect their UUIDs, then delete ACL2Summary objects that cross-ref those cells).

### 8. Include version in summary UUID generation

Change all three UUID seeds to include the version label:
- Cell: `generate_uuid5(f"summary:cell:{ref_key}:{version}")` (line ~1493)
- Notebook: `generate_uuid5(f"summary:notebook:{ref_key}:{version}")` (line ~1662)
- Directory: `generate_uuid5(f"summary:directory:{ref_key}:{version}")` (line ~1955)

This way different versions produce separate `ACL2Summary` objects for the same cell/notebook/directory.

### 9. Set `version` at all upsert points

At all three upsert locations (cell ~1487, notebook ~1662, directory ~1955), add `"version": version` to the properties dict. The version label is the single tracking value — the `SUMMARY_VERSIONS` dict provides the model and prompt directory lookup when needed.

### 10. Update `ref_key` to NOT include version

Keep `ref_key` as-is (`nb_src:cell_idx:si`). The version is only in the UUID seed, not in `ref_key`. This lets queries filter by `ref_key` + optional `version` filter to find summaries across versions for the same cell.

### 11. Update MCP and browser queries for version awareness

**MCP** ([weaviate_client.py](external/acl2-kg-mcp/src/acl2_kg_mcp/weaviate_client.py)):
- `search_summaries` (~L226): Add optional `version` filter parameter; include `version` and `symbol` in returned properties
- `get_summary` (~L670): Include `version` and `symbol` in returned properties
- `_get_cell_summaries` (~L769): Add optional `version` filter; return `version` and `symbol` in results
- `_get_notebook_summary` (~L796): Same

**MCP server** ([server.py](external/acl2-kg-mcp/src/acl2_kg_mcp/server.py)):
- Add optional `version` parameter to relevant tool schemas; pass through to client

**Browser** ([kg_browser.py](scripts/kg_browser.py)):
- Summary display routes (~L437-533): Fetch and display `version` and `symbol` properties on each summary
- Add a version dropdown to the right side of the navigation bar, populated from the distinct `version` values present in the ACL2Summary collection. The selected version filters all summary queries site-wide (passed as a query parameter, stored in session or cookie). Default to the latest version.

### 12. Update tests

- Update `TestProgressFunction` and `TestCachedToolCall` for model-aware cache signature
- Add test for portcullis cell filtering
- Add test for symbol extraction from tool calls with validation (match, mismatch, missing)
- Add test for `SUMMARY_VERSIONS` dict integrity (all required keys present, prompt dirs exist)
- Add test for Jinja template loading (templates render with expected variables)
- Update any tests that construct `LLMCache` or mock cache get/put

### 13. Update batch runner script

In [run_summary_batches.sh](scripts/run_summary_batches.sh), update to support `--base-url`, `--api-key`, `--model`, and `--version` via environment variables or `EXTRA_FLAGS`.

## Verification

- `pytest scripts/test_summarize_kg.py -v` — all tests pass
- `pytest external/acl2-kg-mcp/tests/ -v` — all MCP tests pass
- Dry run: `python scripts/summarize_kg.py --notebook-list scripts/batch-lists/batch-1.txt --base-url https://api.groq.com/openai/v1 --api-key $GROQ_API_KEY --model gpt-oss-20b --version v2-groq-gpt-oss --dry-run` — verifies endpoint config, portcullis filtering counts
- Run on a small test notebook with `--overwrite -v` to confirm: symbol param captured, version stored, portcullis cells skipped, cache keyed by model
- Run `--migrate` against existing Weaviate to add new properties and delete portcullis summaries

## Decisions

- `symbol` is singular optional `str` per Report call (cell level only), not a list — LLM tags one symbol per atomic idea
- Symbol validation is flexible: match with or without package prefix (e.g. `"MY-FN"` matches `"ACL2::MY-FN"`)
- Non-matching symbols are accepted but noted in tool response — we don't reject the summary
- Prompts live in Jinja2 template files under `scripts/prompts/{label}/`, not inline in Python
- Version label (`--version`) is the single tracking value stored on summaries; `SUMMARY_VERSIONS` dict maps label → {model, prompts dir, description}
- Different versions → separate UUID → side-by-side summary objects in Weaviate
- `ref_key` unchanged; version is in UUID seed only, queryable via `version` property filter
- `--lm-studio-url` kept as deprecated alias for `--base-url`
- Portcullis filter is Python-side (not Weaviate query filter) for simplicity and logging
- Migration deletes existing portcullis summaries (cells with `is_portcullis=True`)
- Migration path (not wipe) for existing ACL2Summary collection
