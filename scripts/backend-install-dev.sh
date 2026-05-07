#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")/../backend"
python -m pip install -r requirements-dev.txt
