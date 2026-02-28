#!/usr/bin/env python3
"""Scan ACL2 books/ for non-ASCII bytes, classify as ISO-8859-1 or UTF-8,
and generate a markdown report with GitHub links.

Usage:
    python3 scan-books-non-ascii.py [ACL2_DIR]

    ACL2_DIR defaults to /workspaces/pup/external/acl2
"""

import os
import sys
import glob
from collections import defaultdict

GITHUB_BASE = "https://github.com/acl2/acl2/blob/master"

# ISO-8859-1 byte → readable name for display
LATIN1_NAMES = {
    0xA0: "non-breaking space",
    0xA1: "¡ (inverted exclamation)",
    0xA2: "¢ (cent)",
    0xA3: "£ (pound)",
    0xA4: "¤ (currency)",
    0xA5: "¥ (yen)",
    0xA9: "© (copyright)",
    0xAB: "« (left guillemet)",
    0xAD: "soft hyphen",
    0xB0: "° (degree)",
    0xB7: "· (middle dot)",
    0xBB: "» (right guillemet)",
    0xBF: "¿ (inverted question mark)",
    0xC0: "À", 0xC1: "Á", 0xC2: "Â", 0xC3: "Ã", 0xC4: "Ä",
    0xC7: "Ç", 0xC8: "È", 0xC9: "É", 0xCA: "Ê",
    0xD1: "Ñ", 0xD3: "Ó", 0xDA: "Ú",
    0xDF: "ß",
    0xE0: "à", 0xE1: "á", 0xE2: "â", 0xE3: "ã", 0xE4: "ä",
    0xE7: "ç", 0xE8: "è", 0xE9: "é", 0xEA: "ê", 0xEB: "ë",
    0xED: "í", 0xEE: "î", 0xEF: "ï",
    0xF1: "ñ", 0xF3: "ó", 0xF4: "ô", 0xF6: "ö",
    0xF9: "ù", 0xFA: "ú", 0xFC: "ü",
}


def classify_file(fpath):
    """Classify a file as 'utf8' or 'iso-8859-1'.

    Returns (encoding, lines_info) where lines_info is a list of
    (line_number, line_text_decoded, non_ascii_details).
    """
    with open(fpath, 'rb') as f:
        data = f.read()

    # Check if valid UTF-8
    try:
        data.decode('utf-8')
        is_utf8 = True
    except UnicodeDecodeError:
        is_utf8 = False

    # Find all lines with non-ASCII bytes
    lines = data.split(b'\n')
    hits = []
    for line_num_0, line_bytes in enumerate(lines):
        non_ascii = [(col, b) for col, b in enumerate(line_bytes) if b > 127]
        if non_ascii:
            line_num = line_num_0 + 1
            if is_utf8:
                line_text = line_bytes.decode('utf-8', errors='replace')
            else:
                line_text = line_bytes.decode('iso-8859-1')
            # Describe the non-ASCII bytes, consolidating repeats
            from collections import Counter
            if is_utf8:
                byte_counts = Counter(byte_val for _, byte_val in non_ascii)
                details = []
                for byte_val, count in byte_counts.most_common():
                    tag = f"`0x{byte_val:02X}`"
                    details.append(f"{tag} ×{count}" if count > 1 else tag)
            else:
                byte_counts = Counter(byte_val for _, byte_val in non_ascii)
                details = []
                for byte_val, count in byte_counts.most_common():
                    name = LATIN1_NAMES.get(byte_val, f"0x{byte_val:02X}")
                    details.append(f"{name} ×{count}" if count > 1 else name)
            hits.append((line_num, line_text.strip(), details))

    encoding = 'utf8' if is_utf8 else 'iso-8859-1'
    return encoding, hits


def find_books_files(acl2_dir):
    """Find all Lisp source files under books/."""
    books_dir = os.path.join(acl2_dir, 'books')
    files = set()
    for ext in ('*.lisp', '*.lsp', '*.acl2', '*.cl'):
        files.update(glob.glob(os.path.join(books_dir, '**', ext), recursive=True))
    return sorted(files)


