#!/usr/bin/env python3
"""Interactive browser for the ACL2 knowledge graph stored in Weaviate.

Usage:
    pip install flask
    python scripts/kg_browser.py [--port 5000] [--debug]
"""

import atexit
import argparse
import os

from flask import (Flask, render_template, request, redirect,
                   url_for, abort)
import weaviate
from weaviate.classes.query import QueryReference, Filter, MetadataQuery
from weaviate.util import generate_uuid5

app = Flask(__name__)

# ── Weaviate connection ──────────────────────────────────────────────

_client = None
_cfg = {
    "host": os.environ.get("WEAVIATE_HOST", "host.docker.internal"),
    "http_port": int(os.environ.get("WEAVIATE_HTTP_PORT", "8080")),
    "grpc_port": int(os.environ.get("WEAVIATE_GRPC_PORT", "50051")),
}


def _get_client():
    global _client
    if _client is None:
        _client = weaviate.connect_to_custom(
            http_host=_cfg["host"], http_port=_cfg["http_port"],
            http_secure=False,
            grpc_host=_cfg["host"], grpc_port=_cfg["grpc_port"],
            grpc_secure=False,
        )
        atexit.register(_client.close)
    return _client


# ── Jinja helpers ────────────────────────────────────────────────────

KIND_COLORS = {
    "function": "primary",
    "macro": "success",
    "theorem": "info",
    "constant": "warning",
    "stobj": "danger",
    "variable": "secondary",
    "special-form": "dark",
    "raw-function": "primary",
    "unknown": "secondary",
}

app.jinja_env.globals["kind_color"] = lambda k: KIND_COLORS.get(k, "light")
app.jinja_env.filters["commas"] = lambda n: f"{n:,}" if n else "0"


# ── Routes ───────────────────────────────────────────────────────────

@app.route("/")
def index():
    client = _get_client()
    stats = {}
    for name in ["ACL2Notebook", "ACL2Cell", "ACL2Symbol"]:
        col = client.collections.get(name)
        resp = col.aggregate.over_all(total_count=True)
        stats[name] = resp.total_count

    # Summary stats (may not exist yet)
    try:
        col = client.collections.get("ACL2Summary")
        resp = col.aggregate.over_all(total_count=True)
        stats["ACL2Summary"] = resp.total_count
    except Exception:
        stats["ACL2Summary"] = 0

    sym = client.collections.get("ACL2Symbol")
    resp = sym.aggregate.over_all(group_by="kind", total_count=True)
    kinds = sorted(
        [(g.grouped_by.value or "(none)", g.total_count) for g in resp.groups],
        key=lambda x: -x[1],
    )

    return render_template("index.html", stats=stats, kinds=kinds)


