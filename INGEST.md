

 ## Plan: ACL2 Notebook → Weaviate Ingestion (Final Draft)

**TL;DR** — A single script scripts/ingest_notebooks.py walks all `.ipynb` files under acl2, parses their nbformat JSON, and upserts structured objects into three Weaviate v4 collections: **ACL2Notebook**, **ACL2Cell**, and **ACL2Symbol**. Raw JSON payloads (events, forms, expansions) stay in the `.ipynb` files and are fetchable by notebook path + cell index. Symbols and dependencies are fully unpacked into the `ACL2Symbol` collection with `dependsOn` cross-references forming the symbol graph. Portcullis companion data is unpacked into an `OBJECT_ARRAY` on the notebook. Cells carry two named vectors (`comment_vector`, `code_vector`) via `text2vec-ollama` so comment queries and code queries don't interfere. Deterministic UUID5 keys make the import idempotent; a checkpoint file makes it restartable.

---

### Schema

**`ACL2Notebook`** — one object per notebook, no vectorization

| Property | DataType | Description |
|---|---|---|
| `source_file` | `TEXT` | `.lisp` path with acl2 prefix stripped (e.g. `books/centaur/bitops/fast-part-select.lisp`) |
| `stem` | `TEXT` | Filename stem (`fast-part-select`) |
| `is_bootstrap` | `BOOL` | True for the ~57 top-level boot-strap sources (not under `books/`) |
| `source_type` | `TEXT` | From `acl2_boot_strap.source_type` if present (`acl2_source` / `raw_common_lisp`), else empty |
| `acl2_version` | `TEXT` | From `language_info.version` if present |
| `cell_count` | `INT` | Total cells |
| `code_cell_count` | `INT` | Code cells only |
| `portcullis` | `OBJECT_ARRAY` | Unpacked companion file entries (see below) |

Portcullis nested object properties:
| Property | DataType | Description |
|---|---|---|
| `filename` | `TEXT` | Companion filename (`cert.acl2`, `defsort.acl2`, etc.) |
| `content` | `TEXT` | Full text of the companion file |

UUID5 key: `"notebook:" + source_file`

---

**`ACL2Cell`** — one object per cell, two named vectors

| Property | DataType | Description |
|---|---|---|
| `notebook_source` | `TEXT` | Parent notebook's `source_file` (enables filtering/grouping without a cross-ref hop) |
| `cell_index` | `INT` | 0-based position in notebook |
| `cell_type` | `TEXT` | `code`, `markdown`, or `raw` |
| `comment_text` | `TEXT` | Cell source text — populated only for **markdown** cells, empty string for others |
| `code_text` | `TEXT` | Cell source text — populated only for **code** cells, empty string for others |
| `package` | `TEXT` | ACL2 package after execution (from `display_data`), empty if not executed |
| `execution_count` | `INT` | Jupyter execution count (-1 if null/unexecuted) |
| `provenance_start` | `INT` | Byte offset in original source file (-1 if absent) |
| `provenance_end` | `INT` | Byte offset end (-1 if absent) |
| `is_portcullis` | `BOOL` | True if `provenance.portcullis` is set |
| `stdout` | `TEXT` | Concatenated stream outputs (ACL2 proof text — useful for search) |
| `execute_result` | `TEXT` | `text/plain` from `execute_result` output |

Named vectors:
- `comment_vector` — `text2vec-ollama(nomic-embed-text)` sourcing `comment_text`
- `code_vector` — `text2vec-ollama(nomic-embed-text)` sourcing `code_text`

Cross-references:
- `belongsToNotebook` → `ACL2Notebook`
- `definesSymbols` → `ACL2Symbol` (one-to-many)

UUID5 key: `"cell:" + source_file + ":" + str(cell_index)`

**Not stored in Weaviate** (fetch from `.ipynb` by `notebook_source` + `cell_index`): `events`, `forms`, `expansions`, `symbols` array, `dependencies` map, raw `display_data` JSON.

---

**`ACL2Symbol`** — one object per unique `PACKAGE::NAME`, one named vector

| Property | DataType | Description |
|---|---|---|
| `name` | `TEXT` | Symbol name (e.g. `EXW-FN1`) |
| `package` | `TEXT` | Symbol package (e.g. `ACL2`, `COMMON-LISP`) |
| `qualified_name` | `TEXT` | `PACKAGE::NAME` canonical form |
| `kind` | `TEXT` | Most specific observed kind: `function` / `macro` / `theorem` / `constant` / `stobj` / `variable` / `raw-function` / `special-form` / `unknown` |
| `is_operator` | `BOOL` | Whether symbol ever appeared in operator position |

Named vector:
- `symbol_vector` — `text2vec-ollama(nomic-embed-text)` sourcing `qualified_name`

Cross-references:
- `dependsOn` → `ACL2Symbol` (many-to-many self-reference — the dependency graph)
- `definedInCell` → `ACL2Cell` (the cell(s) where this symbol is defined)

UUID5 key: `"symbol:" + qualified_name`

---

### Steps

1. **Create** scripts/ingest_notebooks.py — single-file script, no package structure. Imports: `weaviate`, `json`, `os`, `argparse`, `logging`, `uuid`, `pathlib`.

