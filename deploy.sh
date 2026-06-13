#!/usr/bin/env bash
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
REGISTRY="ghcr.io/soksanichenko/discord-meow-bot"
IMAGE_TAG="dev"
LOCAL_TRANSFER=false
INVENTORY="inventories/zelgray.work"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --local) LOCAL_TRANSFER=true; shift;;
    -i|--inventory) INVENTORY="$2"; shift 2;;
    *) echo "Unknown option: $1"; exit 1;;
  esac
done

echo "=== Infisical vars ==="
: "${INFISICAL_CLIENT_ID:?INFISICAL_CLIENT_ID is not set}"
: "${INFISICAL_CLIENT_SECRET:?INFISICAL_CLIENT_SECRET is not set}"
: "${INFISICAL_API_URL:?INFISICAL_API_URL is not set}"

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

echo "=== Deploying ==="
pushd "${PROJECT_DIR}/ansible" || exit 1
INFISICAL_API_URL="${INFISICAL_API_URL}" \
INFISICAL_CLIENT_ID="${INFISICAL_CLIENT_ID}" \
INFISICAL_CLIENT_SECRET="${INFISICAL_CLIENT_SECRET}" \
ansible-playbook -i "${INVENTORY}" -vv "playbooks/deploy.yml" ${EXTRA_VARS}
popd || exit 1
