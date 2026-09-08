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

# A failure here is not fatal. Creating a Space through the API returns 402 on
# accounts without the paid tier, and it also fails when the Space already exists.
# Either way the push below is what decides, so it always gets its turn.
case "$created" in
  200|201) echo "  the Space was created" ;;
  409)     echo "  the Space already exists" ;;
  *)       echo "  could not create it through the API; assuming it already exists" ;;
esac

cd "$staging"
git init -q

# The Hub rejects any binary file that is not in LFS, whatever its size, so the
# corpus has to be tracked before it is added.
if ! git lfs version >/dev/null 2>&1; then
  cat >&2 <<'EOF'
git-lfs is not installed, and the Hub will not accept the corpus without it.

  Windows   it ships with Git for Windows; run:  git lfs install
  macOS     brew install git-lfs && git lfs install
  Debian    sudo apt install git-lfs && git lfs install

Then run this command again.
EOF
  exit 1
fi
git lfs install --local >/dev/null
git lfs track "deploy/*.gz" >/dev/null
git add .gitattributes
git add -A
git -c user.email=deploy@localhost -c user.name=deploy commit -qm "Deploy fact knowledge layer"

remote="https://huggingface.co/spaces/$user/$space"
if [ -n "${HF_TOKEN:-}" ]; then
  remote="https://$user:$HF_TOKEN@huggingface.co/spaces/$user/$space"
fi
echo "Pushing to $user/$space ..."
if ! git push --force "$remote" HEAD:main; then
  cat >&2 <<EOF

The push failed. If it said "Repository not found", the Space does not exist yet.
Create it once in the browser — it is free and takes about thirty seconds — then
run this command again:

  1. Open  https://huggingface.co/new-space
  2. Choose "Manual setup", not the AI agent option
  3. Owner / name:  $user / $space
     Select the SDK: Gradio  ->  Blank   (Docker Spaces are a paid feature)
     Hardware:       the free tier offered
     Visibility:     Public
  4. Click "Create Space", then re-run this command.

EOF
  exit 1
fi

echo
echo "Deployed. It builds for a few minutes, then serves at:"
echo "  https://$user-$space.hf.space"
