#!/bin/bash
# Post-create setup script for the ACL2 Verified Agent devcontainer

set -e

WORKSPACE_FOLDER="${1:-/workspaces/verified-agent}"

echo "=== Setting up ACL2 Verified Agent devcontainer ==="

# --- Python virtual environment ---
echo ""
echo "Setting up Python virtual environment..."
if [ ! -d "${WORKSPACE_FOLDER}/.venv" ]; then
    python3 -m venv "${WORKSPACE_FOLDER}/.venv"
    "${WORKSPACE_FOLDER}/.venv/bin/pip" install --upgrade pip
    "${WORKSPACE_FOLDER}/.venv/bin/pip" install -e "${WORKSPACE_FOLDER}/acl2-mcp"
    echo "✓ Python venv created and acl2-mcp installed"
else
    echo "✓ Python venv already exists"
fi

# --- Rust toolchain ---
echo ""
echo "Setting up Rust toolchain..."
if [ ! -f "$HOME/.cargo/env" ]; then
    echo "Installing Rust..."
    curl https://sh.rustup.rs -sSf | sh -s -- -y
    echo "✓ Rust installed"
else
    echo "✓ Rust already installed"
fi

# Source cargo environment for this script
. "$HOME/.cargo/env"

# --- parinfer-rust ---
echo ""
echo "Setting up parinfer-rust..."
if command -v parinfer-rust >/dev/null 2>&1; then
    echo "✓ parinfer-rust already installed"
else
    echo "Installing parinfer-rust from GitHub..."
    cargo install --git https://github.com/eraserhd/parinfer-rust
    echo "✓ parinfer-rust installed"
fi

# --- Add cargo to bashrc if not present ---
if ! grep -q 'source.*\.cargo/env' ~/.bashrc 2>/dev/null; then
    echo '' >> ~/.bashrc
    echo '# Rust/Cargo environment' >> ~/.bashrc
    echo '[ -f "$HOME/.cargo/env" ] && source "$HOME/.cargo/env"' >> ~/.bashrc
    echo "✓ Added cargo to ~/.bashrc"
fi

echo ""
echo "=== Setup complete! ==="
echo "  - Python venv: ${WORKSPACE_FOLDER}/.venv"
echo "  - Rust: ~/.cargo"
echo "  - parinfer-rust: $(command -v parinfer-rust || echo 'will be available in new shell')"
