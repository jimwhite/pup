#!/usr/bin/env python3
"""Summary report of the ACL2 knowledge graph in Weaviate."""

import argparse
import weaviate
from weaviate.classes.query import QueryReference


def main():
    parser = argparse.ArgumentParser(description="Summarize the ACL2 knowledge graph")
    parser.add_argument("--host", default="host.docker.internal",
                        help="Weaviate HTTP host (default: host.docker.internal)")
    parser.add_argument("--http-port", type=int, default=8080)
    parser.add_argument("--grpc-port", type=int, default=50051)
    args = parser.parse_args()

    client = weaviate.connect_to_custom(
        http_host=args.host, http_port=args.http_port, http_secure=False,
        grpc_host=args.host, grpc_port=args.grpc_port, grpc_secure=False,
    )

    try:
        # ── Collection counts ────────────────────────────────────────
        print("=== Collection Counts ===")
        for name in ["ACL2Notebook", "ACL2Cell", "ACL2Symbol"]:
            col = client.collections.get(name)
            resp = col.aggregate.over_all(total_count=True)
            print(f"  {name}: {resp.total_count:,}")

        # ── Symbol kinds ─────────────────────────────────────────────
        print("\n=== Symbol Kinds ===")
        sym = client.collections.get("ACL2Symbol")
        resp = sym.aggregate.over_all(group_by="kind", total_count=True)
        for g in sorted(resp.groups, key=lambda x: x.total_count, reverse=True):
            print(f"  {g.grouped_by.value or '(none)'}: {g.total_count:,}")

        # ── Cell types ───────────────────────────────────────────────
        print("\n=== Cell Types ===")
        cell = client.collections.get("ACL2Cell")
        resp = cell.aggregate.over_all(group_by="cell_type", total_count=True)
        for g in sorted(resp.groups, key=lambda x: x.total_count, reverse=True):
            print(f"  {g.grouped_by.value}: {g.total_count:,}")

        # ── Source types ─────────────────────────────────────────────
        print("\n=== Source Types ===")
        nb = client.collections.get("ACL2Notebook")
        resp = nb.aggregate.over_all(group_by="source_type", total_count=True)
        for g in sorted(resp.groups, key=lambda x: x.total_count, reverse=True):
            print(f"  {g.grouped_by.value or '(none)'}: {g.total_count:,}")

        # ── Sample notebooks ────────────────────────────────────────
        print("\n=== Sample Notebooks (10) ===")
        results = nb.query.fetch_objects(limit=10)
        for obj in results.objects:
            p = obj.properties
            print(f"  {p['source_file']}  cells={p['cell_count']}  "
                  f"code={p['code_cell_count']}  bootstrap={p['is_bootstrap']}")

        # ── Sample symbols with graph edges ──────────────────────────
        print("\n=== Sample Symbols with Dependencies (10) ===")
        results = sym.query.fetch_objects(
            limit=10,
            return_references=[
                QueryReference(link_on="dependsOn",
                               return_properties=["qualified_name", "kind"]),
                QueryReference(link_on="definedInCell",
                               return_properties=["cell_index", "notebook_source"]),
            ],
        )
        for obj in results.objects:
            deps = obj.references.get("dependsOn")
            cell_ref = obj.references.get("definedInCell")
            dep_count = len(deps.objects) if deps and deps.objects else 0
            cell_info = ""
            if cell_ref and cell_ref.objects:
                c = cell_ref.objects[0].properties
                cell_info = (f"  defined in cell {c['cell_index']} "
                             f"of {c['notebook_source']}")
            print(f"  {obj.properties['qualified_name']}  "
                  f"kind={obj.properties['kind']}  deps={dep_count}{cell_info}")

        # ── Semantic search demos ────────────────────────────────────
        print("\n=== Semantic Search: 'sorting algorithm' (symbol_vector) ===")
        results = sym.query.near_text(
            query="sorting algorithm", limit=5, target_vector="symbol_vector")
        for obj in results.objects:
            print(f"  {obj.properties['qualified_name']}  kind={obj.properties['kind']}")

        print("\n=== Semantic Search: 'cryptographic hash' (comment_vector) ===")
        results = cell.query.near_text(
            query="cryptographic hash function", limit=5,
            target_vector="comment_vector")
        for obj in results.objects:
            src = obj.properties.get("comment_text") or ""
            preview = (src[:120] + "...") if len(src) > 120 else src
            print(f"  [{obj.properties['notebook_source']}] "
                  f"cell {obj.properties['cell_index']}:")
            print(f"    {preview}")

        print("\n=== Semantic Search: 'binary search tree' (code_vector) ===")
        results = cell.query.near_text(
            query="binary search tree", limit=5, target_vector="code_vector")
        for obj in results.objects:
            src = obj.properties.get("code_text") or ""
            preview = (src[:120] + "...") if len(src) > 120 else src
            print(f"  [{obj.properties['notebook_source']}] "
                  f"cell {obj.properties['cell_index']}:")
            print(f"    {preview}")

    finally:
        client.close()


if __name__ == "__main__":
    main()
