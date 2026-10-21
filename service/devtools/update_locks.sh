#!/usr/bin/env bash
# Regenerate (or --check) the dependency locks (spec 011).
#
#   service/requirements.lock       service runtime: extras ai + metrics   (Docker image)
#   service/requirements-dev.lock   + dev extra                            (CI, local tests)
#   requirements-pipeline.lock      src/pipeline + tools/ from requirements-pipeline.in
#
# Usage (from anywhere in the repo):
#   service/devtools/update_locks.sh             re-resolve, keeping existing pins unless
#                                                pyproject.toml / requirements-pipeline.in changed
#   CONSTRAINTS=frozen.txt service/devtools/update_locks.sh
#                                                re-pin from a known-good env (`pip freeze`)
#   service/devtools/update_locks.sh --check     fail if any lock is stale (CI)
#
# Each lock is constrained by itself by default, which is what makes --check meaningful:
# re-compiling an up-to-date lock reproduces it byte for byte.
set -euo pipefail

root="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
check=0
[[ "${1:-}" == "--check" ]] && check=1
out_dir="$root"
if (( check )); then out_dir="$(mktemp -d)"; mkdir -p "$out_dir/service"; fi

opts=(--python-version 3.11 --universal --no-header --no-annotate --quiet)

compile() {  # compile <source> <lock path relative to root> [extra ...]
  local src="$1" lock="$2"; shift 2
  local extras=()
  for e in "$@"; do extras+=(--extra "$e"); done
  local constraint="${CONSTRAINTS:-$root/$lock}"
  local cargs=()
  [[ -f "$constraint" ]] && cargs=(-c "$constraint")
  uv pip compile "$root/$src" "${extras[@]}" "${opts[@]}" "${cargs[@]}" -o "$out_dir/$lock"
}

compile service/pyproject.toml service/requirements.lock ai metrics
compile service/pyproject.toml service/requirements-dev.lock ai metrics dev
compile requirements-pipeline.in requirements-pipeline.lock

if (( check )); then
  stale=0
  for lock in service/requirements.lock service/requirements-dev.lock requirements-pipeline.lock; do
    if ! diff -q "$root/$lock" "$out_dir/$lock" >/dev/null; then
      echo "STALE: $lock -- run service/devtools/update_locks.sh and commit the result"
      diff "$root/$lock" "$out_dir/$lock" | head -20
      stale=1
    fi
  done
  rm -rf "$out_dir"
  (( stale == 0 )) && echo "locks are up to date"
  exit "$stale"
fi
echo "locks written"
