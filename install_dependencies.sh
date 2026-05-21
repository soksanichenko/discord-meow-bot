#!/usr/bin/env bash
set -euo pipefail

pip install -r sources/requirements.txt
pip install pre-commit
ansible-galaxy install -r requirements.yml
pre-commit install
