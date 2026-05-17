#!/usr/bin/env bash
# setup_hera.sh — one-time install for Hera local agent
# Run once: bash setup_hera.sh
# After that, just type: hera [directory]

set -e

HERA_DIR="$HOME/.hera"
SCRIPT_NAME="llm_code_agent.py"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}  ➜  $1${NC}"; }
success() { echo -e "${GREEN}  ✓  $1${NC}"; }
warn()    { echo -e "${YELLOW}  ⚠  $1${NC}"; }
error()   { echo -e "${RED}  ✗  $1${NC}"; exit 1; }

echo ""
echo -e "${CYAN}  Hera Setup${NC}"
echo "  ─────────────────────────────────────"
echo ""

# ── 1. Find the script ──────────────────────────────────────────────────────
if [ -f "./$SCRIPT_NAME" ]; then
    SCRIPT_SRC="$(pwd)/$SCRIPT_NAME"
elif [ -f "$HOME/$SCRIPT_NAME" ]; then
    SCRIPT_SRC="$HOME/$SCRIPT_NAME"
else
    error "Cannot find $SCRIPT_NAME. Run this script from the same directory."
fi
info "Found script: $SCRIPT_SRC"

# ── 2. Create Hera home directory ───────────────────────────────────────────
mkdir -p "$HERA_DIR"
cp "$SCRIPT_SRC" "$HERA_DIR/llm_code_agent.py"
chmod +x "$HERA_DIR/llm_code_agent.py"
success "Installed to $HERA_DIR/"

# ── 3. Check Homebrew ────────────────────────────────────────────────────────
if ! command -v brew &>/dev/null; then
    warn "Homebrew not found. Installing..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi
success "Homebrew ready"

# ── 4. Check Python 3.10+ ────────────────────────────────────────────────────
PYTHON=""
for py in python3.12 python3.11 python3.10 python3; do
    if command -v "$py" &>/dev/null; then
        ver=$("$py" -c 'import sys; print(sys.version_info >= (3,10))')
        if [ "$ver" = "True" ]; then
            PYTHON="$py"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    info "Installing Python 3.12 via Homebrew..."
    brew install python@3.12
    PYTHON="python3.12"
fi
success "Python: $($PYTHON --version)"

# ── 5. Create dedicated virtualenv for Hera ──────────────────────────────────
info "Creating Python virtualenv at $HERA_DIR/venv..."
"$PYTHON" -m venv "$HERA_DIR/venv"
HERA_PIP="$HERA_DIR/venv/bin/pip"
HERA_PYTHON="$HERA_DIR/venv/bin/python"

info "Installing Python dependencies..."
"$HERA_PIP" install --quiet --upgrade pip
"$HERA_PIP" install --quiet requests rich chromadb
success "Python dependencies installed"

# ── 6. Check / install Ollama ────────────────────────────────────────────────
if ! command -v ollama &>/dev/null; then
    info "Installing Ollama..."
    brew install ollama
fi
success "Ollama ready: $(ollama --version 2>/dev/null | head -1)"

# ── 7. Pull default model if not present ─────────────────────────────────────
DEFAULT_MODEL="gemma3:12b"
if ! ollama list 2>/dev/null | grep -q "gemma3:12b"; then
    info "Pulling $DEFAULT_MODEL (this takes a few minutes on first run)..."
    ollama pull "$DEFAULT_MODEL"
    success "Model $DEFAULT_MODEL ready"
else
    success "Model $DEFAULT_MODEL already downloaded"
fi

# Pull embedding model for semantic memory
if ! ollama list 2>/dev/null | grep -q "nomic-embed-text"; then
    info "Pulling nomic-embed-text (for semantic memory)..."
    ollama pull nomic-embed-text
    success "Embedding model ready"
else
    success "Embedding model already downloaded"
fi

# ── 8. Write the shell function to zshrc ─────────────────────────────────────
ZSHRC="$HOME/.zshrc"
MARKER="# >>> hera >>>"
MARKER_END="# <<< hera <<<"

if grep -q "$MARKER" "$ZSHRC" 2>/dev/null; then
    warn "Hera shell function already exists in $ZSHRC — skipping. Re-run with --force to overwrite."
else
    cat >> "$ZSHRC" << 'SHELL_FUNC'

# >>> hera >>>
hera() {
    local TARGET_DIR="${1:-.}"

    # Resolve to absolute path
    if [ ! -d "$TARGET_DIR" ]; then
        echo "  ✗  Directory not found: $TARGET_DIR"
        return 1
    fi
    TARGET_DIR="$(cd "$TARGET_DIR" && pwd)"

    local HERA_DIR="$HOME/.hera"
    local HERA_PYTHON="$HERA_DIR/venv/bin/python"
    local HERA_SCRIPT="$HERA_DIR/llm_code_agent.py"

    if [ ! -f "$HERA_PYTHON" ]; then
        echo "  ✗  Hera not installed. Run: bash setup_hera.sh"
        return 1
    fi

    # Start Ollama in background if not already running
    if ! pgrep -x "ollama" > /dev/null 2>&1; then
        echo "  ➜  Starting Ollama..."
        ollama serve > /dev/null 2>&1 &
        sleep 2
    fi

    echo "  ➜  Hera → $TARGET_DIR"
    echo ""

    "$HERA_PYTHON" "$HERA_SCRIPT" \
        --workdir "$TARGET_DIR" \
        --model "gemma3:12b" \
        "${@:2}"
}

# Tab completion: complete directory names for first argument
_hera_complete() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    COMPREPLY=($(compgen -d -- "$cur"))
}
# zsh-compatible completion
if [[ -n "$ZSH_VERSION" ]]; then
    compdef '_files -/' hera
fi
# <<< hera <<<
SHELL_FUNC

    success "Shell function added to $ZSHRC"
fi

# ── 9. Done ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}  ─────────────────────────────────────${NC}"
echo -e "${GREEN}  Setup complete!${NC}"
echo ""
echo "  Reload your shell:"
echo -e "    ${CYAN}source ~/.zshrc${NC}"
echo ""
echo "  Then use Hera from anywhere:"
echo -e "    ${CYAN}hera${NC}                  # current directory"
echo -e "    ${CYAN}hera ~/projects/myapp${NC}  # specific directory"
echo -e "    ${CYAN}hera . --approval${NC}      # with approval mode"
echo -e "    ${CYAN}hera . --model qwen2.5:7b${NC}  # different model"
echo ""