@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    target = request.args.get("target", "symbol")
    mode = request.args.get("mode", "semantic")
    limit = min(int(request.args.get("limit", "50")), 200)

    if not q:
        return redirect(url_for("index"))

    client = _get_client()
    results = []

    if target == "symbol":
        sym = client.collections.get("ACL2Symbol")
        if mode == "semantic":
            resp = sym.query.near_text(
                query=q, limit=limit, target_vector="symbol_vector",
                return_metadata=MetadataQuery(distance=True),
                return_references=[
                    QueryReference(link_on="definedInCell",
                                   return_properties=["cell_index", "notebook_source"]),
                ],
            )
        else:
            resp = sym.query.fetch_objects(
                filters=Filter.by_property("qualified_name").like(f"*{q}*"),
                limit=limit,
                return_references=[
                    QueryReference(link_on="definedInCell",
                                   return_properties=["cell_index", "notebook_source"]),
                ],
            )
        for obj in resp.objects:
            dist = None
            if obj.metadata:
                dist = getattr(obj.metadata, "distance", None)
            # Get summary for the defining cell
            summary_what = ""
            cell_ref = obj.references.get("definedInCell")
            if cell_ref and cell_ref.objects:
                c = cell_ref.objects[0].properties
                nb_src = c.get("notebook_source", "")
                ci = c.get("cell_index", -1)
                if nb_src and ci >= 0:
                    sums = _get_cell_summaries(client, nb_src)
                    s = sums.get(ci)
                    if s:
                        summary_what = s.get("what", "")
            results.append({
                "type": "symbol",
                "qn": obj.properties["qualified_name"],
                "kind": obj.properties.get("kind", ""),
                "package": obj.properties.get("package", ""),
                "distance": dist,
                "summary_what": summary_what,
            })

    elif target in ("code", "comment"):
        cell = client.collections.get("ACL2Cell")
        vec = "code_vector" if target == "code" else "comment_vector"
        prop = "code_text" if target == "code" else "comment_text"

        if mode == "semantic":
            resp = cell.query.near_text(
                query=q, limit=limit, target_vector=vec,
                return_metadata=MetadataQuery(distance=True),
            )
        else:
            resp = cell.query.fetch_objects(
                filters=Filter.by_property(prop).like(f"*{q}*"),
                limit=limit,
            )
        for obj in resp.objects:
            text = obj.properties.get(prop) or ""
            dist = None
            if obj.metadata:
                dist = getattr(obj.metadata, "distance", None)
            # Get cell summary
            nb_src = obj.properties.get("notebook_source", "")
            ci = obj.properties.get("cell_index", 0)
            summary_what = ""
            if nb_src:
                sums = _get_cell_summaries(client, nb_src)
                s = sums.get(ci)
                if s:
                    summary_what = s.get("what", "")
            results.append({
                "type": target,
                "notebook": nb_src,
                "cell_index": ci,
                "cell_type": obj.properties.get("cell_type", ""),
                "preview": text[:300],
                "distance": dist,
                "summary_what": summary_what,
            })

    elif target == "summary":
        try:
            col = client.collections.get("ACL2Summary")
        except Exception:
            col = None
        if col:
            if mode == "semantic":
                resp = col.query.near_text(
                    query=q, limit=limit, target_vector="what_vector",
                    return_metadata=MetadataQuery(distance=True),
                )
            else:
                resp = col.query.fetch_objects(
                    filters=Filter.by_property("what_summary").like(f"*{q}*"),
                    limit=limit,
                )
            for obj in resp.objects:
                p = obj.properties
                dist = None
                if obj.metadata:
                    dist = getattr(obj.metadata, "distance", None)
                results.append({
                    "type": "summary",
                    "scope": p.get("scope", ""),
                    "ref_key": p.get("ref_key", ""),
                    "what": p.get("what_summary", ""),
                    "why": p.get("why_summary", ""),
                    "source_file": p.get("source_file", ""),
                    "cell_index": p.get("cell_index", -1),
                    "symbol_names": p.get("symbol_names", []),
                    "distance": dist,
                })

    return render_template("search_results.html",
                           q=q, target=target, mode=mode,
                           results=results, count=len(results))


@app.route("/symbol")
def symbol_detail():
    qn = request.args.get("qn", "").strip()
    if not qn:
        return redirect(url_for("index"))

    client = _get_client()
    sym = client.collections.get("ACL2Symbol")

    # Use deterministic UUID for exact lookup (avoids word-tokenization
    # issues with special chars like @ : - in qualified names).
    symbol_uuid = generate_uuid5(f"symbol:{qn}")
    obj = sym.query.fetch_object_by_id(
        symbol_uuid,
        return_references=[
            QueryReference(link_on="dependsOn",
                           return_properties=["qualified_name", "kind",
                                              "package"]),
            QueryReference(link_on="definedInCell",
                           return_properties=["cell_index", "notebook_source",
                                              "code_text", "comment_text"]),
        ],
    )

    if obj is None:
        abort(404)
    symbol = dict(obj.properties)

    # Dependencies
    deps_ref = obj.references.get("dependsOn")
    deps = []
    if deps_ref and deps_ref.objects:
        deps = sorted(
            [{"qn": d.properties["qualified_name"],
              "kind": d.properties.get("kind", "unknown"),
              "package": d.properties.get("package", "")}
             for d in deps_ref.objects],
            key=lambda x: x["qn"],
        )

    # Defining cell
    cell_ref = obj.references.get("definedInCell")
    defining_cell = None
    if cell_ref and cell_ref.objects:
        c = cell_ref.objects[0].properties
        defining_cell = {
            "cell_index": c["cell_index"],
            "notebook": c["notebook_source"],
            "code": c.get("code_text") or c.get("comment_text") or "",
        }

    # Reverse dependencies (symbols that depend on this one)
    reverse_deps = []
    try:
        rev_resp = sym.query.fetch_objects(
            filters=Filter.by_ref("dependsOn").by_id().equal(symbol_uuid),
            limit=50,
        )
        reverse_deps = sorted(
            [{"qn": r.properties["qualified_name"],
              "kind": r.properties.get("kind", "unknown")}
             for r in rev_resp.objects],
            key=lambda x: x["qn"],
        )
    except Exception:
        pass  # Reference filters may not be supported in all versions

    # Cell summary for the defining cell
    cell_summary = None
    if defining_cell:
        sums = _get_cell_summaries(client, defining_cell["notebook"])
        cell_summary = sums.get(defining_cell["cell_index"])

    return render_template("symbol.html",
                           symbol=symbol, deps=deps,
                           defining_cell=defining_cell,
                           reverse_deps=reverse_deps,
                           cell_summary=cell_summary)


