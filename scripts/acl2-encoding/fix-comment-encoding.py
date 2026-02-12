#!/usr/bin/env python3
"""Convert ISO-8859-1 non-ASCII bytes in ACL2 comment lines to UTF-8.

Strategy:
  1. Skip files that are already valid UTF-8 (they need no conversion).
  2. For remaining files (assumed ISO-8859-1), find non-ASCII bytes in
     ';' line comments and replace each byte with its UTF-8 equivalent.
     ISO-8859-1 byte values 0x80-0xFF map directly to Unicode code points
     U+0080-U+00FF, so the conversion is just chr(byte).encode('utf-8').
  3. Non-ASCII bytes in code (strings, symbols) are left untouched and
     reported as warnings since they may have semantic meaning.

Excluded by default:
  - books/quicklisp/  (third-party code, already valid UTF-8)
  - books/projects/python/  (test data)

Usage:
    python3 fix-comment-encoding.py [--dry-run] [--report REPORT.json] [ACL2_DIR]

    --dry-run       Show what would be changed without modifying files
    --report FILE   Read scan report from FILE to select files to process
    ACL2_DIR        defaults to /workspaces/pup/external/acl2
"""

import os
import sys
import argparse
import json
import glob


# Directories to exclude (relative to ACL2_DIR, with trailing /)
EXCLUDE_DIRS = [
    'books/quicklisp/',
]


def is_valid_utf8(data):
    """Check if raw bytes are valid UTF-8."""
    try:
        data.decode('utf-8')
        return True
    except UnicodeDecodeError:
        return False


