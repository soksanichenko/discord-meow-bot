#!/usr/bin/env bash

pushd "${PROJECT_DIR}/ansible" || exit 1
ansible-playbook -i "inventories/zelgray.work" -vv "playbooks/deploy.yml"
popd || exit 1
