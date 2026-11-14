"""Real euler-miniapp members and the MLEF stub. Needs the CPU build (scripts/<platform>/build.sh);
the campaign tests also need Flux: flux start --test-size=2 python3 -m unittest discover -s tests"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import campaign  # noqa: E402

MEMBER = os.path.join(ROOT, "components", "euler_member.py")
STUB = os.path.join(ROOT, "components", "mlef_stub.py")
EXE = os.environ.get("EULER_EXE") or os.path.join(ROOT, "external", "euler-miniapp", "main_cpu")
BUILT = os.access(EXE, os.X_OK)
IN_FLUX = bool(os.environ.get("FLUX_URI")) and shutil.which("flux")
SHOCK = {"SHOCK.pressureLow": 10000, "SHOCK.pressureHigh": 100000,
         "SHOCK.densityLow": 0.125, "SHOCK.densityHigh": 1.0}


def member_params(**extra):
    return dict({"exe": EXE, "steps_per_cycle": 5, "overrides": {"number_cells": "15 15"},
                 "perturb": {"sigma": 0.05, "seed_base": 1, "keys": SHOCK}}, **extra)


@unittest.skipUnless(BUILT, f"{EXE} not built; run scripts/<platform>/build.sh")
class ComponentTests(unittest.TestCase):
    """Runs the components directly, the way campaign.py's jobs would."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="euler-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def run_member(self, member, cycle, params, attempt=1):
        attempt_dir = os.path.join(self.tmp, f"cycle_{cycle}", f"member_{member}",
                                   f"attempt_{attempt}")
        os.makedirs(attempt_dir)
        with open(os.path.join(attempt_dir, "params.json"), "w") as handle:
            json.dump(params, handle)
        done = subprocess.run([sys.executable, MEMBER], encoding="utf-8",
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=dict(os.environ, ORCH_DIR=attempt_dir, ORCH_MEMBER_ID=str(member),
                                       ORCH_CYCLE=str(cycle), ORCH_ATTEMPT=str(attempt),
                                       ORCH_PARAMS=os.path.join(attempt_dir, "params.json")))
        return done, attempt_dir

    def run_stub(self, cycle, member_dirs):
        attempt_dir = os.path.join(self.tmp, f"cycle_{cycle}", "analysis", "attempt_1")
        os.makedirs(attempt_dir)
        manifest = os.path.join(self.tmp, f"cycle_{cycle}", "manifest.json")
        with open(manifest, "w") as handle:
            json.dump({"cycle": cycle, "members": [{"name": f"member_{i}", "dir": d}
                                                   for i, d in enumerate(member_dirs)]}, handle)
        done = subprocess.run([sys.executable, STUB], encoding="utf-8",
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=dict(os.environ, ORCH_DIR=attempt_dir, ORCH_MANIFEST=manifest))
        return done, attempt_dir

    def result(self, attempt_dir):
        with open(os.path.join(attempt_dir, "result.json")) as handle:
            return json.load(handle)

    def test_members_get_different_reproducible_initial_conditions(self):
        runs = [self.run_member(member, 0, member_params()) for member in (0, 1)]
        for done, _ in runs:
            self.assertEqual(done.returncode, 0, done.stderr)
        first, second = (self.result(d)["initial"] for _, d in runs)
        self.assertNotEqual(first["SHOCK.pressureLow"], second["SHOCK.pressureLow"])
        done, again = self.run_member(0, 0, member_params(), attempt=2)
        self.assertEqual(done.returncode, 0, done.stderr)
        repeat = self.result(again)["initial"]
        self.assertEqual({k: repeat[k] for k in SHOCK}, {k: first[k] for k in SHOCK})
        result = self.result(runs[0][1])
        self.assertEqual(result["step"], 5)
        self.assertTrue(result["snapshot"].endswith("ShockTube_2d_000005.cgns"))
        self.assertTrue(os.path.exists(result["snapshot"]))

    def test_stub_analysis_feeds_restart_of_next_cycle(self):
        dirs = []
        for member in (0, 1):
            done, attempt_dir = self.run_member(member, 0, member_params())
            self.assertEqual(done.returncode, 0, done.stderr)
            dirs.append(attempt_dir)
        done, analysis_dir = self.run_stub(0, dirs)
        self.assertEqual(done.returncode, 0, done.stderr)
        with open(os.path.join(analysis_dir, "next_cycle.json")) as handle:
            restart_dir = json.load(handle)["params"]["restart_dir"]
        for member in (0, 1):
            self.assertTrue(os.path.exists(os.path.join(
                restart_dir, f"mem_{member:03d}", "ShockTube_2d_000005.cgns")))
            self.assertTrue(os.path.islink(os.path.join(analysis_dir, "forecast",
                                                        f"mem_{member:03d}")))
        with open(os.path.join(analysis_dir, "mlef.input")) as handle:
            self.assertIn("snapshot_step       = 5", handle.read())

        done, attempt_dir = self.run_member(1, 1, member_params(restart_dir=restart_dir))
        self.assertEqual(done.returncode, 0, done.stderr)
        with open(os.path.join(attempt_dir, "euler.log")) as handle:
            self.assertIn("Resumed from CGNS restart at step -- 5", handle.read())
        self.assertEqual(self.result(attempt_dir)["step"], 10)

    def test_stub_rejects_members_at_different_steps(self):
        done0, dir0 = self.run_member(0, 0, member_params())
        done1, dir1 = self.run_member(1, 0, member_params(steps_per_cycle=6))
        self.assertEqual((done0.returncode, done1.returncode), (0, 0))
        done, _ = self.run_stub(0, [dir0, dir1])
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("different steps", done.stderr)

    def test_blowup_exits_with_instability_code(self):
        done, _ = self.run_member(0, 0, member_params(
            overrides={"number_cells": "15 15", "dt": "1.0e-2"}, steps_per_cycle=20))
        self.assertEqual(done.returncode, 20, done.stderr)

    def test_missing_restart_file_fails(self):
        done, _ = self.run_member(0, 1, member_params(restart_dir=self.tmp))
        self.assertEqual(done.returncode, 1)
        self.assertIn("restart file", done.stderr)


