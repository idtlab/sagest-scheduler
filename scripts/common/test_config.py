#!/usr/bin/env python3
"""Write a copy of a config for one test run: sets run_dir and bank, and resolves {config_dir}
against the original's directory so the copy can live anywhere (e.g. on scratch).

Usage: test_config.py TEMPLATE OUT --run-dir DIR [--bank BANK]
"""
import argparse
import json
import os

parser = argparse.ArgumentParser()
parser.add_argument("template")
parser.add_argument("out")
parser.add_argument("--run-dir", required=True)
parser.add_argument("--bank")
args = parser.parse_args()

with open(args.template) as handle:
    cfg = json.load(handle)
config_dir = os.path.dirname(os.path.abspath(args.template))
for kind in ("ensemble", "analysis"):
    cfg[kind]["command"] = [c.replace("{config_dir}", config_dir) for c in cfg[kind]["command"]]
cfg["run_dir"] = os.path.abspath(args.run_dir)
if args.bank:
    cfg["bank"] = args.bank
os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
with open(args.out, "w") as handle:
    json.dump(cfg, handle, indent=2)
