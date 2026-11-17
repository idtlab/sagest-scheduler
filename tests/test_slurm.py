"""campaign.py's Slurm code. The parsing tests use real Slurm output formats; the campaign tests
run against tests/fake_slurm, which mimics sbatch, sacct, squeue and srun with local processes."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import campaign  # noqa: E402

COMPONENT = [sys.executable, os.path.join(ROOT, "tests", "component.py")]
FAKE_BIN = os.path.join(ROOT, "tests", "fake_slurm", "bin")
FAKE_MINUTE = 2.0  # real seconds per Slurm minute in the fake


class SlurmParsingTests(unittest.TestCase):
    def test_time_limits_round_up_to_minutes(self):
        self.assertEqual(campaign.slurm_time(1), "0:01:00")
        self.assertEqual(campaign.slurm_time(60), "0:01:00")
        self.assertEqual(campaign.slurm_time(61), "0:02:00")
        self.assertEqual(campaign.slurm_time(5400), "1:30:00")

    def test_squeue_time_left(self):
        self.assertEqual(campaign.parse_slurm_time("1-02:03:04"), 93784)
        self.assertEqual(campaign.parse_slurm_time("02:03:04"), 7384)
        self.assertEqual(campaign.parse_slurm_time("05:06"), 306)
        self.assertIsNone(campaign.parse_slurm_time("UNLIMITED"))
        self.assertIsNone(campaign.parse_slurm_time("NOT_SET"))

    def test_sacct_states(self):
        out = ("101|COMPLETED|0:0|12\n102|FAILED|20:0|3\n103|TIMEOUT|0:0|60\n"
               "104|CANCELLED by 12345|0:15|7\n105|RUNNING|0:0|4\n106|PENDING|0:0|0\n"
               "107|OUT_OF_MEMORY|0:125|9\n108|NODE_FAIL|1:0|2\n999|COMPLETED|0:0|1\n")
        ids = [str(i) for i in range(101, 109)]
        jobs = campaign.parse_sacct(ids, out)
        self.assertEqual(jobs["101"], {"result": "COMPLETED", "rc": 0, "runtime": 12.0,
                                       "note": ""})
        self.assertEqual((jobs["102"]["result"], jobs["102"]["rc"]), ("FAILED", 20))
        self.assertEqual(jobs["103"]["result"], "TIMEOUT")
        self.assertEqual(jobs["104"]["result"], "CANCELED")
        self.assertIsNone(jobs["105"]["result"])
        self.assertIsNone(jobs["106"]["result"])
        self.assertEqual((jobs["107"]["result"], jobs["107"]["rc"]), ("OUT_OF_MEMORY", 253))
        self.assertEqual(jobs["108"]["note"], "NODE_FAIL")
        self.assertNotIn("999", jobs)

    def test_classify_uses_the_same_rules_as_flux(self):
        comp = {"failures": {"UNSTABLE": {"exit_code": 20, "retries": 1},
                             "TIMEOUT": {"retries": 1}}}
        jobs = campaign.parse_sacct(["1", "2", "3", "4"], "1|FAILED|20:0|1\n2|TIMEOUT|0:0|1\n"
                                    "3|CANCELLED by 1|0:15|1\n4|COMPLETED|0:0|1\n")
        self.assertEqual(campaign.classify(comp, jobs["1"]), ("predicted", "UNSTABLE"))
        self.assertEqual(campaign.classify(comp, jobs["2"]), ("predicted", "TIMEOUT"))
        self.assertEqual(campaign.classify(comp, jobs["3"]), ("canceled", "CANCELED"))
        self.assertEqual(campaign.classify(comp, jobs["4"]), ("success", None))

    def test_mode_comes_from_slurm_environment(self):
        cfg = {"scheduler": "slurm"}
        cases = [({}, "login"), ({"SLURM_JOB_ID": "5"}, "allocation"),
                 ({"SLURM_JOB_ID": "5", "SLURM_STEP_ID": "4294967294"}, "allocation"),
                 ({"SLURM_JOB_ID": "5", "SLURM_STEP_ID": "4294967290"}, "allocation"),
                 ({"SLURM_JOB_ID": "5", "SLURM_STEP_ID": "0"}, None)]
        for env, expected in cases:
            clean = {k: v for k, v in os.environ.items() if not k.startswith("SLURM_")}
            with unittest.mock.patch.dict(os.environ, dict(clean, **env), clear=True):
                self.assertEqual(campaign.detect_mode(cfg), expected, env)

    def test_job_commands(self):
        cfg = {"scheduler": "slurm", "queue": "shared", "bank": "m1234", "name": "x",
               "run_dir": "/r", "config_path": "/c.json",
               "time": {"member": "90s", "analysis": "1m"},
               "allocation": {"nodes": 2, "slurm_args": ["-C", "cpu"]},
               "ensemble": {"size": 4, "command": ["a", "b c"],
                            "slurm_args": ["-C", "cpu", "-c", "2"]},
               "analysis": {"command": ["an"]}}
        sbatch = campaign.sbatch_args(cfg, "ensemble", "n", "/d", 90)
        self.assertEqual(sbatch[:3], ["sbatch", "--parsable", "--job-name=n"])
        self.assertIn("--time=0:02:00", sbatch)
        self.assertIn("--output=/d/stdout.log", sbatch)
        self.assertEqual(sbatch[sbatch.index("-q"):], ["-q", "shared", "-A", "m1234", "-C", "cpu",
                                                       "-c", "2", "--wrap", "a 'b c'"])
        srun = campaign.srun_args(cfg, "ensemble", "n", "/d", 90)
        self.assertEqual(srun[:4], ["srun", "--exact", "-N", "1"])
        self.assertNotIn("-q", srun)
        self.assertEqual(srun[-6:], ["-C", "cpu", "-c", "2", "a", "b c"])
        batch = campaign.batch_args(cfg)
        self.assertEqual(batch[:4], ["sbatch", "--parsable", "-N", "2"])
        self.assertIn("-A", batch)
        self.assertEqual(batch[-2], "--wrap")
        self.assertTrue(batch[-1].endswith("campaign.py run /c.json"))

    def test_validation(self):
        base = {"name": "t", "run_dir": "r", "scheduler": "slurm",
                "time": {"member": "1m", "analysis": "1m"},
                "allocation": {"slurm_args": ["-C", "cpu"]},
                "ensemble": {"size": 1, "command": ["a"], "slurm_args": ["-c", "2"]},
                "analysis": {"command": ["a"]}}
        self.assertEqual(campaign.validate(base), [])
        for bad, fragment in (({"scheduler": "pbs"}, '"flux" or "slurm"'),):
            self.assertIn(fragment, "\n".join(campaign.validate(dict(base, **bad))))
        for arg in ("-A", "--account=m1", "-t5", "--time=5", "--wrap", "--chdir=/x", "-J", "--exact"):
            cfg = json.loads(json.dumps(base))
            cfg["ensemble"]["slurm_args"] = [arg]
            self.assertIn("is set by campaign.py", "\n".join(campaign.validate(cfg)), arg)
        cfg = json.loads(json.dumps(base))
        cfg["allocation"]["slurm_args"] = ["-N", "2"]
        self.assertIn("allocation.slurm_args", "\n".join(campaign.validate(cfg)))


class FakeSlurmTests(unittest.TestCase):
    """Whole campaigns through campaign.py's Slurm code and the fake Slurm commands."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="slurm-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = {k: v for k, v in os.environ.items() if not k.startswith("SLURM_")}
        env.update(PATH=FAKE_BIN + os.pathsep + os.environ["PATH"],
                   FAKE_SLURM_DIR=os.path.join(self.tmp, "slurm"),
                   FAKE_SLURM_MINUTE=str(FAKE_MINUTE))
        patcher = unittest.mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(campaign.STEPS.clear)

    def config(self, size=2, cycles=1, ensemble=None, analysis=None, **top):
        raw = dict({"name": "t", "run_dir": "run", "cycles": cycles, "poll_seconds": 0.2,
                    "scheduler": "slurm", "queue": "shared", "bank": "m0000",
                    "time": {"member": "1m", "analysis": "1m"},
                    "ensemble": dict({"size": size, "command": COMPONENT,
                                      "slurm_args": ["-C", "cpu", "-c", "2"]}, **(ensemble or {})),
                    "analysis": dict({"command": COMPONENT}, **(analysis or {}))}, **top)
        path = os.path.join(self.tmp, "config.json")
        with open(path, "w") as handle:
            json.dump(raw, handle)
        self.cfg = campaign.load_config(path)
        return self.cfg

    def state(self):
        with open(os.path.join(self.cfg["run_dir"], "state.json")) as handle:
            return json.load(handle)

    def calls(self, name):
        with open(os.path.join(self.tmp, "slurm", "calls.log")) as handle:
            return [line.split() for line in handle if line.startswith(name + " ")]

    def test_login_mode_submits_each_job_with_sbatch(self):
        self.config(cycles=2)
        self.assertEqual(campaign.run(self.cfg, "login"), 0)
        state = self.state()
        self.assertEqual(len(state["cycles"]), 2)
        sbatches = self.calls("sbatch")
        self.assertEqual(len(sbatches), 2 * 3)  # two members and the analysis, twice
        for call in sbatches:
            self.assertIn("-A", call)
            self.assertEqual(call[call.index("-q") + 1], "shared")
        self.assertFalse(self.calls("srun"))
        self.assertTrue(state["cycles"][1]["members"][0]["job"].isdigit())

    def test_login_mode_failures_and_retries(self):
        self.config(ensemble={"retries": 1, "params": {"retry_params": '{"dt": 0.5}'},
                              "failures": {"UNSTABLE": {"exit_code": 20, "retries": 1}}})
        original = campaign.member_params
        scripts = iter([["exit:20", "crash", "ok"], ["ok"]])

        def params(cfg, state):
            return dict(original(cfg, state), script=next(scripts))
        with unittest.mock.patch.object(campaign, "member_params", params):
            self.assertEqual(campaign.run(self.cfg, "login"), 0)
        member = self.state()["cycles"][0]["members"][0]
        self.assertEqual([h["kind"] for h in member["history"]],
                         ["predicted", "unpredicted", "success"])
        self.assertEqual(member["history"][0]["reason"], "UNSTABLE")
        self.assertEqual(member["params"]["dt"], 0.5)

    def test_login_mode_timeout_scales_limit(self):
        self.config(size=1, ensemble={"params": {"script": [f"sleep:{3 * FAKE_MINUTE}", "ok"]},
                                      "failures": {"TIMEOUT": {"retries": 1, "time_scale": 2}}})
        self.assertEqual(campaign.run(self.cfg, "login"), 0)
        member = self.state()["cycles"][0]["members"][0]
        self.assertEqual(member["history"][0]["reason"], "TIMEOUT")
        self.assertEqual(member["time"], 120)

    def test_allocation_mode_runs_steps_with_srun(self):
        os.environ["FAKE_SLURM_TIMELEFT"] = "1:00:00"
        os.environ["SLURM_JOB_ID"] = "42"
        self.config(size=3, cycles=2)
        self.assertEqual(campaign.run(self.cfg, "allocation"), 0)
        steps = self.calls("srun")
        self.assertEqual(len(steps), 2 * 4)
        for call in steps:
            self.assertNotIn("-q", call)
            self.assertNotIn("-A", call)
            self.assertIn("--exact", call)
        self.assertFalse(self.calls("sbatch"))
        self.assertTrue(self.state()["cycles"][0]["members"][0]["job"].startswith("step-"))

    def test_allocation_mode_step_timeout_and_failure(self):
        os.environ["FAKE_SLURM_TIMELEFT"] = "1:00:00"
        os.environ["SLURM_JOB_ID"] = "42"
        self.config(size=2, ensemble={"retries": 1, "failures": {
            "TIMEOUT": {"retries": 1, "time_scale": 2}, "BAD": {"exit_code": 22, "retries": 0}}})
        original = campaign.member_params
        scripts = iter([[f"sleep:{3 * FAKE_MINUTE}", "ok"], ["exit:22"]])

        def params(cfg, state):
            return dict(original(cfg, state), script=next(scripts))
        with unittest.mock.patch.object(campaign, "member_params", params):
            self.assertEqual(campaign.run(self.cfg, "allocation"), 1)
        first, second = self.state()["cycles"][0]["members"]
        self.assertEqual(first["history"][0]["reason"], "TIMEOUT")
        self.assertEqual(first["status"], "DONE")
        self.assertEqual(second["history"][0]["reason"], "BAD")
        self.assertEqual(second["status"], "FAILED")

    def test_allocation_mode_pauses_when_time_runs_short(self):
        os.environ["FAKE_SLURM_TIMELEFT"] = "0:30"
        os.environ["SLURM_JOB_ID"] = "42"
        self.config(size=1)
        self.assertEqual(campaign.run(self.cfg, "allocation"), 3)
        self.assertEqual(self.state()["cycles"][0]["members"][0]["attempt"], 0)

    def test_steps_from_an_earlier_run_are_resubmitted(self):
        os.environ["FAKE_SLURM_TIMELEFT"] = "1:00:00"
        os.environ["SLURM_JOB_ID"] = "42"
        self.config(size=1)
        self.assertEqual(campaign.run(self.cfg, "allocation"), 0)
        path = os.path.join(self.cfg["run_dir"], "state.json")
        state = self.state()
        state["status"] = "paused"
        state["cycles"][0]["analysis"] = None
        state["cycles"][0]["members"][0].update(status="RUNNING", job="step-999999")
        with open(path, "w") as handle:
            json.dump(state, handle)
        campaign.STEPS.clear()
        self.assertEqual(campaign.run(self.cfg, "allocation"), 0)
        member = self.state()["cycles"][0]["members"][0]
        self.assertEqual(member["history"][-2]["kind"], "lost")
        self.assertEqual(member["unpredicted"], 0)

    def test_batch_runs_the_campaign_inside_an_allocation(self):
        self.config(size=2, cycles=2, allocation={"nodes": 1, "slurm_args": ["-C", "cpu"]})
        done = subprocess.run([sys.executable, os.path.join(ROOT, "campaign.py"), "batch",
                               self.cfg["config_path"]], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, encoding="utf-8")
        self.assertEqual(done.returncode, 0, done.stderr)
        deadline = time.time() + 60
        while time.time() < deadline:
            state_file = os.path.join(self.cfg["run_dir"], "state.json")
            if os.path.exists(state_file) and self.state()["status"] in ("completed", "failed"):
                break
            time.sleep(0.5)
        self.assertEqual(self.state()["status"], "completed")
        self.assertEqual(len(self.calls("sbatch")), 1)
        self.assertEqual(len(self.calls("srun")), 2 * 3)


if __name__ == "__main__":
    unittest.main()
