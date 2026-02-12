#!/usr/bin/env python3
"""Scan ACL2 source files for non-ASCII bytes.

Reports every non-ASCII byte found in .lisp, .lsp, .acl2, and .cl files
under the ACL2 source tree. Bytes are decoded as ISO-8859-1 for display
since that's ACL2's native encoding.

Usage:
    python3 scan-non-ascii.py [ACL2_DIR]

    ACL2_DIR defaults to /workspaces/pup/external/acl2
"""

import os
import sys
import glob
import json
from collections import defaultdict

def scan_file(fpath):
    """Return list of (line, col, byte_val, context_str) for non-ASCII bytes."""
    with open(fpath, 'rb') as f:
        data = f.read()
    
    hits = []
    for i, b in enumerate(data):
        if b > 127:
            line = data[:i].count(b'\n') + 1
            col = i - data[:i].rfind(b'\n') - 1
            # Get context: the full line containing this byte
            line_start = data[:i].rfind(b'\n') + 1
            line_end = data.find(b'\n', i)
            if line_end == -1:
                line_end = len(data)
            context = data[line_start:line_end].decode('iso-8859-1')
            char = bytes([b]).decode('iso-8859-1')
            hits.append({
                'line': line,
                'col': col,
                'byte': b,
                'hex': f'0x{b:02x}',
                'char': char,
                'context': context.strip(),
            })
    return hits

def find_source_files(acl2_dir):
    """Find all Lisp source files under acl2_dir."""
    files = set()
    for ext in ('*.lisp', '*.lsp', '*.acl2', '*.cl'):
        files.update(glob.glob(os.path.join(acl2_dir, '**', ext), recursive=True))
    return sorted(files)

def main():
    acl2_dir = sys.argv[1] if len(sys.argv) > 1 else '/workspaces/pup/external/acl2'
    
    if not os.path.isdir(acl2_dir):
        print(f"Error: {acl2_dir} is not a directory", file=sys.stderr)
        sys.exit(1)
    
    files = find_source_files(acl2_dir)
    print(f"Scanning {len(files)} files under {acl2_dir}...", file=sys.stderr)
    
    results = {}
    total_hits = 0
    
    for fpath in files:
        try:
            hits = scan_file(fpath)
        except Exception as e:
            print(f"  Error reading {fpath}: {e}", file=sys.stderr)
            continue
        
        if hits:
            relpath = os.path.relpath(fpath, acl2_dir)
            results[relpath] = hits
            total_hits += len(hits)
    
    # Print summary to stderr
    print(f"\nFound {total_hits} non-ASCII byte(s) in {len(results)} file(s):\n", file=sys.stderr)
    for relpath, hits in sorted(results.items()):
        print(f"  {relpath}: {len(hits)} non-ASCII byte(s)", file=sys.stderr)
        # Group by unique byte values
        byte_counts = defaultdict(int)
        for h in hits:
            byte_counts[(h['hex'], h['char'])] += 1
        for (hx, ch), count in sorted(byte_counts.items()):
            print(f"    {hx} '{ch}' x{count}", file=sys.stderr)
    
    # Print full JSON report to stdout
    report = {
        'acl2_dir': acl2_dir,
        'files_scanned': len(files),
        'files_with_non_ascii': len(results),
        'total_non_ascii_bytes': total_hits,
        'results': results,
    }
    json.dump(report, sys.stdout, indent=2, ensure_ascii=True)
    print()

if __name__ == '__main__':
    main()
