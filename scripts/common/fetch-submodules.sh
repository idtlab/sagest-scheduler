#!/usr/bin/env bash
# Fetch the submodules in CI. They're private on sagestgit.cs.virginia.edu, so this reads a
# token from SAGESTGIT_TOKEN (a repository secret). The token goes on this one command line
# rather than into any git config, so it isn't left on disk.
set -euo pipefail
: "${SAGESTGIT_TOKEN:?set the SAGESTGIT_TOKEN repository secret}"
host=sagestgit.cs.virginia.edu
git config --global --get-all safe.directory | grep -qx '\*' || git config --global --add safe.directory '*'
git -c "url.https://oauth2:${SAGESTGIT_TOKEN}@$host/.insteadOf=https://$host/" submodule update --init
git submodule status
