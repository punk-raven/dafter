#!/usr/bin/env bash
set -euo pipefail

# Fetch the pinned lk (livekit-cli) release binary for this OS and CPU into
# the path given, for scripts/loadtest-media.sh.
#
# The release tarball is used instead of `go install` on purpose: the video
# clips lk's load-test publishers loop live in Git LFS, and a build from the
# module proxy embeds the LFS pointer files in their place. Such a binary
# connects its publishers but never sends a frame, and every subscriber
# then reports 0/N tracks.
#
# Usage: scripts/install-lk.sh VERSION DEST

version=$1 dest=$2

case $(uname -s) in
  Linux) os=linux ;;
  Darwin) os=darwin ;;
  *) echo "install-lk: unsupported OS $(uname -s)" >&2; exit 1 ;;
esac
case $(uname -m) in
  x86_64 | amd64) arch=amd64 ;;
  aarch64 | arm64) arch=arm64 ;;
  *) echo "install-lk: unsupported CPU $(uname -m)" >&2; exit 1 ;;
esac

url="https://github.com/livekit/livekit-cli/releases/download/v${version}/lk_${version}_${os}_${arch}.tar.gz"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

echo "  fetching lk $version ($os/$arch)"
curl -fsSL --retry 3 -o "$tmp/lk.tar.gz" "$url"
tar -xzf "$tmp/lk.tar.gz" -C "$tmp" lk
mkdir -p "$(dirname "$dest")"
install -m 0755 "$tmp/lk" "$dest"
"$dest" --version
