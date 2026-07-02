#!/bin/sh
set -e

# Ensure log directory exists
mkdir -p "${LOG_DIR:-/tracet/data/logs}"

exec "$@"