@app.route("/notebook/<path:source_file>")
def notebook_view(source_file):
    client = _get_client()

    # Notebook metadata
    nb_col = client.collections.get("ACL2Notebook")
    nb_resp = nb_col.query.fetch_objects(
        filters=Filter.by_property("source_file").equal(source_file),
        limit=1,
    )
    notebook = nb_resp.objects[0].properties if nb_resp.objects else {}

    # All cells with their defined symbols
    cell_col = client.collections.get("ACL2Cell")
    resp = cell_col.query.fetch_objects(
        filters=Filter.by_property("notebook_source").equal(source_file),
        limit=10000,
        return_references=[
            QueryReference(link_on="definesSymbols",
                           return_properties=["qualified_name", "kind"]),
        ],
    )

    cells = []
    seen_indices: set[int] = set()
    for obj in resp.objects:
        # Weaviate TEXT tokenization can match related paths;
        # post-filter to exact source_file match.
        if obj.properties.get("notebook_source") != source_file:
            continue
        idx = obj.properties["cell_index"]
        if idx in seen_indices:
            continue
        seen_indices.add(idx)

        syms_ref = obj.references.get("definesSymbols")
        defined_symbols = []
        if syms_ref and syms_ref.objects:
            defined_symbols = sorted(
                [{"qn": s.properties["qualified_name"],
                  "kind": s.properties.get("kind", "unknown")}
                 for s in syms_ref.objects],
                key=lambda x: x["qn"],
            )
        cells.append({
            "index": idx,
            "type": obj.properties["cell_type"],
            "code_text": obj.properties.get("code_text") or "",
            "comment_text": obj.properties.get("comment_text") or "",
            "package": obj.properties.get("package") or "",
            "execution_count": obj.properties.get("execution_count"),
            "is_portcullis": obj.properties.get("is_portcullis", False),
            "defined_symbols": defined_symbols,
        })

    cells.sort(key=lambda c: c["index"])
    highlight = request.args.get("cell", type=int)

    # Fetch summaries
    cell_sums = _get_cell_summaries(client, source_file)
    nb_summary = _get_notebook_summary(client, source_file)

    return render_template("notebook.html",
                           notebook=notebook, source_file=source_file,
                           cells=cells, highlight=highlight,
                           cell_summaries=cell_sums,
                           nb_summary=nb_summary)


@app.route("/notebooks")
def notebook_list():
    q = request.args.get("q", "").strip()
    client = _get_client()
    nb_col = client.collections.get("ACL2Notebook")

    if q:
        resp = nb_col.query.fetch_objects(
            filters=Filter.by_property("source_file").like(f"*{q}*"),
            limit=200,
        )
    else:
        resp = nb_col.query.fetch_objects(limit=100)

    notebooks = sorted(
        [obj.properties for obj in resp.objects],
        key=lambda x: x["source_file"],
    )

    # Fetch notebook summaries
    nb_summaries = {}
    for nb in notebooks:
        sf = nb["source_file"]
        s = _get_notebook_summary(client, sf)
        if s and s.get("what"):
            nb_summaries[sf] = s["what"]

    return render_template("notebooks.html", notebooks=notebooks, q=q,
                           nb_summaries=nb_summaries)


# ── Summary routes ───────────────────────────────────────────────────

