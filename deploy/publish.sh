#!/usr/bin/env bash
# Publish this project to a Hugging Face Space.
#
#   HF_TOKEN=hf_xxx deploy/publish.sh              # username read from the token
#   deploy/publish.sh <hf-username> <space-name>   # or name them yourself
#
# Needs a Hugging Face account and a write token (https://huggingface.co/settings/tokens).
# The token is read from HF_TOKEN, or git will prompt for it as the password.
#
# Uploads are disabled on the Space until a model key is added:
#   Space -> Settings -> Variables and secrets -> New secret
#     GEMINI_API_KEY = <your key>
# Browsing the prepared corpus works without one.
set -euo pipefail

space="${2:-factlayer}"
root="$(cd "$(dirname "$0")/.." && pwd)"

# The username is whoever the token belongs to, so it does not have to be typed
# and cannot be typed wrong.
user="${1:-}"
if [ -z "$user" ]; then
  if [ -z "${HF_TOKEN:-}" ]; then
    echo "Set HF_TOKEN, or pass the username: deploy/publish.sh <hf-username> [space]" >&2
    exit 1
  fi
  user=$(curl -sS -H "Authorization: Bearer $HF_TOKEN" \
           https://huggingface.co/api/whoami-v2 |
         python3 -c "import json,sys; print(json.load(sys.stdin)['name'])" 2>/dev/null || true)
  if [ -z "$user" ]; then
    echo "Could not read the account for that token. Is it a Write token?" >&2
    exit 1
  fi
  echo "Deploying as $user"
fi

if [ ! -f "$root/deploy/factlayer.db.gz" ]; then
  echo "deploy/factlayer.db.gz is missing. Build it with:" >&2
  echo "  python -m factlayer ingest data/*/*.pdf && gzip -9 -c factlayer.db > deploy/factlayer.db.gz" >&2
  exit 1
fi

staging="$(mktemp -d)"
trap 'rm -rf "$staging"' EXIT

git -C "$root" archive HEAD | tar -x -C "$staging"

# The Space is a deployment, not a copy of the repository. Only what the container
# runs is pushed: the source PDFs and the test suite are twenty megabytes the image
# never reads, and the corpus already holds everything extracted from them.
rm -rf "$staging/data" "$staging/docs" "$staging/tests"

# A Space is identified by the YAML header of its README, which the project's own
# README must not carry.
cp "$root/deploy/factlayer.db.gz" "$staging/deploy/factlayer.db.gz"
mv "$staging/deploy/README-space.md" "$staging/README.md"

# Create the Space if it is not there yet. Harmless when it already exists.
created=""
if [ -n "${HF_TOKEN:-}" ]; then
  echo "Creating the Space (skipped if it already exists)..."
  created=$(curl -sS -X POST https://huggingface.co/api/repos/create \
    -H "Authorization: Bearer $HF_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"type\":\"space\",\"name\":\"$space\",\"sdk\":\"docker\",\"private\":false}" \
    -o /dev/null -w "%{http_code}" 2>/dev/null || true)
  created="${created:-000}"
  echo "  create: HTTP $created"
fi

# 402 and 403 mean the account cannot create a Space through the API. Creating it
# once in the browser costs nothing and makes every later push work.
case "$created" in
  402|403)
    cat >&2 <<EOF

The API would not create the Space (HTTP $created). Create it once by hand, which
takes about thirty seconds, then run this command again:

  1. Open  https://huggingface.co/new-space
  2. Choose "Manual setup" (not the AI agent option)
  3. Space name:   $space
     Licence:      mit
     Select the SDK: Docker  ->  Blank
     Hardware:     CPU basic (free)
     Visibility:   Public
  4. Click "Create Space", then re-run:

       HF_TOKEN=... deploy/publish.sh $user $space

EOF
    exit 1
    ;;
esac

cd "$staging"
git init -q
git add -A
git -c user.email=deploy@localhost -c user.name=deploy commit -qm "Deploy fact knowledge layer"

remote="https://huggingface.co/spaces/$user/$space"
if [ -n "${HF_TOKEN:-}" ]; then
  remote="https://$user:$HF_TOKEN@huggingface.co/spaces/$user/$space"
fi
echo "Pushing to $user/$space ..."
git push --force "$remote" HEAD:main

echo
echo "Deployed. It builds for a few minutes, then serves at:"
echo "  https://$user-$space.hf.space"