2. **CLI** — `argparse` with flags:
   - `--source-dir` (default: acl2)
   - `--source-prefix` (default: acl2 — stripped from `source_file` URIs)
   - `--weaviate-host` / `--port` / `--grpc-port` (defaults: `host.docker.internal`, `8080`, `50051`)
   - `--ollama-url` (default: `http://host.docker.internal:11434`)
   - `--recreate` (drop and rebuild all collections)
   - `--force` (ignore checkpoint, reprocess everything)
   - `--batch-size` (default: `200`)
   - `--dry-run` (parse and validate, don't write to Weaviate)
   - `--verbose`

3. **`ensure_collections(client, ollama_url, recreate)`** — create all three collections if missing, or drop+recreate if `--recreate`. Uses `client.collections.create()` with:
   - `ACL2Notebook`: `vectorizer_config=Configure.Vectorizer.none()`, `OBJECT_ARRAY` portcullis property with nested `filename`/`content`
   - `ACL2Cell`: two named vectors via `Configure.NamedVectors.text2vec_ollama()`, `ReferenceProperty` for `belongsToNotebook` and `definesSymbols`
   - `ACL2Symbol`: one named vector, `ReferenceProperty` for `dependsOn` (self-ref) and `definedInCell`

4. **`find_notebooks(source_dir)`** — `os.walk()` for `*.ipynb`, skip placeholders (check `metadata.placeholder`), return sorted `Path` list.

5. **`parse_notebook(path, source_prefix)`** — read JSON, extract:
   - Notebook-level: `source_file` (strip  + prefix), `acl2_boot_strap`, `acl2_portcullis` (unpack to `[{filename, content}, ...]`), `language_info`
   - Per-cell: `cell_type`, joined `source`, `provenance.*`, `execution_count`, concatenated `stdout`, `execute_result` text
   - From `display_data` with MIME `application/vnd.acl2.events+json`: extract `package`, `symbols` array, `dependencies` map
   - Collect all unique symbols by `qualified_name`, resolve `kind` conflicts (prefer non-`unknown`), track which cells define which symbols and dependency edges
   - Return structured dataclass/dict

6. **Phase 1 — Upsert Notebooks**: batch-insert `ACL2Notebook` objects via `collection.batch.fixed_size(batch_size)`. Deterministic UUID5s make re-inserts overwrite.

7. **Phase 2 — Upsert Symbols**: collect all unique symbols across all parsed notebooks (global dedup by `qualified_name`). Batch-insert `ACL2Symbol` objects. Build a `{qualified_name → uuid}` lookup for cross-referencing.

8. **Phase 3 — Upsert Cells**: batch-insert `ACL2Cell` objects with inline cross-references to parent notebook UUID. Populate `comment_text` xor `code_text` based on `cell_type`.

9. **Phase 4 — Cross-references**: 
   - `ACL2Cell.definesSymbols` → for each cell, link to the `ACL2Symbol` UUIDs it defines (from parsed `dependencies` keys + `symbols` where `kind != unknown`)
   - `ACL2Symbol.dependsOn` → from parsed `dependencies` map, link each defined symbol to its referenced symbols via `reference_add_many()`
   - `ACL2Symbol.definedInCell` → link symbol to the cell(s) that define it

10. **Checkpoint & restartability**: maintain `scripts/.ingest_checkpoint.json` mapping `{source_file: {notebook_uuid, cell_count, timestamp}}`. On restart without `--force`, skip notebooks present in checkpoint. Checkpoint written after each notebook's cells are committed.

11. **Error handling**: wrap `client.close()` in `finally`. After each batch, log `batch.failed_objects`. Abort if error rate exceeds 5% of batch. All errors logged with notebook path + cell index for debugging.

12. **Create** scripts/requirements.txt with `weaviate-client>=4.5` and `nbformat>=5.0`.

---

### Verification

- **Dry run**: `python scripts/ingest_notebooks.py --dry-run --source-dir data/home/acl2` — parses all notebooks, prints stats, validates structure without touching Weaviate
- **Full import**: `python scripts/ingest_notebooks.py --recreate --source-dir data/home/acl2` — clean import from scratch
- **Idempotency**: run twice without `--recreate` — object counts must be identical
- **Spot check**: query `ACL2Symbol` for `COMMON-LISP::DEFUN`, verify it has many `dependsOn` back-links; query an `ACL2Cell` by `notebook_source` and fetch its `definesSymbols` targets
- **Comment search**: near-text query against `comment_vector` on `ACL2Cell` to verify markdown cells are searchable independently of code

### Decisions

- **No JSON blob fields in Weaviate** — `events`, `forms`, `expansions`, raw `symbols`, raw `dependencies` stay in `.ipynb` files; fetchable by `notebook_source` path + `cell_index`
- **Portcullis unpacked** into `OBJECT_ARRAY` with `filename`/`content` nested properties on `ACL2Notebook` rather than a serialized JSON string
- **Two text fields** (`comment_text` / `code_text`) instead of one `source` field — Weaviate vectorizer source properties are schema-level, so the only way to have independent named vectors for comments vs. code is to use separate properties
- **Symbol kind conflict resolution**: when the same `PACKAGE::NAME` appears across cells with different `kind` values, keep the most specific non-`unknown` value (precedence: `function` > `macro` > `theorem` > `constant` > `stobj` > `variable` > `raw-function` > `special-form` > `unknown`)
- **`stdout` kept** in `ACL2Cell` despite being fetchable from `.ipynb` — ACL2 proof output text is valuable for search; can be omitted later if storage is a concern
- **Checkpoint file** for restartability rather than querying Weaviate for existing objects — avoids expensive full-collection scans on large datasets
