#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

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

confirm() {
  if [ "$assume_yes" -eq 1 ]; then
    return 0
  fi
  if [ ! -t 0 ]; then
    return 1
  fi
  printf '%s not installed. Install it with:\n\n    %s\n\nRun that now? [y/N] ' "$1" "$2"
  read -r reply
  case "$reply" in
    [yY] | [yY][eE][sS]) return 0 ;;
    *) return 1 ;;
  esac
}

installer_for_make() {
  if [ "$(uname -s)" = Darwin ]; then
    echo 'xcode-select --install'
  elif have apt-get; then
    echo 'sudo apt-get update && sudo apt-get install -y build-essential'
  elif have dnf; then
    echo 'sudo dnf install -y make'
  elif have pacman; then
    echo 'sudo pacman -S --needed --noconfirm make'
  fi
}

installer_for_go() {
  if have brew; then
    echo 'brew install go'
  elif have apt-get; then
    echo 'sudo apt-get update && sudo apt-get install -y golang-go'
  elif have dnf; then
    echo 'sudo dnf install -y golang'
  elif have pacman; then
    echo 'sudo pacman -S --needed --noconfirm go'
  fi
}

installer_for_uv() {
  echo 'curl -LsSf https://astral.sh/uv/install.sh | sh'
}

ensure() {
  local tool=$1 docs=$2 installer
  have "$tool" && return 0

  installer=$("installer_for_$tool")
  [ -n "$installer" ] || die "$tool is missing and there is no known way to install it here. See $docs"

  confirm "$tool is" "$installer" || die "$tool is required. Install it and run this again: $docs"
  eval "$installer"

  case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) [ -d "$HOME/.local/bin" ] && PATH="$HOME/.local/bin:$PATH" ;;
  esac
  hash -r

  have "$tool" || die "$tool installed but is not on PATH. Open a new shell and run this again"
}

ensure make https://www.gnu.org/software/make/
ensure go https://go.dev/dl/
ensure uv https://docs.astral.sh/uv/getting-started/installation/

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
make tools

cat <<'DONE'

Ready.

  make check   the checks CI runs
  make help    every target
DONE
