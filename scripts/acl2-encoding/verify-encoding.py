#!/usr/bin/env python3
"""Verify that ACL2 source files are valid UTF-8 after encoding fixes.

Checks that all .lisp, .lsp, .acl2, and .cl files can be read as UTF-8.
Reports any remaining non-UTF-8 bytes (which should be in code, not comments).

Usage:
    python3 verify-encoding.py [ACL2_DIR]
"""

import os
import sys
import glob


def verify_file(fpath):
    """Check if a file is valid UTF-8. Return list of problem locations."""
    with open(fpath, 'rb') as f:
        data = f.read()
    
    problems = []
    try:
        data.decode('utf-8')
    except UnicodeDecodeError:
        # Find specific problem locations
        for i, b in enumerate(data):
            if b > 127:
                # Try to decode this byte and its neighbors as UTF-8
                # Check if it's a valid UTF-8 start or continuation byte
                start = i
                while start > 0 and (data[start] & 0xC0) == 0x80:
                    start -= 1
                try:
                    end = start + 1
                    while end < len(data) and (data[end] & 0xC0) == 0x80:
                        end += 1
                    data[start:end].decode('utf-8')
                except (UnicodeDecodeError, IndexError):
                    if i == start:  # Only report the start byte
                        line = data[:i].count(b'\n') + 1
                        col = i - data[:i].rfind(b'\n') - 1
                        line_start = data[:i].rfind(b'\n') + 1
                        line_end = data.find(b'\n', i)
                        if line_end == -1:
                            line_end = len(data)
                        context = data[line_start:line_end].decode('iso-8859-1').strip()
                        problems.append({
                            'line': line,
                            'col': col,
                            'byte': f'0x{b:02x}',
                            'char_iso': bytes([b]).decode('iso-8859-1'),
                            'context': context,
                        })
    
    return problems


def main():
    acl2_dir = sys.argv[1] if len(sys.argv) > 1 else '/workspaces/pup/external/acl2'
    
    files = set()
    for ext in ('*.lisp', '*.lsp', '*.acl2', '*.cl'):
        files.update(glob.glob(os.path.join(acl2_dir, '**', ext), recursive=True))
    files = sorted(files)
    
    print(f"Verifying {len(files)} files for UTF-8 validity...", file=sys.stderr)
    
    ok_count = 0
    problem_files = {}
    
    for fpath in files:
        problems = verify_file(fpath)
        if problems:
            relpath = os.path.relpath(fpath, acl2_dir)
            problem_files[relpath] = problems
        else:
            ok_count += 1
    
    total_problems = sum(len(v) for v in problem_files.values())
    
    print(f"\nResults:", file=sys.stderr)
    print(f"  {ok_count} files are valid UTF-8", file=sys.stderr)
    print(f"  {len(problem_files)} files have non-UTF-8 bytes "
          f"({total_problems} total)", file=sys.stderr)
    
    if problem_files:
        print(f"\nFiles with remaining non-UTF-8 bytes:", file=sys.stderr)
        for relpath, problems in sorted(problem_files.items()):
            print(f"  {relpath}:", file=sys.stderr)
            for p in problems:
                print(f"    Line {p['line']}, col {p['col']}: "
                      f"{p['byte']} '{p['char_iso']}' — {p['context'][:80]}",
                      file=sys.stderr)
    
    # Exit code: 0 if all clean, 1 if problems remain
    sys.exit(0 if not problem_files else 1)


if __name__ == '__main__':
    main()
