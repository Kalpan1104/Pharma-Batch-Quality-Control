#!/usr/bin/env bash
# Copies Terraform outputs into GitHub Actions variables and creates the "production" environment
# with you as the required reviewer. Run from the repo root after `make tf-apply`.
set -euo pipefail
command -v gh >/dev/null || { echo "Install the GitHub CLI first: brew install gh"; exit 1; }
REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner)"

echo "==> Setting Actions variables on $REPO"
terraform -chdir=infra/terraform output -json github_variables \
  | python3 -c 'import json,sys; [print(f"{k}\t{v}") for k, v in json.load(sys.stdin).items()]' \
  | while IFS=$'\t' read -r key value; do
      gh variable set "$key" --body "$value" --repo "$REPO" >/dev/null && echo "  $key"
    done

echo "==> Creating environment 'production' (you approve every release)"
USER_ID="$(gh api user -q .id)"
gh api --method PUT "repos/$REPO/environments/production" --input - >/dev/null <<JSON
{"reviewers": [{"type": "User", "id": $USER_ID}], "deployment_branch_policy": null}
JSON
echo "Done. Check: gh variable list   |   Settings -> Environments -> production"
