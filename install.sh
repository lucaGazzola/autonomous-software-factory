#!/bin/sh
#
# Install the `factory` CLI from the public GitHub remote.
#
#   curl -fsSL https://raw.githubusercontent.com/lucaGazzola/autonomous-software-factory/main/install.sh | bash
#
# Prefers pipx, falls back to `pip install --user`. Never requires root.
# Re-running the script upgrades the existing install (pipx --force / pip --upgrade).
set -eu

REPO_URL="git+https://github.com/lucaGazzola/autonomous-software-factory.git"
MIN_PYTHON="3.11"

log() { printf '%s\n' "$*"; }
warn() { printf '%s\n' "$*" >&2; }

die() {
    warn "error: $*"
    exit 1
}

# Print the name of the first interpreter that is Python >= 3.11.
find_python() {
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
                printf '%s\n' "$candidate"
                return 0
            fi
        fi
    done
    return 1
}

PYTHON="$(find_python)" || die "Python $MIN_PYTHON or newer is required but none was found on PATH. Install Python $MIN_PYTHON+ (https://www.python.org/downloads/ or your system package manager) and re-run this installer."

log "Using $PYTHON."

if command -v pipx >/dev/null 2>&1; then
    log "Installing factory with pipx..."
    pipx install --force "$REPO_URL"
else
    warn "pipx not found; falling back to 'pip install --user'."
    user_base="$("$PYTHON" -m site --user-base 2>/dev/null || printf '%s/.local' "${HOME:-}")"
    user_bin="$user_base/bin"
    case ":$PATH:" in
        *":$user_bin:"*)
            ;;
        *)
            warn "warning: $user_bin is not on your PATH, so the 'factory' command will not be found."
            warn "Add it to your PATH (e.g. 'export PATH=\"\$PATH:$user_bin\"' in ~/.profile) and open a new shell."
            ;;
    esac
    "$PYTHON" -m pip install --user --upgrade "$REPO_URL"
fi

log ""
log "Done. The 'factory' CLI is installed. Next steps:"
log "  factory init"
log "  factory start"
