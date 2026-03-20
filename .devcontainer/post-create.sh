#!/bin/bash
# Post-create setup script for the ACL2 Verified Agent devcontainer

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_WORKSPACE_FOLDER="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKSPACE_FOLDER="${1:-.}"

echo "=== Setting up ACL2 Verified Agent devcontainer ==="

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

# --- Python virtual environment ---
echo ""
echo "Setting up Python virtual environment..."
if [ ! -d "${WORKSPACE_FOLDER}/.venv" ]; then
    python3 -m venv "${WORKSPACE_FOLDER}/.venv"
    "${WORKSPACE_FOLDER}/.venv/bin/pip" install --upgrade pip
    "${WORKSPACE_FOLDER}/.venv/bin/pip" install -e "${WORKSPACE_FOLDER}/external/acl2-mcp"
    "${WORKSPACE_FOLDER}/.venv/bin/pip" install -e "${WORKSPACE_FOLDER}/external/acl2-kg-mcp"
    "${WORKSPACE_FOLDER}/.venv/bin/pip" install 'jupyter-mcp-server>=0.15.0' z3-solver
    echo "✓ Python venv created and acl2-mcp installed"
else
    echo "✓ Python venv already exists"
fi

# --- ACL2 Jupyter Kernel ---
echo ""
echo "Re-installing ACL2 Jupyter kernelspec (aligning Quicklisp packages with ACL2)..."
# The Dockerfile installs these as root; fix ownership so the installer can update them
LOCAL_PROJECTS_KERNEL="${HOME}/quicklisp/local-projects/acl2-jupyter-kernel"
if [ -d "${LOCAL_PROJECTS_KERNEL}" ]; then
    sudo chown -R "$(id -u):$(id -g)" "${LOCAL_PROJECTS_KERNEL}"
fi
KERNEL_INSTALL="${WORKSPACE_FOLDER}/external/acl2-jupyter-kernel/install-kernelspec.sh"
if [ -x "${KERNEL_INSTALL}" ]; then
    "${KERNEL_INSTALL}"
    echo "✓ ACL2 Jupyter kernelspec re-installed"
else
    echo "⚠ install-kernelspec.sh not found or not executable at ${KERNEL_INSTALL}"
fi

echo ""
echo "=== Setup complete! ==="
echo "  - Python venv: ${WORKSPACE_FOLDER}/.venv"
echo "  - Rust: ~/.cargo"
echo "  - parinfer-rust: $(command -v parinfer-rust || echo 'will be available in new shell')"
