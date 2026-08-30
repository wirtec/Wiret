#!/usr/bin/env bash
# Copy the GitHub Action workflows into .github/workflows/ and commit them.
#
# Why this script exists: the bot token that opened the pull request is not
# allowed to create or update files under .github/workflows/ (GitHub requires
# the `workflows` permission). Run this once locally, with your own git user:
#
#   bash ci/install-workflows.sh && git push
#
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

mkdir -p .github/workflows
cp ci/workflows/scrape-news.yml .github/workflows/scrape-news.yml
cp ci/workflows/tests.yml       .github/workflows/tests.yml

echo "Installed:"
echo "  .github/workflows/scrape-news.yml   (hourly cron: 0 * * * *)"
echo "  .github/workflows/tests.yml         (offline unit tests)"

if git diff --quiet -- .github/workflows && git diff --cached --quiet -- .github/workflows; then
  if [ -z "$(git ls-files --others --exclude-standard .github/workflows)" ]; then
    echo "Nothing to commit - workflows already up to date."
    exit 0
  fi
fi

git add .github/workflows
git commit -m "ci: add hourly Google News scrape workflow and test workflow"

cat <<'EOF'

Next steps
  1) git push
  2) Repo Settings -> Actions -> General -> Workflow permissions
     -> select "Read and write permissions"  (so the job can commit news.json)
  3) Actions tab -> "Scrape Google News" -> Run workflow (to test it immediately)
EOF
