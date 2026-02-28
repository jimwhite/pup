mkdir -p docling
mkdir -p docling/acl2
mkdir -p docling/apprentice
mkdir -p docling/benchmarks
mkdir -p docling/coding
mkdir -p docling/goal-directed
mkdir -p docling/graphs
mkdir -p docling/kg
mkdir -p docling/lenat
mkdir -p docling/predicates
mkdir -p docling/recursive
mkdir -p docling/talks
mkdir -p docling/theory
mkdir -p docling/verifiable
mkdir -p docling/verification

docling --vlm-model granite_docling . --to json --to md --output docling
docling --vlm-model granite_docling acl2 --to json --to md --output docling/acl2
# docling --vlm-model granite_docling apprentice --to json --to md --output docling/apprentice
docling --vlm-model granite_docling benchmarks --to json --to md --output docling/benchmarks
docling --vlm-model granite_docling coding --to json --to md --output docling/coding
docling --vlm-model granite_docling goal-directed --to json --to md --output docling/goal-directed
docling --vlm-model granite_docling graphs --to json --to md --output docling/graphs
docling --vlm-model granite_docling kg --to json --to md --output docling/kg
docling --vlm-model granite_docling lenat --to json --to md --output docling/lenat
docling --vlm-model granite_docling predicates --to json --to md --output docling/predicates
docling --vlm-model granite_docling recursive --to json --to md --output docling/recursive
docling --vlm-model granite_docling talks --to json --to md --output docling/talks
docling --vlm-model granite_docling theory --to json --to md --output docling/theory
docling --vlm-model granite_docling verifiable --to json --to md --output docling/verifiable
docling --vlm-model granite_docling verification --to json --to md --output docling/verification
