#!/usr/bin/env bash
# System packages for building on Ubuntu 24.04 (the CI container and local WSL). Needs root.
set -euo pipefail
apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends \
    git g++ make cmake curl ca-certificates perl zlib1g-dev
