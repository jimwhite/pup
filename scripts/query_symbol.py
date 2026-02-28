#!/usr/bin/env python3
"""Query a symbol's dependencies and definition from the ACL2 knowledge graph."""

import argparse
import weaviate
from weaviate.classes.query import QueryReference, Filter


def main():
    parser = argparse.ArgumentParser(description="Look up an ACL2 symbol in the KG")
    parser.add_argument("symbol", help="Qualified symbol name (e.g. ACL2::INTEGERP-OF-BITOR)")
    parser.add_argument("--host", default="host.docker.internal")
    parser.add_argument("--code", action="store_true", help="Show defining code")
    args = parser.parse_args()

    client = weaviate.connect_to_custom(
        http_host=args.host, http_port=8080, http_secure=False,
        grpc_host=args.host, grpc_port=50051, grpc_secure=False,
    )

    try:
        sym = client.collections.get("ACL2Symbol")
        results = sym.query.fetch_objects(
            filters=Filter.by_property("qualified_name").equal(args.symbol),
            limit=1,
            return_references=[
                QueryReference(link_on="dependsOn", return_properties=["qualified_name", "kind"]),
                QueryReference(link_on="definedInCell", return_properties=["cell_index", "notebook_source", "code_text"]),
            ],
        )

        if not results.objects:
            print(f"Symbol not found: {args.symbol}")
            return

        obj = results.objects[0]
        p = obj.properties
        print(f"Symbol: {p['qualified_name']}  kind={p['kind']}  is_operator={p['is_operator']}")

        cell_ref = obj.references.get("definedInCell")
        if cell_ref and cell_ref.objects:
            c = cell_ref.objects[0].properties
            print(f"Defined in: {c['notebook_source']}  cell {c['cell_index']}")
            if args.code:
                code = c.get("code_text") or ""
                if code:
                    print("Code:")
                    for line in code.splitlines():
                        print(f"  {line}")

        deps = obj.references.get("dependsOn")
        if deps and deps.objects:
            print(f"\nDependencies ({len(deps.objects)}):")
            for d in sorted(deps.objects, key=lambda x: x.properties["qualified_name"]):
                print(f"  -> {d.properties['qualified_name']}  ({d.properties['kind']})")
        else:
            print("\nNo dependencies recorded.")

    finally:
        client.close()


if __name__ == "__main__":
    main()
