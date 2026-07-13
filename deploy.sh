#!/usr/bin/env bash
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
REGISTRY="ghcr.io/soksanichenko/discord-meow-bot"
IMAGE_TAG="dev"
LOCAL_TRANSFER=false
INVENTORY="inventories/zelgray.work"
ANSIBLE_TAGS=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --local) LOCAL_TRANSFER=true; shift;;
    -i|--inventory) INVENTORY="$2"; shift 2;;
    --tags) ANSIBLE_TAGS="$2"; shift 2;;
    *) echo "Unknown option: $1"; exit 1;;
  esac
done

GH_REPO="${REGISTRY#ghcr.io/}"

# Prod (zelgray.work) full deploys go through the existing GitHub Actions
# Test -> Build -> Deploy chain instead of building/pushing locally, so no
# local GHCR credentials are needed. --tags runs (infra-only changes) and
# other inventories (e.g. home-server) keep the local build+push+ansible path.
if [[ -z "$ANSIBLE_TAGS" && "$(basename "$INVENTORY")" == "zelgray.work" ]]; then
  echo "=== Running tests ==="
  python -m pytest "${PROJECT_DIR}/tests/" -q --tb=short

  BRANCH="$(git -C "$PROJECT_DIR" rev-parse --abbrev-ref HEAD)"
  if [[ "$BRANCH" != "main" ]]; then
    echo "Prod auto-deploy only triggers from 'main' (current branch: ${BRANCH})." >&2
    exit 1
  fi

  if [[ -n "$(git -C "$PROJECT_DIR" status --porcelain)" ]]; then
    echo "Working tree has uncommitted changes — commit first." >&2
    exit 1
  fi

  watch_workflow() {
    local workflow="$1"
    echo "=== Waiting for '${workflow}' run ==="
    local run_id=""
    for _ in $(seq 1 60); do
      run_id=$(gh run list --repo "$GH_REPO" --workflow "$workflow" --branch main \
        --json databaseId,createdAt \
        --jq "[.[] | select(.createdAt > \"${SINCE}\")] | sort_by(.createdAt) | .[0].databaseId // empty" \
        2>/dev/null || true)
      [[ -n "$run_id" ]] && break
      sleep 5
    done
    if [[ -z "$run_id" ]]; then
      echo "Timed out waiting for '${workflow}' to start." >&2
      exit 1
    fi
    gh run watch "$run_id" --repo "$GH_REPO" --exit-status
  }

  SINCE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "=== Pushing main to trigger Test -> Build -> Deploy ==="
  git -C "$PROJECT_DIR" push origin main

  watch_workflow "test.yml"
  watch_workflow "build.yml"
  watch_workflow "deploy.yml"

  echo "=== Deploy complete ==="
  exit 0
fi

if [[ -z "$ANSIBLE_TAGS" ]]; then
  echo "=== Running tests ==="
  python -m pytest "${PROJECT_DIR}/tests/" -q --tb=short

  mkdir -p "${PROJECT_DIR}/frontend"

  echo "=== Building Docker image ==="
  docker build -t "${REGISTRY}:${IMAGE_TAG}" "${PROJECT_DIR}"

  if [[ "$LOCAL_TRANSFER" == true ]]; then
    echo "=== Transferring image directly to remote (skipping GHCR push) ==="
    SSH_TARGET=$(cd "${PROJECT_DIR}/ansible" && \
      ansible-inventory -i "${INVENTORY}" --list | \
      python3 -c "import sys,json; h=json.load(sys.stdin)['_meta']['hostvars']; v=list(h.values())[0]; print(v['ansible_user']+'@'+v['ansible_host'])")
    docker save "${REGISTRY}:${IMAGE_TAG}" | ssh "${SSH_TARGET}" docker load
    EXTRA_VARS="-e bot_image_tag=${IMAGE_TAG} -e bot_image_pull=never"
  else
    echo "=== Pushing Docker image to GHCR ==="
    docker push "${REGISTRY}:${IMAGE_TAG}"
    EXTRA_VARS="-e bot_image_tag=${IMAGE_TAG}"
  fi
fi

TAGS_ARG=""
[[ -n "$ANSIBLE_TAGS" ]] && TAGS_ARG="--tags ${ANSIBLE_TAGS}"

echo "=== Deploying ==="
pushd "${PROJECT_DIR}/ansible" || exit 1
ansible-playbook -i "${INVENTORY}" -vv "playbooks/deploy.yml" ${EXTRA_VARS} ${TAGS_ARG}
popd || exit 1