def github_link(relpath, line_num):
    """Return a markdown link to the file on GitHub."""
    return f"{GITHUB_BASE}/{relpath}#L{line_num}"


def main():
    acl2_dir = sys.argv[1] if len(sys.argv) > 1 else '/workspaces/pup/external/acl2'

    if not os.path.isdir(acl2_dir):
        print(f"Error: {acl2_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    files = find_books_files(acl2_dir)
    print(f"Scanning {len(files)} files under books/...", file=sys.stderr)

    iso_files = {}  # relpath -> [(line_num, line_text, details)]
    utf8_files = {}

    for fpath in files:
        try:
            encoding, hits = classify_file(fpath)
        except Exception as e:
            print(f"  Error reading {fpath}: {e}", file=sys.stderr)
            continue

        if not hits:
            continue

        relpath = os.path.relpath(fpath, acl2_dir)

        # Skip quicklisp files
        if 'quicklisp' in relpath:
            continue

        if encoding == 'iso-8859-1':
            iso_files[relpath] = hits
        else:
            utf8_files[relpath] = hits

    # Generate markdown
    md = []
    md.append("# Non-ASCII Characters in ACL2 Books\n")
    md.append("Source files in `books/` containing non-ASCII bytes, classified by encoding.\n")
    md.append(f"Scanned {len(files)} files. Found {len(iso_files)} ISO-8859-1 files "
              f"and {len(utf8_files)} UTF-8 files with non-ASCII characters.\n")
    md.append("Quicklisp vendored files are excluded.\n")

    # --- ISO-8859-1 section ---
    md.append(f"## ISO-8859-1 Encoded Files ({len(iso_files)} files)\n")
    md.append("These files contain raw ISO-8859-1 bytes (single bytes 0x80–0xFF) that are "
              "**not** valid UTF-8. They need conversion to be UTF-8 compatible.\n")

    for relpath in sorted(iso_files.keys()):
        hits = iso_files[relpath]
        md.append(f"### [{relpath}]({GITHUB_BASE}/{relpath})\n")
        md.append("| Line | Characters | Context |")
        md.append("|------|-----------|---------|")
        for line_num, line_text, details in hits:
            link = f"[L{line_num}]({github_link(relpath, line_num)})"
            chars = ", ".join(details)
            # Escape pipes in context
            ctx = line_text.replace("|", "\\|")
            # Truncate long lines
            if len(ctx) > 100:
                ctx = ctx[:100] + "…"
            md.append(f"| {link} | {chars} | `{ctx}` |")
        md.append("")

    # --- UTF-8 section ---
    md.append(f"## Already Valid UTF-8 Files ({len(utf8_files)} files)\n")
    md.append("These files already contain valid UTF-8 multi-byte sequences. "
              "No conversion needed.\n")

    for relpath in sorted(utf8_files.keys()):
        hits = utf8_files[relpath]
        md.append(f"### [{relpath}]({GITHUB_BASE}/{relpath})\n")
        md.append("| Line | Context |")
        md.append("|------|---------|")
        for line_num, line_text, details in hits:
            link = f"[L{line_num}]({github_link(relpath, line_num)})"
            ctx = line_text.replace("|", "\\|")
            if len(ctx) > 100:
                ctx = ctx[:100] + "…"
            md.append(f"| {link} | `{ctx}` |")
        md.append("")

    print("\n".join(md))

    # Summary to stderr
    print(f"\nISO-8859-1 files: {len(iso_files)}", file=sys.stderr)
    total_iso_lines = sum(len(v) for v in iso_files.values())
    print(f"  Total lines with non-ASCII: {total_iso_lines}", file=sys.stderr)
    print(f"UTF-8 files: {len(utf8_files)}", file=sys.stderr)
    total_utf8_lines = sum(len(v) for v in utf8_files.values())
    print(f"  Total lines with non-ASCII: {total_utf8_lines}", file=sys.stderr)


if __name__ == '__main__':
    main()
