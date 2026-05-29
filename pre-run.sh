#!/usr/bin/env bash
set -e

echo "=== Running tests ==="
python -m pytest "${PROJECT_DIR}/tests/" -q --tb=short
echo "=== Tests passed ==="

pushd "${PROJECT_DIR}/ansible" || exit 1
ansible-playbook -i "inventories/zelgray.work" -vv "playbooks/deploy.yml"
popd || exit 1
