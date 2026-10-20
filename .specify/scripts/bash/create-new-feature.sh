#!/usr/bin/env bash
# Scaffold a new spec folder: specs/NNN-short-name/ with spec.md, plan.md, tasks.md
# copied from .specify/templates/. Numbering continues from the highest existing spec.
#
# Usage: .specify/scripts/bash/create-new-feature.sh "short-name"
set -euo pipefail

name="${1:?usage: create-new-feature.sh <short-name>}"
root="$(git rev-parse --show-toplevel)"
specs="$root/specs"
tpl="$root/.specify/templates"

last=$(ls -1 "$specs" 2>/dev/null | grep -E '^[0-9]{3}-' | sort | tail -1 | cut -c1-3 || true)
next=$(printf '%03d' $(( 10#${last:-0} + 1 )))
dir="$specs/$next-$name"

mkdir -p "$dir/contracts"
today=$(date +%Y-%m-%d)
for f in spec plan tasks; do
  sed -e "s/\[###-feature-name\]/$next-$name/g" -e "s/\[DATE\]/$today/g" \
      "$tpl/$f-template.md" > "$dir/$f.md"
done
cp "$tpl/checklist-template.md" "$dir/checklist.md"
echo "$dir"
