#!/usr/bin/env bash
# Release script for YNAB Balance Monitor
# Pushes, triggers release workflow, waits, pulls version bump, and rebuilds container.
#
# Usage:
#   ./scripts/release.sh              # prerelease (default)
#   ./scripts/release.sh --stable     # stable release
#   ./scripts/release.sh --rebuild    # rebuild docker only (no new release)
#   ./scripts/release.sh --dry-run    # show what would happen without executing

set -euo pipefail

REPO="bakerboy448/YNAB-Balance-Monitor"
COMPOSE_DIR="/opt/dockergit"
COMPOSE_FILE="servers/home/docker-compose.ynab.yml"
SERVICE="ynab-monitor"
IMAGE="ghcr.io/bakerboy448/ynab-balance-monitor"

# Parse flags
PRERELEASE=true
REBUILD_ONLY=false
DRY_RUN=false
SKIP_PUSH=false

for arg in "$@"; do
    case "$arg" in
        --stable)    PRERELEASE=false ;;
        --rebuild)   REBUILD_ONLY=true ;;
        --dry-run)   DRY_RUN=true ;;
        --skip-push) SKIP_PUSH=true ;;
        -h|--help)
            echo "Usage: $0 [--stable] [--rebuild] [--dry-run] [--skip-push]"
            echo ""
            echo "  --stable     Create a stable release (default: prerelease)"
            echo "  --rebuild    Rebuild Docker image only (no new release)"
            echo "  --dry-run    Show what would happen without executing"
            echo "  --skip-push  Skip git push (already pushed)"
            exit 0
            ;;
        *)
            echo "Unknown flag: $arg" >&2
            exit 1
            ;;
    esac
done

info()  { echo "==> $*"; }
err()   { echo "ERROR: $*" >&2; }
run()   {
    if $DRY_RUN; then
        echo "[dry-run] $*"
    else
        "$@"
    fi
}

# --- Preflight checks ---
info "Preflight checks"

if ! command -v gh &>/dev/null; then
    err "gh CLI not found"
    exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
    err "Working tree is dirty — commit or stash first"
    git status --short
    exit 1
fi

BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$BRANCH" != "main" ]; then
    err "Not on main branch (on: $BRANCH)"
    exit 1
fi

# --- Push ---
if ! $SKIP_PUSH; then
    info "Pushing to origin"
    run git push
fi

# --- Trigger workflow ---
if $REBUILD_ONLY; then
    info "Triggering Docker rebuild (no new release)"
    if ! $DRY_RUN; then
        gh workflow run release.yml -f rebuild_docker=true --repo "$REPO"
    fi
else
    RELEASE_TYPE=$( $PRERELEASE && echo "prerelease" || echo "stable" )
    info "Triggering $RELEASE_TYPE release"
    if ! $DRY_RUN; then
        gh workflow run release.yml -f prerelease=$PRERELEASE --repo "$REPO"
    fi
fi

if $DRY_RUN; then
    info "Dry run complete"
    exit 0
fi

# --- Wait for workflow ---
info "Waiting for workflow to start..."
sleep 5

RUN_ID=$(gh run list --repo "$REPO" --workflow release.yml --limit 1 --json databaseId --jq '.[0].databaseId')
if [ -z "$RUN_ID" ]; then
    err "Could not find workflow run"
    exit 1
fi
info "Workflow run: $RUN_ID"
info "https://github.com/$REPO/actions/runs/$RUN_ID"

gh run watch "$RUN_ID" --repo "$REPO" --exit-status
info "Workflow completed"

# --- Pull version bump ---
info "Pulling version bump"
git pull --rebase

# --- Get new tag ---
NEW_TAG=$(git describe --tags --abbrev=0 | sed 's/^v//')
if [ -z "$NEW_TAG" ]; then
    err "Could not determine new tag"
    exit 1
fi
info "New version: $NEW_TAG"

# --- Update compose ---
COMPOSE_PATH="$COMPOSE_DIR/$COMPOSE_FILE"
OLD_IMAGE_LINE=$(grep "image: $IMAGE" "$COMPOSE_PATH" || true)
if [ -z "$OLD_IMAGE_LINE" ]; then
    err "Could not find image line in $COMPOSE_PATH"
    exit 1
fi

OLD_TAG=$(echo "$OLD_IMAGE_LINE" | sed "s|.*$IMAGE:||" | tr -d '[:space:]')
if [ "$OLD_TAG" = "$NEW_TAG" ]; then
    info "Compose already pinned to $NEW_TAG"
else
    info "Updating compose: $OLD_TAG -> $NEW_TAG"
    sed -i "s|$IMAGE:$OLD_TAG|$IMAGE:$NEW_TAG|" "$COMPOSE_PATH"
fi

# --- Rebuild container ---
info "Pulling image and recreating container"
cd "$COMPOSE_DIR"
op run --env-file .env -- docker compose up -d --pull always --force-recreate "$SERVICE"

info "Done — $SERVICE running $IMAGE:$NEW_TAG"
