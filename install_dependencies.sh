#!/usr/bin/env bash
set -euo pipefail

pip install -r sources/prod.txt
ansible-galaxy install -r requirements.yml
