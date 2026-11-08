#!/bin/sh
# Build the release images from committed files only (git archive of HEAD), like CI does: untracked or
# ignored files in your working copy never reach the build context. Uncommitted changes are left out too.
#
#   tools/build-images.sh            # incant
#   tools/build-images.sh local      # incant and incant_local
set -eu
cd "$(git rev-parse --show-toplevel)"
if ! git diff --quiet HEAD --; then
    echo "note: uncommitted changes are not included (building $(git rev-parse --short HEAD))" >&2
fi
git archive HEAD | docker build -t incant -
if [ "${1:-}" = "local" ]; then
    git archive HEAD | docker build -t incant_local --build-arg EXTRAS="qa browser kokoro anthropic nlp mobi torch" -
fi
