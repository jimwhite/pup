

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

# Results of first ingestion:

=== Collection Counts ===
  ACL2Notebook: 14,544
  ACL2Cell: 507,645
  ACL2Symbol: 403,038

=== Sample Notebooks (first 15) ===
  books/kestrel/acl2-arrays/aref1.lisp  cells=16  code=15  bootstrap=False
  books/workshops/1999/graph/find-path3.lisp  cells=23  code=18  bootstrap=False
  books/projects/rp-rewriter/eval-functions.lisp  cells=40  code=35  bootstrap=False
  books/nonstd/nsa/nsa.lisp  cells=96  code=78  bootstrap=False
  books/std/system/function-namep-tests.lisp  cells=13  code=11  bootstrap=False
  books/workshops/2000/manolios/pipeline/pipeline/non-deterministic-systems/top/non-det-macros.lisp  cells=10  code=7  bootstrap=False
  books/models/jvm/m6/BCV/bcv-functions.lisp  cells=24  code=19  bootstrap=False
  books/kestrel/utilities/set-cbd-fn.lisp  cells=16  code=14  bootstrap=False
  books/projects/paco/books/proveall-book.lisp  cells=4  code=3  bootstrap=False
  books/kestrel/axe/call-axe-script.lisp  cells=13  code=11  bootstrap=False
  books/projects/apply-model-2/ex2/evaluation-user-defs.lisp  cells=68  code=56  bootstrap=False
  books/quicklisp/bundle/software/osicat-20220220-git/windows/windows.lisp  cells=58  code=49  bootstrap=False
  books/kestrel/utilities/array-stobj.lisp  cells=13  code=11  bootstrap=False
  books/std/system/fresh-logical-name-with-dollars-suffix-tests.lisp  cells=27  code=25  bootstrap=False
  books/kestrel/ethereum/semaphore/json-to-r1cs/load-circom-json.lisp  cells=27  code=21  bootstrap=False

=== Symbol Kinds Distribution ===
  theorem: 146,967
  unknown: 143,156
  function: 85,716
  macro: 19,498
  constant: 5,721
  raw-function: 1,272
  stobj: 362
  variable: 326
  special-form: 20

=== Cell Types Distribution ===
  code: 437,186
  markdown: 70,459

=== Sample Symbols with Dependencies ===
  ACL2::EXPAND-LAMBDAS-IN-TERMS-INDUCT-REMOVAL  kind=theorem  deps=13 in cell 36 of books/kestrel/terms-light/expand-lambdas-in-term-proofs.lisp
  ACL2S::P21  kind=unknown  deps=0
  ACL2::UPDATE-DATA-REGION-ALT-LEMMA-2  kind=unknown  deps=0
  ACL2::MAKE-SBITS  kind=unknown  deps=0
  STR::COERCION  kind=unknown  deps=0
  ACL2::NTH-0-CONS  kind=theorem  deps=7 in cell 1553 of axioms.lisp
  X86ISA::VPINSRQ  kind=unknown  deps=0
  ACL2::STEP8-NEGATIVE  kind=unknown  deps=22 in cell 55 of books/kestrel/crypto/ecurve/twisted-edwards-closure-core.lisp
  ACL2::INTEGERP-OF-BITOR  kind=theorem  deps=5 in cell 9 of books/kestrel/bv/bitor.lisp
  IRV::IRV-BALLOT-P-CDR  kind=theorem  deps=19 in cell 20 of books/projects/irv/irv.lisp

  === Collection Counts ===
  ACL2Notebook: 14,544
  ACL2Cell: 507,645
  ACL2Symbol: 403,038

  === Symbol Kinds ===
  theorem: 146,967
  unknown: 143,156
  function: 85,716
  macro: 19,498
  constant: 5,721
  raw-function: 1,272
  stobj: 362
  variable: 326
  special-form: 20

=== Cell Types ===
  code: 437,186
  markdown: 70,459

=== Source Types ===
  (none): 14,544

  === Semantic Search: "sorting algorithm" ===
  FLD::a * (b + c) = (a * b) + (a * c)  kind=theorem
  ACL2::(disjointp (list x y)) --- disjoint super-ranges  kind=unknown
  FUMON::(a * b) * c = a * (b * c)  kind=theorem
  FLD::(a * b) * c = a * (b * c)  kind=theorem
  FUTER::(a * b) * c = a * (b * c)  kind=theorem
  FLD::(a + b) * c = (a * c) + (b * c)  kind=unknown
  ACL2::(disjointp (list (range base1 offset1 length1) (range base2 offset2 length2))) --- 1  kind=unknown
  ACL2::(disjointp (list (range base1 offset1 length1) (range base2 offset2 length2))) --- 2  kind=unknown
  FUNPOL::(a + b) = 0 => a +Mo (b +Mo p) = p-lemma-2  kind=theorem
  FLD::a + (b + c) = b + (a + c)  kind=theorem

=== Semantic Search: "cryptographic hash" (comments) ===
  [books/kestrel/crypto/keccak/keccak.lisp] cell 191:
    ```
;; --------------------------------
;; Main bit-oriented hash functions.

;; These are the functions that are closes...
  [books/workshops/2000/sumners2/bdds/bdd-mgr.lisp] cell 59:
    ```
;;;; END data structure functions ;;;;

#|--------------------------------------------------------------------------...
  [books/kestrel/crypto/sha-2/sha-224.lisp] cell 3:
    ```
;A formal spec for the SHA-224 hash function, which is standardized
;; in FIPS PUB 180-4.  See:
;; http://nvlpubs.ni...
  [books/workshops/2009/sumners/support/kas.lisp] cell 325:
    ```
;; The following is the ratio from entries in the unique node hash-table and
;; the memo-table. Since we reuse the h...
  [bdd.lisp] cell 58:
    ```
; Having found the bucket associated with the hash-index, here is how
; we search it.
```

=== Semantic Search: "binary search tree" (code) ===
  [books/kestrel/data/treeset/internal/bst.lisp] cell 82:
    (define bstp
  ((tree treep))
  (declare (xargs :type-prescription (booleanp (bstp tree))))
  :parents (tree)
  :short "...
  [books/kestrel/data/treeset/internal/in.lisp] cell 80:
    (defrule tree-search-in-becomes-tree-in-when
  (implies (bstp tree)
           (equal (tree-search-in x tree)
          ...
  [books/meta/term-defuns.lisp] cell 14:
    (defun binary-op_tree (binary-op-name constant-name fix-name lst)
  (declare (xargs :guard (and (symbolp binary-op-name)...
  [books/projects/taspi/tree-generation/tree-gen-helper/basics.lisp] cell 15:
    ;Returns list of all possible rooted trees from adding x to
;; rooted binary tree tree
(defun addTaxa-rooted (x tree)
  ...
  [books/projects/taspi/tree-generation/heuristics/spr.lisp] cell 5:
    ;takes a well-formed piece (which means it is a binary tree,
; whose attaching point is defined by the root), with an un...

