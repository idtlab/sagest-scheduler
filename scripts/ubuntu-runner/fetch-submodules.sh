#!/usr/bin/env bash
# Fetch the submodules in CI. They're private on sagestgit.cs.virginia.edu, so this reads a
# token from SAGESTGIT_TOKEN (a repository secret) and only stores it in this job's git config.
set -euo pipefail
: "${SAGESTGIT_TOKEN:?set the SAGESTGIT_TOKEN repository secret}"
git config --global --add safe.directory '*'
git config --global url."https://oauth2:${SAGESTGIT_TOKEN}@sagestgit.cs.virginia.edu/".insteadOf \
    "https://sagestgit.cs.virginia.edu/"
git submodule update --init
git submodule status