def process_file(fpath, dry_run=False):
    """Process a single file, converting ISO-8859-1 non-ASCII in comments to UTF-8.

    Returns dict with changes made, warnings, skip reason (if any).
    """
    with open(fpath, 'rb') as f:
        data = f.read()

    # Check if there are any non-ASCII bytes at all
    has_non_ascii = any(b > 127 for b in data)
    if not has_non_ascii:
        return {'changes': [], 'warnings': [], 'modified': False,
                'skipped': 'no non-ASCII bytes'}

    # If file is already valid UTF-8, skip it — the non-ASCII bytes are
    # already properly encoded multi-byte sequences (e.g., curly quotes,
    # CJK characters, math symbols).
    if is_valid_utf8(data):
        return {'changes': [], 'warnings': [], 'modified': False,
                'skipped': 'already valid UTF-8'}

    # Process as ISO-8859-1: walk each line, track comment vs code context
    lines = data.split(b'\n')
    changes = []
    warnings = []
    new_lines = []
    in_block_comment = False  # Track #|...|# block comments across lines

    for line_no, line in enumerate(lines, 1):
        new_line = bytearray()
        in_line_comment = False
        in_string = False
        escape_next = False
        prev_byte = 0

        for col, b in enumerate(line):
            if b <= 127:
                if escape_next:
                    escape_next = False
                elif in_block_comment:
                    # Check for |# to end block comment
                    if prev_byte == ord('|') and b == ord('#'):
                        in_block_comment = False
                elif in_line_comment:
                    pass  # rest of line is comment
                elif b == ord('\\') and in_string:
                    escape_next = True
                elif b == ord('"') and not in_line_comment:
                    in_string = not in_string
                elif b == ord(';') and not in_string:
                    in_line_comment = True
                elif prev_byte == ord('#') and b == ord('|') and not in_string:
                    in_block_comment = True
                new_line.append(b)
                prev_byte = b
            else:
                # Non-ASCII byte (0x80-0xFF)
                iso_char = chr(b)  # ISO-8859-1 -> Unicode (same code point)
                if in_line_comment or in_block_comment:
                    # Convert to UTF-8 in the output
                    utf8_bytes = iso_char.encode('utf-8')
                    new_line.extend(utf8_bytes)
                    changes.append({
                        'line': line_no,
                        'col': col + 1,
                        'byte': f'0x{b:02x}',
                        'char': iso_char,
                    })
                else:
                    # Non-ASCII in code — leave as-is, warn
                    new_line.append(b)
                    context_str = line.decode('iso-8859-1').strip()
                    if len(context_str) > 80:
                        context_str = context_str[:80] + '...'
                    warnings.append({
                        'line': line_no,
                        'col': col + 1,
                        'byte': f'0x{b:02x}',
                        'char': iso_char,
                        'context': context_str,
                        'reason': 'non-ASCII in code (not in comment)',
                    })
                prev_byte = b

        new_lines.append(bytes(new_line))

    modified = len(changes) > 0

    if modified and not dry_run:
        new_data = b'\n'.join(new_lines)
        with open(fpath, 'wb') as f:
            f.write(new_data)

    return {'changes': changes, 'warnings': warnings, 'modified': modified,
            'skipped': None}


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('acl2_dir', nargs='?',
                        default='/workspaces/pup/external/acl2',
                        help='ACL2 source directory')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show changes without modifying files')
    parser.add_argument('--report', type=str,
                        help='Use scan report JSON to select files to process')
    parser.add_argument('--include-excluded', action='store_true',
                        help='Process files in normally-excluded directories')
    args = parser.parse_args()

    acl2_dir = os.path.abspath(args.acl2_dir)

    # Build file list
    if args.report:
        with open(args.report) as f:
            report = json.load(f)
        file_list = [os.path.join(acl2_dir, relpath)
                     for relpath in report['results'].keys()]
    else:
        file_set = set()
        for ext in ('*.lisp', '*.lsp', '*.acl2', '*.cl'):
            file_set.update(
                glob.glob(os.path.join(acl2_dir, '**', ext), recursive=True))
        file_list = sorted(file_set)

    # Apply exclusions
    if not args.include_excluded:
        excluded_count = 0
        filtered = []
        for fpath in file_list:
            relpath = os.path.relpath(fpath, acl2_dir)
            if any(relpath.startswith(ex) for ex in EXCLUDE_DIRS):
                excluded_count += 1
            else:
                filtered.append(fpath)
        file_list = filtered
        if excluded_count:
            print(f"Excluded {excluded_count} file(s) in: "
                  f"{', '.join(EXCLUDE_DIRS)}", file=sys.stderr)

    print(f"Processing {len(file_list)} file(s)...", file=sys.stderr)
    if args.dry_run:
        print("DRY RUN — no files will be modified\n", file=sys.stderr)

    all_changes = {}
    all_warnings = {}
    skipped_utf8 = 0
    skipped_clean = 0

    for fpath in sorted(file_list):
        relpath = os.path.relpath(fpath, acl2_dir)
        result = process_file(fpath, dry_run=args.dry_run)

        if result.get('skipped') == 'already valid UTF-8':
            skipped_utf8 += 1
            continue
        if result.get('skipped') == 'no non-ASCII bytes':
            skipped_clean += 1
            continue

        if result['changes']:
            all_changes[relpath] = result['changes']
            action = 'Would change' if args.dry_run else 'Changed'
            print(f"  {action} {relpath}: {len(result['changes'])} byte(s)",
                  file=sys.stderr)
            for c in result['changes']:
                print(f"    Line {c['line']}:{c['col']}: "
                      f"{c['byte']} '{c['char']}' -> UTF-8",
                      file=sys.stderr)

        if result['warnings']:
            all_warnings[relpath] = result['warnings']
            for w in result['warnings']:
                print(f"  WARNING {relpath}:{w['line']}:{w['col']}: "
                      f"{w['reason']} ({w['byte']} '{w['char']}')",
                      file=sys.stderr)

    # Summary
    total_changes = sum(len(v) for v in all_changes.values())
    total_warnings = sum(len(v) for v in all_warnings.values())
    print(f"\nSummary:", file=sys.stderr)
    print(f"  Files processed: {len(file_list)}", file=sys.stderr)
    print(f"  Skipped (already UTF-8): {skipped_utf8}", file=sys.stderr)
    print(f"  Skipped (all ASCII): {skipped_clean}", file=sys.stderr)
    print(f"  Files {'to change' if args.dry_run else 'changed'}: "
          f"{len(all_changes)}", file=sys.stderr)
    print(f"  Total bytes converted: {total_changes}", file=sys.stderr)
    print(f"  Warnings (non-ASCII in code): {total_warnings}", file=sys.stderr)

    report_out = {
        'dry_run': args.dry_run,
        'files_processed': len(file_list),
        'skipped_utf8': skipped_utf8,
        'skipped_clean': skipped_clean,
        'files_changed': len(all_changes),
        'total_replacements': total_changes,
        'total_warnings': total_warnings,
        'changes': all_changes,
        'warnings': all_warnings,
    }
    json.dump(report_out, sys.stdout, indent=2, ensure_ascii=False)
    print()


if __name__ == '__main__':
    main()
