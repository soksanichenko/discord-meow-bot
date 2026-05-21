#!/usr/bin/env bash
set -euo pipefail

pip install -r sources/requirements.txt
pip install pre-commit
ansible-galaxy install -r requirements.yml
pre-commit install
cp hooks/pre-push .git/hooks/pre-push
chmod +x .git/hooks/pre-push
