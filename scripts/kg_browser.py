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
    "unknown": "light",
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
            )
        else:
            resp = sym.query.fetch_objects(
                filters=Filter.by_property("qualified_name").like(f"*{q}*"),
                limit=limit,
            )
        for obj in resp.objects:
            dist = None
            if obj.metadata:
                dist = getattr(obj.metadata, "distance", None)
            results.append({
                "type": "symbol",
                "qn": obj.properties["qualified_name"],
                "kind": obj.properties.get("kind", ""),
                "package": obj.properties.get("package", ""),
                "distance": dist,
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
            results.append({
                "type": target,
                "notebook": obj.properties.get("notebook_source", ""),
                "cell_index": obj.properties.get("cell_index", 0),
                "cell_type": obj.properties.get("cell_type", ""),
                "preview": text[:300],
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

    resp = sym.query.fetch_objects(
        filters=Filter.by_property("qualified_name").equal(qn),
        limit=1,
        return_references=[
            QueryReference(link_on="dependsOn",
                           return_properties=["qualified_name", "kind",
                                              "package"]),
            QueryReference(link_on="definedInCell",
                           return_properties=["cell_index", "notebook_source",
                                              "code_text", "comment_text"]),
        ],
    )

    if not resp.objects:
        abort(404)

    obj = resp.objects[0]
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
            filters=Filter.by_ref("dependsOn").by_property(
                "qualified_name").equal(qn),
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

    return render_template("symbol.html",
                           symbol=symbol, deps=deps,
                           defining_cell=defining_cell,
                           reverse_deps=reverse_deps)


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
    for obj in resp.objects:
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
            "index": obj.properties["cell_index"],
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

    return render_template("notebook.html",
                           notebook=notebook, source_file=source_file,
                           cells=cells, highlight=highlight)


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

    return render_template("notebooks.html", notebooks=notebooks, q=q)


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