@app.route("/summaries")
def summaries():
    """Browse summaries: semantic search, or list by scope."""
    q = request.args.get("q", "").strip()
    scope = request.args.get("scope", "all")
    vector = request.args.get("vector", "what_vector")
    limit = min(int(request.args.get("limit", "50")), 200)

    client = _get_client()
    try:
        col = client.collections.get("ACL2Summary")
    except Exception:
        return render_template("summaries.html", q=q, scope=scope,
                               vector=vector, results=[], count=0,
                               error="ACL2Summary collection not found. Run summarize_kg.py first.")

    results = []

    if q:
        # Semantic search across summaries
        resp = col.query.near_text(
            query=q, limit=limit, target_vector=vector,
            return_metadata=MetadataQuery(distance=True),
        )
        for obj in resp.objects:
            p = obj.properties
            if scope != "all" and p.get("scope") != scope:
                continue
            dist = getattr(obj.metadata, "distance", None) if obj.metadata else None
            results.append({
                "scope": p.get("scope", ""),
                "ref_key": p.get("ref_key", ""),
                "what": p.get("what_summary", ""),
                "why": p.get("why_summary", ""),
                "how": p.get("how_summary", ""),
                "source_file": p.get("source_file", ""),
                "cell_index": p.get("cell_index", -1),
                "directory": p.get("directory", ""),
                "symbol_names": p.get("symbol_names", []),
                "distance": dist,
            })
    else:
        # List by scope
        if scope == "all":
            filt = None
        else:
            filt = Filter.by_property("scope").equal(scope)
        resp = col.query.fetch_objects(filters=filt, limit=limit)
        for obj in resp.objects:
            p = obj.properties
            results.append({
                "scope": p.get("scope", ""),
                "ref_key": p.get("ref_key", ""),
                "what": p.get("what_summary", ""),
                "why": p.get("why_summary", ""),
                "how": p.get("how_summary", ""),
                "source_file": p.get("source_file", ""),
                "cell_index": p.get("cell_index", -1),
                "directory": p.get("directory", ""),
                "symbol_names": p.get("symbol_names", []),
                "distance": None,
            })
        results.sort(key=lambda r: (r["scope"], r["ref_key"]))

    return render_template("summaries.html", q=q, scope=scope,
                           vector=vector, results=results, count=len(results),
                           error=None)


@app.route("/summary/<path:ref_key>")
def summary_detail(ref_key):
    """Show a single summary and its context."""
    client = _get_client()
    try:
        col = client.collections.get("ACL2Summary")
    except Exception:
        abort(404)

    resp = col.query.fetch_objects(
        filters=Filter.by_property("ref_key").equal(ref_key),
        limit=1,
    )
    if not resp.objects:
        abort(404)

    p = resp.objects[0].properties
    summary = {
        "scope": p.get("scope", ""),
        "ref_key": p.get("ref_key", ""),
        "what": p.get("what_summary", ""),
        "why": p.get("why_summary", ""),
        "how": p.get("how_summary", ""),
        "source_file": p.get("source_file", ""),
        "cell_index": p.get("cell_index", -1),
        "directory": p.get("directory", ""),
        "symbol_names": p.get("symbol_names", []),
    }

    return render_template("summary_detail.html", summary=summary)


def _get_cell_summaries(client, source_file):
    """Fetch all cell-level summaries for a notebook, as a dict keyed by cell_index."""
    try:
        col = client.collections.get("ACL2Summary")
    except Exception:
        return {}

    resp = col.query.fetch_objects(
        filters=(
            Filter.by_property("scope").equal("cell")
            & Filter.by_property("source_file").equal(source_file)
        ),
        limit=10000,
    )

    sums = {}
    for obj in resp.objects:
        p = obj.properties
        if p.get("source_file") != source_file:
            continue
        idx = p.get("cell_index", -1)
        if idx >= 0:
            sums[idx] = {
                "what": p.get("what_summary", ""),
                "why": p.get("why_summary", ""),
                "how": p.get("how_summary", ""),
            }
    return sums


def _get_notebook_summary(client, source_file):
    """Fetch the notebook-level summary."""
    try:
        col = client.collections.get("ACL2Summary")
    except Exception:
        return None

    resp = col.query.fetch_objects(
        filters=(
            Filter.by_property("scope").equal("notebook")
            & Filter.by_property("source_file").equal(source_file)
        ),
        limit=1,
    )
    for obj in resp.objects:
        p = obj.properties
        if p.get("source_file") == source_file:
            return {
                "what": p.get("what_summary", ""),
                "why": p.get("why_summary", ""),
                "how": p.get("how_summary", ""),
            }
    return None


# ── Main ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="ACL2 Knowledge Graph Browser")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--weaviate-host", default=None,
                   help="Override WEAVIATE_HOST env var")
    p.add_argument("--debug", action="store_true")
    args = p.parse_args()

    if args.weaviate_host:
        _cfg["host"] = args.weaviate_host

    app.run(host=args.host, port=args.port, debug=args.debug)
