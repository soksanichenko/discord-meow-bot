#!/usr/bin/env bash
set -euo pipefail

pip install -r sources/requirements.txt
ansible-galaxy install -r requirements.yml
