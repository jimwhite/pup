#!/usr/bin/env bash
# ACL2 Encoding Migration Scripts
#
# These scripts help migrate ACL2 source files from ISO-8859-1 to UTF-8
# by replacing non-ASCII bytes in comments with their UTF-8 equivalents.
# Non-ASCII bytes in code are left untouched and reported as warnings.
#
# Workflow:
#   1. Scan:    python3 scan-non-ascii.py [ACL2_DIR] > report.json
#   2. Review:  inspect report.json for non-ASCII locations
#   3. Fix:     python3 fix-comment-encoding.py --dry-run [ACL2_DIR]
#   4. Apply:   python3 fix-comment-encoding.py [ACL2_DIR] > changes.json
#   5. Verify:  python3 verify-encoding.py [ACL2_DIR]
#
# The scripts should be run from the pup repo root on a fresh branch
# off of the ACL2 master branch.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ACL2_DIR="${1:-/workspaces/pup/external/acl2}"

echo "=== Step 1: Scan for non-ASCII bytes ==="
python3 "$SCRIPT_DIR/scan-non-ascii.py" "$ACL2_DIR" > "$SCRIPT_DIR/scan-report.json"

echo ""
echo "=== Step 2: Dry run of comment encoding fix ==="
python3 "$SCRIPT_DIR/fix-comment-encoding.py" --dry-run "$ACL2_DIR" > "$SCRIPT_DIR/dry-run-report.json"

echo ""
echo "Review the reports before applying changes:"
echo "  $SCRIPT_DIR/scan-report.json"
echo "  $SCRIPT_DIR/dry-run-report.json"
echo ""
echo "To apply changes:"
echo "  python3 $SCRIPT_DIR/fix-comment-encoding.py $ACL2_DIR > $SCRIPT_DIR/changes.json"
echo ""
echo "To verify after applying:"
echo "  python3 $SCRIPT_DIR/verify-encoding.py $ACL2_DIR"
