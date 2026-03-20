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
echo "Aligning Quicklisp packages with ACL2 build..."

# Remove any packages from jovyan's Quicklisp dist that also exist in ACL2's
# bundled Quicklisp.  The kernel loads into the ACL2 image which already has
# its bundle versions; having a second (newer) copy in jovyan's dist causes
# defconstant-uneql errors when ACL2 books try to reload them.
ACL2_BUNDLE_SW="${ACL2_HOME:-/home/acl2}/books/quicklisp/bundle/software"
JOVYAN_DIST_SW="${HOME}/quicklisp/dists/quicklisp/software"
if [ -d "${ACL2_BUNDLE_SW}" ] && [ -d "${JOVYAN_DIST_SW}" ]; then
    for acl2_pkg in "${ACL2_BUNDLE_SW}"/*/; do
        base=$(basename "$acl2_pkg" | sed 's/-[0-9v].*//')
        for jovyan_pkg in "${JOVYAN_DIST_SW}/${base}"-*/; do
            if [ -d "$jovyan_pkg" ]; then
                echo "  removing duplicate: $(basename "$jovyan_pkg")"
                rm -rf "$jovyan_pkg"
            fi
        done
    done
    echo "✓ Duplicate Quicklisp packages removed"
fi

# Tell ASDF where to find ACL2's bundle packages (now the only copies).
ASDF_CONF_DIR="${HOME}/.config/common-lisp/source-registry.conf.d"
if [ -d "${ACL2_BUNDLE_SW}" ] && [ ! -f "${ASDF_CONF_DIR}/01-acl2-bundle.conf" ]; then
    mkdir -p "${ASDF_CONF_DIR}"
    echo "(:tree \"${ACL2_BUNDLE_SW}/\")" > "${ASDF_CONF_DIR}/01-acl2-bundle.conf"
    echo "✓ ASDF source registry configured for ACL2 bundle"
fi

# Clear stale ASDF fasl cache so recompilation picks up the right sources.
rm -rf "${HOME}/.cache/common-lisp/"
echo "✓ ASDF fasl cache cleared"

# Re-install the kernelspec with the latest installer code
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
