#!/usr/bin/env python3
"""Stands in for libMLEF's mlef, which needs a GPU: reads the same input file, checks the
files it names exist, and writes an identity analysis where mlef would write its own."""
import glob
import os
import shutil
import sys

with open(sys.argv[1]) as handle:
    settings = dict(line.split("=", 1) for line in handle if "=" in line and line[0] != "#")
settings = {key.strip(): value.strip() for key, value in settings.items()}
if not os.path.exists(settings.get("observation_file", "")):
    sys.exit(f"fake_mlef: no observation file {settings.get('observation_file')}")
name = f"{settings['filename_prefix']}{int(settings['snapshot_step']):06d}.cgns"
members = sorted(glob.glob(os.path.join(settings["ensemble_root"], settings["member_prefix"] + "*")))
for member in members:
    out = os.path.join(settings["analysis_output_dir"], os.path.basename(member))
    os.makedirs(out, exist_ok=True)
    for path in [os.path.join(member, name), *glob.glob(os.path.join(member, "*_grid_*"))]:
        shutil.copy2(path, out)
# The lines check_euler_run.py reads from mlef's output; the shift is made up.
print("fake_mlef: ||x̄_a||_2 = 1   (||Δ|| = 1)")
print(f"fake_mlef: wrote {len(members)} analysis member(s)")
