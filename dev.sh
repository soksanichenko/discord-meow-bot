#!/usr/bin/env bash

pushd "${PROJECT_DIR}" || exit 1
python3.9 -m venv --symlinks venv
source venv/bin/activate
python3.9 -m pip install -U pip
pip install -r requirements/dev.txt
deactivate
popd || exit 1
