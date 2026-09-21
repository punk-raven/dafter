#!/usr/bin/env bash
set -euo pipefail

# Runs two ways:
#   ./scripts/setup.sh                                   inside a checkout
#   curl -fsSL <raw url of this file> | bash [-s -- -y]  before there is one
# Piped, there is no file to locate the checkout from, so it clones into
# $DAFTER_DIR (default ./dafter), at $DAFTER_REF if set, after the
# prerequisites are in place.
repo_url=https://github.com/punk-raven/dafter.git
checkout=
if [ -f "${BASH_SOURCE[0]:-}" ] && [ -f "$(dirname "${BASH_SOURCE[0]}")/../Makefile" ]; then
  checkout=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
fi

assume_yes=0
for arg in "$@"; do
  case "$arg" in
    -y | --yes) assume_yes=1 ;;
    *)
      printf 'setup: unknown option: %s\nusage: scripts/setup.sh [-y|--yes]\n' "$arg" >&2
      exit 2
      ;;
  esac
done

have() { command -v "$1" >/dev/null 2>&1; }
die() { printf 'setup: %s\n' "$*" >&2; exit 1; }

# Asks on the terminal, not stdin: when piped from curl, stdin is this script.
confirm() {
  if [ "$assume_yes" -eq 1 ]; then
    return 0
  fi
  if ! { : </dev/tty; } 2>/dev/null; then
    return 1
  fi
  printf '%s not installed. Install it with:\n\n    %s\n\nRun that now? [y/N] ' "$1" "$2" >/dev/tty
  read -r reply </dev/tty
  case "$reply" in
    [yY] | [yY][eE][sS]) return 0 ;;
    *) return 1 ;;
  esac
}

# Install command for this machine, or nothing when there is no package manager
# it knows. Arguments: what the tool is called in apt, dnf and pacman.
package_installer() {
  local apt=$1 dnf=$2 pacman=$3
  if [ "$(uname -s)" = Darwin ]; then
    echo 'xcode-select --install'
  elif have apt-get; then
    echo "sudo apt-get update && sudo apt-get install -y $apt"
  elif have dnf; then
    echo "sudo dnf install -y $dnf"
  elif have pacman; then
    echo "sudo pacman -S --needed --noconfirm $pacman"
  fi
}

installer_for_git() { package_installer git git git; }
installer_for_make() { package_installer build-essential make make; }
installer_for_uv() { echo 'curl -LsSf https://astral.sh/uv/install.sh | sh'; }

# `go test -race` links through cgo, which needs a C compiler. Go's default is
# gcc on Linux and clang on macOS; either one, or whatever $CC names, will do.
installer_for_cc() { package_installer build-essential gcc gcc; }

installer_for_go() {
  if have brew; then
    echo 'brew install go'
  else
    package_installer golang-go golang go
  fi
}

# A tool is present when its own probe says so, or, failing one, when it is on PATH.
present_cc() { have "${CC:-}" || have gcc || have clang || have cc; }
present() {
  if declare -F "present_$1" >/dev/null; then "present_$1"; else have "$1"; fi
}

ensure() {
  local tool=$1 docs=$2 installer
  present "$tool" && return 0

  installer=$("installer_for_$tool")
  [ -n "$installer" ] || die "$tool is missing and there is no known way to install it here. See $docs"

  confirm "$tool is" "$installer" || die "$tool is required. Install it and run this again: $docs"
  eval "$installer"

  case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) [ -d "$HOME/.local/bin" ] && PATH="$HOME/.local/bin:$PATH" ;;
  esac
  hash -r

  present "$tool" || die "$tool installed but is not on PATH. Open a new shell and run this again"
}

ensure git https://git-scm.com/downloads
ensure make https://www.gnu.org/software/make/
ensure cc https://go.dev/doc/install/source#environment
ensure go https://go.dev/dl/
ensure uv https://docs.astral.sh/uv/getting-started/installation/

if [ -n "$checkout" ]; then
  cd "$checkout"
else
  dir=${DAFTER_DIR:-dafter}
  if [ -d "$dir/.git" ]; then
    echo "==> using the existing clone in $dir"
  else
    clone=(git clone --quiet)
    [ -n "${DAFTER_REF:-}" ] && clone+=(--branch "$DAFTER_REF")
    echo "==> cloning $repo_url${DAFTER_REF:+ at $DAFTER_REF} into $dir"
    "${clone[@]}" "$repo_url" "$dir"
  fi
  cd "$dir"
fi

go_version=$(go env GOVERSION | sed 's/^go//')
if [ "$(printf '1.21\n%s\n' "$go_version" | sort -V | head -1)" != "1.21" ]; then
  die "go $go_version is too old. go/go.mod pins a newer toolchain and needs 1.21+ to fetch it: https://go.dev/dl/"
fi

printf '==> go %s, uv %s\n' "$go_version" "$(uv --version | awk '{print $2}')"

echo '==> generating everything derived from schemas/'
make generate

echo '==> resolving the Python environment from python/uv.lock'
(cd python && uv sync --frozen)

echo '==> building the pinned linter and vulnerability scanner'
make check-tools

cat <<DONE

Ready, in $(pwd).

  make check   the checks CI runs
  make help    every target
DONE
