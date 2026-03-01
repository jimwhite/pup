#!/bin/bash
# Convert ACL2 PDFs to JSON and Markdown using Docling with VLM.
#
# Run on host macOS (needs MLX for granite_docling VLM model).
# From the pup repo root:
#   bash scripts/docling-acl2-pdfs.sh
#
# Skips books/doc/manual/ (duplicates of the originals).
# Skips books/projects/pdf-parser/hello.pdf (test file, not documentation).
#
# Output goes to docling/acl2-pdfs/ with subdirectories matching the source structure.

# Which models need pulling?
# ollama serve
# cd ~
# source .venv/bin/activate
#

set -e

ACL2_DIR=external/acl2
OUT_DIR=docling/acl2-pdfs

mkdir -p "$OUT_DIR/acl2s"
mkdir -p "$OUT_DIR/centaur-ubdds"
mkdir -p "$OUT_DIR/centaur-vl2014"
mkdir -p "$OUT_DIR/demos-big-proof"
mkdir -p "$OUT_DIR/demos-marktoberdorf"
mkdir -p "$OUT_DIR/kestrel-apt"
mkdir -p "$OUT_DIR/kestrel-design"
mkdir -p "$OUT_DIR/std-util-design"
mkdir -p "$OUT_DIR/jvm-m1"
mkdir -p "$OUT_DIR/workshops"

DOCCLING_ARGS="--vlm-model granite_docling --from pdf --to json --to md"

# ACL2s doc sheet
docling $DOCLING_ARGS \
  "$ACL2_DIR/books/acl2s/doc-assets/sheet.pdf" \
   --output "$OUT_DIR/acl2s"

# Centaur UBDD slides
docling $DOCLING_ARGS \
  "$ACL2_DIR/books/centaur/ubdds/slides/ubdds.pdf" \
  --output "$OUT_DIR/centaur-ubdds"

# Centaur VL2014 talks
docling $DOCLING_ARGS \
  "$ACL2_DIR/books/centaur/vl2014/talks/parser" \
  --output "$OUT_DIR/centaur-vl2014"

docling $DOCLING_ARGS \
  "$ACL2_DIR/books/centaur/vl2014/talks/translator/translator.pdf" \
  --output "$OUT_DIR/centaur-vl2014"

# Demo talks: big-proof
docling $DOCLING_ARGS \
  "$ACL2_DIR/books/demos/big-proof-talks" \
  --output "$OUT_DIR/demos-big-proof"

# Demo lectures: Marktoberdorf '08
docling $DOCLING_ARGS \
  "$ACL2_DIR/books/demos/marktoberdorf-08" \
  --output "$OUT_DIR/demos-marktoberdorf"

# Kestrel APT design notes
docling $DOCLING_ARGS \
  "$ACL2_DIR/books/kestrel/apt/design-notes" \
  --output "$OUT_DIR/kestrel-apt"

# Kestrel design notes (notation)
docling $DOCLING_ARGS \
  "$ACL2_DIR/books/kestrel/design-notes/notation.pdf" \
  --output "$OUT_DIR/kestrel-design"

# Std util design notes
docling $DOCLING_ARGS \
  "$ACL2_DIR/books/std/util/design-notes" \
  --output "$OUT_DIR/std-util-design"

# JVM M1 Turing equivalence talk (take the guard-verified version)
docling $DOCLING_ARGS \
  "$ACL2_DIR/books/models/jvm/guard-verified-m1/turing-equivalence-talk.pdf" \
  --output "$OUT_DIR/jvm-m1"

# Workshop papers
docling $DOCLING_ARGS \
  "$ACL2_DIR/books/workshops/2009/vandenbroek-schmaltz/GeNoC/docs" \
  --output "$OUT_DIR/workshops"

echo ""
echo "Done! Output in $OUT_DIR/"
find "$OUT_DIR" -name "*.json" | wc -l | xargs echo "JSON files:"
find "$OUT_DIR" -name "*.md"   | wc -l | xargs echo "MD files:"
