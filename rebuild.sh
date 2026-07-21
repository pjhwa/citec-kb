#!/usr/bin/env bash
# Convenience wrapper — see scripts/docker_rebuild_restart.sh --help
exec "$(cd "$(dirname "$0")" && pwd)/scripts/docker_rebuild_restart.sh" "$@"
