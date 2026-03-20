#!/bin/bash
# Post-create setup script for the ACL2 Verified Agent devcontainer

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_WORKSPACE_FOLDER="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKSPACE_FOLDER="${1:-${DEFAULT_WORKSPACE_FOLDER}}"

echo "=== Setting up ACL2 Verified Agent devcontainer ==="

# --- Ensure required submodules are present ---
echo ""
echo "Checking required submodules..."
if [ ! -d "${WORKSPACE_FOLDER}/external/acl2-mcp" ] || [ ! -f "${WORKSPACE_FOLDER}/external/parinfer-rust/Cargo.toml" ]; then
    echo "Initializing required submodules..."
    git -C "${WORKSPACE_FOLDER}" submodule update --init --recursive external/acl2-mcp external/parinfer-rust
    echo "✓ Required submodules initialized"
else
    echo "✓ Required submodules already available"
fi

# --- Python virtual environment ---
echo ""
echo "Setting up Python virtual environment..."
if [ ! -d "${WORKSPACE_FOLDER}/.venv" ]; then
    python3 -m venv "${WORKSPACE_FOLDER}/.venv"
    "${WORKSPACE_FOLDER}/.venv/bin/pip" install --upgrade pip
    "${WORKSPACE_FOLDER}/.venv/bin/pip" install -e "${WORKSPACE_FOLDER}/acl2-mcp"
    "${WORKSPACE_FOLDER}/.venv/bin/pip" install 'jupyter-mcp-server>=0.15.0'
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
    cargo install --path "${WORKSPACE_FOLDER}/external/parinfer-rust"
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
