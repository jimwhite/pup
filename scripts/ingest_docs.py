#!/usr/bin/env python3
"""
Ingest ACL2 documentation (PDFs, READMEs, HTML) into Weaviate.

Three document types:
  - PDFs: Converted via Docling CLI to JSON, then chunked
  - READMEs: Read as plain text/markdown, chunked directly
  - HTML: Parsed with BeautifulSoup to extract text, chunked

All chunks are embedded with Ollama nomic-embed-text and stored in the
ACL2Docs Weaviate collection.

Usage:
    python ingest_docs.py --phase pdf     [--recreate]
    python ingest_docs.py --phase readme
    python ingest_docs.py --phase html
    python ingest_docs.py --phase all     [--recreate]

    # Or with custom paths:
    python ingest_docs.py --phase pdf --acl2-dir /path/to/acl2 --docling-dir /path/to/output
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import weaviate
from bs4 import BeautifulSoup
from langchain_core.documents import Document
from langchain_ollama import OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_weaviate import WeaviateVectorStore


# --- Defaults ---
DEFAULT_ACL2_DIR = "/workspaces/pup/external/acl2"
DEFAULT_DOCLING_DIR = "/workspaces/pup/docling/acl2-pdfs"
DEFAULT_COLLECTION = "ACL2Docs"
DEFAULT_WEAVIATE_HOST = "host.docker.internal"
DEFAULT_WEAVIATE_PORT = 8080
DEFAULT_WEAVIATE_GRPC_PORT = 50051
DEFAULT_OLLAMA_HOST = "host.docker.internal"
DEFAULT_OLLAMA_PORT = 11434
DEFAULT_EMBED_MODEL = "nomic-embed-text:latest"

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
MIN_CHUNK_LENGTH = 50
INGEST_BATCH_SIZE = 50


# ─────────────────────────────────────────────────────────────
# File discovery
# ─────────────────────────────────────────────────────────────

def find_pdfs(acl2_dir: str) -> list[str]:
    """Find all PDF files under acl2_dir."""
    return sorted(glob.glob(os.path.join(acl2_dir, "**", "*.pdf"), recursive=True))


def find_readmes(acl2_dir: str) -> list[str]:
    """Find all README* files, excluding docs/ directory (duplicates)."""
    results = []
    for root, dirs, files in os.walk(acl2_dir):
        # Skip docs/ directory at the root level
        rel = os.path.relpath(root, acl2_dir)
        if rel == "docs" or rel.startswith("docs/"):
            continue
        for f in files:
            if f.upper().startswith("README"):
                results.append(os.path.join(root, f))
    return sorted(results)


def find_html_files(acl2_dir: str) -> list[str]:
    """Find all HTML files, excluding docs/ duplicates.

    Include:
    - All *.html outside docs/
    - docs/*.html files that don't have a root-level duplicate
    """
    root_html = set()
    results = []

    # First, collect root-level HTML basenames
    for f in os.listdir(acl2_dir):
        if f.endswith(".html"):
            root_html.add(f)

    # Walk everything except docs/
    for root, dirs, files in os.walk(acl2_dir):
        rel = os.path.relpath(root, acl2_dir)
        if rel == "docs" or rel.startswith("docs/"):
            continue
        for f in files:
            if f.endswith(".html"):
                results.append(os.path.join(root, f))

    # Add docs/-only HTML (not duplicated at root)
    docs_dir = os.path.join(acl2_dir, "docs")
    if os.path.isdir(docs_dir):
        for f in os.listdir(docs_dir):
            if f.endswith(".html") and f not in root_html:
                results.append(os.path.join(docs_dir, f))

    return sorted(results)


# ─────────────────────────────────────────────────────────────
# Text extraction
# ─────────────────────────────────────────────────────────────

def extract_text_from_html(filepath: str) -> tuple[str, str]:
    """Extract text from an HTML file using BeautifulSoup.

    Returns (title, text).
    """
    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        print(f"  Warning: could not read {filepath}: {e}")
        return "", ""

    soup = BeautifulSoup(content, "html.parser")

    # Remove script and style elements
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()

    # Get title
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else Path(filepath).stem

    # Get text
    text = soup.get_text(separator="\n", strip=True)

    # Clean up excessive whitespace
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)

    return title, text


def extract_text_from_readme(filepath: str) -> tuple[str, str]:
    """Read a README file as plain text.

    Returns (title, text).
    """
    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception as e:
        print(f"  Warning: could not read {filepath}: {e}")
        return "", ""

    # Use directory name + filename as title
    parent = Path(filepath).parent.name
    name = Path(filepath).name
    title = f"{parent}/{name}" if parent else name

    return title, text


def extract_text_from_docling_json(filepath: str) -> tuple[str, str]:
    """Extract text from a Docling JSON file.

    Returns (title, text).
    """
    try:
        with open(filepath) as f:
            doc = json.load(f)
    except Exception as e:
        print(f"  Warning: could not read {filepath}: {e}")
        return "", ""

    title = doc.get("name", Path(filepath).stem)
    texts = doc.get("texts", [])

    parts = []
    for item in texts:
        label = item.get("label", "")
        text = item.get("text", "").strip()
        if not text:
            continue
        if label == "section_header":
            level = item.get("level", 1)
            prefix = "#" * min(level + 1, 4)
            parts.append(f"\n{prefix} {text}\n")
        elif label in ("page_header", "page_footer"):
            continue
        else:
            parts.append(text)

    return title, "\n\n".join(parts)


# ─────────────────────────────────────────────────────────────
# Docling PDF conversion
# ─────────────────────────────────────────────────────────────

def convert_pdfs_with_docling(pdf_files: list[str], output_dir: str) -> list[str]:
    """Convert PDF files to Docling JSON using the docling CLI.

    Returns list of generated JSON file paths.
    """
    os.makedirs(output_dir, exist_ok=True)

    # Check which PDFs are already converted
    existing = set()
    for f in glob.glob(os.path.join(output_dir, "**", "*.json"), recursive=True):
        existing.add(Path(f).stem)

    to_convert = []
    already_done = []
    for pdf in pdf_files:
        stem = Path(pdf).stem
        if stem in existing:
            already_done.append(pdf)
        else:
            to_convert.append(pdf)

    if already_done:
        print(f"  {len(already_done)} PDFs already converted, skipping")

    if to_convert:
        print(f"  Converting {len(to_convert)} PDFs with Docling...")
        # Convert in small batches to show progress
        batch_size = 10
        for i in range(0, len(to_convert), batch_size):
            batch = to_convert[i:i + batch_size]
            for pdf in batch:
                print(f"    Converting: {os.path.basename(pdf)}")
                try:
                    subprocess.run(
                        ["docling", "--vlm-model", "granite_docling",
                         pdf, "--to", "json", "--to", "md",
                         "--output", output_dir],
                        capture_output=True, text=True, timeout=300,
                    )
                except subprocess.TimeoutExpired:
                    print(f"    Warning: timeout converting {pdf}")
                except Exception as e:
                    print(f"    Warning: error converting {pdf}: {e}")

    # Collect all JSON files
    return sorted(glob.glob(os.path.join(output_dir, "**", "*.json"), recursive=True))


# ─────────────────────────────────────────────────────────────
# Chunking
# ─────────────────────────────────────────────────────────────

def chunk_documents(
    files: list[str],
    doc_type: str,
    acl2_dir: str,
    extract_fn,
) -> list[Document]:
    """Extract text, chunk, and create LangChain Documents.

    Args:
        files: list of file paths
        doc_type: 'pdf', 'readme', or 'html'
        acl2_dir: base ACL2 directory for computing relative paths
        extract_fn: function(filepath) -> (title, text)
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n## ", "\n### ", "\n#### ", "\n\n", "\n", ". ", " ", ""],
    )

    all_docs: list[Document] = []
    skipped_short = 0
    skipped_empty = 0

    for filepath in files:
        title, text = extract_fn(filepath)
        if not text or len(text.strip()) < MIN_CHUNK_LENGTH:
            skipped_empty += 1
            continue

        # Compute path relative to acl2_dir
        try:
            rel_path = os.path.relpath(filepath, acl2_dir)
        except ValueError:
            rel_path = filepath

        chunks = splitter.create_documents(
            texts=[text],
            metadatas=[{
                "title": title,
                "source_path": rel_path,
                "doc_type": doc_type,
            }],
        )
        for chunk in chunks:
            if len(chunk.page_content.strip()) < MIN_CHUNK_LENGTH:
                skipped_short += 1
                continue
            all_docs.append(chunk)

    print(f"  → {len(all_docs)} chunks from {len(files)} {doc_type} files "
          f"({skipped_empty} empty, {skipped_short} short chunks skipped)")
    return all_docs


# ─────────────────────────────────────────────────────────────
# Weaviate ingestion
# ─────────────────────────────────────────────────────────────

def connect_weaviate(host: str, port: int, grpc_port: int) -> weaviate.WeaviateClient:
    """Connect to Weaviate."""
    client = weaviate.connect_to_local(host=host, port=port, grpc_port=grpc_port)
    if not client.is_ready():
        print(f"Weaviate at {host}:{port} is not ready")
        sys.exit(1)
    print(f"Connected to Weaviate at {host}:{port}")
    return client


def ingest_chunks(
    docs: list[Document],
    client: weaviate.WeaviateClient,
    collection_name: str,
    embeddings: OllamaEmbeddings,
    recreate: bool = False,
) -> None:
    """Ingest document chunks into Weaviate."""
    if recreate and client.collections.exists(collection_name):
        print(f"Deleting existing collection '{collection_name}'...")
        client.collections.delete(collection_name)

    print(f"Ingesting {len(docs)} chunks into '{collection_name}'...")
    start = time.time()

    vs = None
    for i in range(0, len(docs), INGEST_BATCH_SIZE):
        batch = docs[i:i + INGEST_BATCH_SIZE]
        if vs is None:
            vs = WeaviateVectorStore.from_documents(
                documents=batch,
                embedding=embeddings,
                client=client,
                index_name=collection_name,
                text_key="text",
            )
        else:
            vs.add_documents(batch)
        done = min(i + INGEST_BATCH_SIZE, len(docs))
        elapsed = time.time() - start
        rate = done / elapsed if elapsed > 0 else 0
        eta = (len(docs) - done) / rate if rate > 0 else 0
        print(f"  → {done:,}/{len(docs):,} ({elapsed:.0f}s, {rate:.0f}/s, ETA {eta:.0f}s)")

    elapsed = time.time() - start
    print(f"Ingestion complete: {len(docs):,} chunks in {elapsed:.1f}s")


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Ingest ACL2 documentation into Weaviate"
    )
    parser.add_argument(
        "--phase", required=True,
        choices=["pdf", "readme", "html", "all"],
        help="Which document type(s) to ingest",
    )
    parser.add_argument("--acl2-dir", default=DEFAULT_ACL2_DIR)
    parser.add_argument("--docling-dir", default=DEFAULT_DOCLING_DIR,
                        help="Output dir for Docling PDF conversions")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--weaviate-host", default=DEFAULT_WEAVIATE_HOST)
    parser.add_argument("--weaviate-port", type=int, default=DEFAULT_WEAVIATE_PORT)
    parser.add_argument("--weaviate-grpc-port", type=int, default=DEFAULT_WEAVIATE_GRPC_PORT)
    parser.add_argument("--ollama-host", default=DEFAULT_OLLAMA_HOST)
    parser.add_argument("--ollama-port", type=int, default=DEFAULT_OLLAMA_PORT)
    parser.add_argument("--embed-model", default=DEFAULT_EMBED_MODEL)
    parser.add_argument("--recreate", action="store_true",
                        help="Delete and recreate the collection (only on first phase)")
    args = parser.parse_args()

    phases = [args.phase] if args.phase != "all" else ["pdf", "readme", "html"]
    acl2_dir = args.acl2_dir

    # Collect all chunks across phases
    all_chunks: list[Document] = []

    for phase in phases:
        print(f"\n{'='*60}")
        print(f"Phase: {phase.upper()}")
        print(f"{'='*60}")

        if phase == "pdf":
            # Read pre-converted Docling JSON files.
            # Run scripts/docling-acl2-pdfs.sh on the host (macOS/MLX) first.
            json_files = sorted(
                glob.glob(os.path.join(args.docling_dir, "**", "*.json"), recursive=True)
            )
            print(f"Found {len(json_files)} Docling JSON files in {args.docling_dir}")
            if not json_files:
                print("  (Run scripts/docling-acl2-pdfs.sh on the host first)")
                continue

            chunks = chunk_documents(
                json_files, "pdf", acl2_dir,
                extract_text_from_docling_json,
            )
            all_chunks.extend(chunks)

        elif phase == "readme":
            readme_files = find_readmes(acl2_dir)
            print(f"Found {len(readme_files)} README files")
            if not readme_files:
                continue

            chunks = chunk_documents(
                readme_files, "readme", acl2_dir,
                extract_text_from_readme,
            )
            all_chunks.extend(chunks)

        elif phase == "html":
            html_files = find_html_files(acl2_dir)
            print(f"Found {len(html_files)} HTML files (excluding docs/ duplicates)")
            if not html_files:
                continue

            chunks = chunk_documents(
                html_files, "html", acl2_dir,
                extract_text_from_html,
            )
            all_chunks.extend(chunks)

    if not all_chunks:
        print("\nNo chunks to ingest.")
        return

    print(f"\n{'='*60}")
    print(f"Total: {len(all_chunks):,} chunks to ingest")
    print(f"{'='*60}")

    # Set up embeddings
    ollama_url = f"http://{args.ollama_host}:{args.ollama_port}"
    embeddings = OllamaEmbeddings(model=args.embed_model, base_url=ollama_url)
    print(f"Embeddings: {args.embed_model} via {ollama_url}")

    # Connect and ingest
    client = connect_weaviate(
        args.weaviate_host, args.weaviate_port, args.weaviate_grpc_port,
    )
    try:
        ingest_chunks(
            all_chunks, client, args.collection, embeddings,
            recreate=args.recreate,
        )
        # Show final count
        col = client.collections.get(args.collection)
        count = col.aggregate.over_all(total_count=True).total_count
        print(f"\nDone! '{args.collection}' now has {count:,} objects.")
    finally:
        client.close()


if __name__ == "__main__":
    main()
