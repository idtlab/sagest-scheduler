#!/usr/bin/env python3
"""Write a copy of a config for one test run: sets run_dir and bank, and resolves {config_dir}
against the original's directory so the copy can live anywhere (e.g. on scratch). YOUR_BANK
in any bank is replaced with BANK, so "YOUR_BANK_g" becomes BANK_g.

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
    for section in (cfg, cfg["ensemble"], cfg["analysis"]):
        if "bank" in section:
            section["bank"] = section["bank"].replace("YOUR_BANK", args.bank)
    cfg.setdefault("bank", args.bank)
os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
with open(args.out, "w") as handle:
    json.dump(cfg, handle, indent=2)
