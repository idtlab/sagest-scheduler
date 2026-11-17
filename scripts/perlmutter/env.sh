# Sourced by the other Perlmutter scripts: compilers for the CPU build, and Python 3 for
# campaign.py. The serial CPU build wants plain g++, not the Cray wrapper (CC), which would
# pull in MPI and GPU libraries.
set +u  # module scripts read unset variables
module load PrgEnv-gnu cmake python
set -u
export CXX=${CXX:-g++}
