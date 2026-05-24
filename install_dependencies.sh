#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

pip install -r "$SCRIPT_DIR/requirements.txt"
ansible-galaxy install -r "$SCRIPT_DIR/ansible/requirements.yml"
pre-commit install

INFISICAL_BINARY="/usr/local/bin/infisical"

if [[ ! -f "$INFISICAL_BINARY" ]]; then
    echo "Installing infisical CLI..."
    curl -1sLf 'https://artifacts-cli.infisical.com/setup.rpm.sh' | sudo bash
    sudo dnf install -y infisical
    echo "infisical CLI installed"
fi
