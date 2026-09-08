#!/usr/bin/env bash
# Publish this project to a Hugging Face Space.
#
#   deploy/publish.sh <hf-username> <space-name>
#
# Needs a Hugging Face account and a write token (https://huggingface.co/settings/tokens).
# The token is read from HF_TOKEN, or git will prompt for it as the password.
#
# Uploads are disabled on the Space until a model key is added:
#   Space -> Settings -> Variables and secrets -> New secret
#     GEMINI_API_KEY = <your key>
# Browsing the prepared corpus works without one.
set -euo pipefail

user="${1:?usage: deploy/publish.sh <hf-username> <space-name>}"
space="${2:?usage: deploy/publish.sh <hf-username> <space-name>}"
root="$(cd "$(dirname "$0")/.." && pwd)"

if [ ! -f "$root/deploy/factlayer.db.gz" ]; then
  echo "deploy/factlayer.db.gz is missing. Build it with:" >&2
  echo "  python -m factlayer ingest data/*/*.pdf && gzip -9 -c factlayer.db > deploy/factlayer.db.gz" >&2
  exit 1
fi

staging="$(mktemp -d)"
trap 'rm -rf "$staging"' EXIT

git -C "$root" archive HEAD | tar -x -C "$staging"
# A Space is identified by the YAML header of its README, which the project's own
# README must not carry.
cp "$root/deploy/factlayer.db.gz" "$staging/deploy/factlayer.db.gz"
mv "$staging/deploy/README-space.md" "$staging/README.md"

cd "$staging"
git init -q
git lfs install --local >/dev/null 2>&1 || true
git lfs track "deploy/*.gz" >/dev/null 2>&1 || true
[ -f .gitattributes ] && git add .gitattributes
git add -A
git -c user.email=deploy@localhost -c user.name=deploy commit -qm "Deploy fact knowledge layer"

remote="https://huggingface.co/spaces/$user/$space"
if [ -n "${HF_TOKEN:-}" ]; then
  remote="https://$user:$HF_TOKEN@huggingface.co/spaces/$user/$space"
fi
git push --force "$remote" HEAD:main

echo
echo "Deployed. It builds for a few minutes, then serves at:"
echo "  https://$user-$space.hf.space"