@unittest.skipUnless(BUILT and IN_FLUX, "needs the CPU build and a Flux instance")
class CampaignTests(unittest.TestCase):
    """The shipped ubuntu-runner config, shrunk, run through campaign.py and Flux."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="euler-campaign-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def go(self, cycles=2, size=3, **params):
        with open(os.path.join(ROOT, "configs", "ubuntu-runner.json")) as handle:
            raw = json.load(handle)
        raw.update(run_dir="run", cycles=cycles, poll_seconds=0.2)
        raw["ensemble"]["size"] = size
        raw["ensemble"]["params"].update(member_params(**params))
        for kind in ("ensemble", "analysis"):
            raw[kind]["command"] = [sys.executable, MEMBER if kind == "ensemble" else STUB]
        path = os.path.join(self.tmp, "config.json")
        with open(path, "w") as handle:
            json.dump(raw, handle)
        cfg = campaign.load_config(path)
        code = campaign.run(cfg, "login")
        with open(os.path.join(cfg["run_dir"], "state.json")) as handle:
            return code, json.load(handle)

    def test_members_cycle_through_the_analysis(self):
        code, state = self.go()
        self.assertEqual((code, state["status"]), (0, "completed"))
        self.assertEqual(len(state["cycles"]), 2)
        analysis_dir = state["cycles"][0]["analysis"]["dir"]
        for task in state["cycles"][1]["members"]:
            self.assertEqual(task["params"]["restart_dir"], os.path.join(analysis_dir, "analysis"))
            with open(os.path.join(task["dir"], "euler.log")) as handle:
                self.assertIn("Resumed from CGNS restart at step -- 5", handle.read())
            with open(os.path.join(task["dir"], "result.json")) as handle:
                self.assertEqual(json.load(handle)["step"], 10)

    def test_blowup_is_a_predicted_fatal_failure(self):
        code, state = self.go(cycles=1, size=2,
                              overrides={"number_cells": "15 15", "dt": "1.0e-2"},
                              steps_per_cycle=20)
        self.assertEqual(code, 1)
        member = state["cycles"][0]["members"][0]
        self.assertEqual(member["history"][0]["reason"], "NUMERICAL_INSTABILITY")
        self.assertEqual(member["attempt"], 1)
        self.assertIsNone(state["cycles"][0]["analysis"])


if __name__ == "__main__":
    unittest.main()
