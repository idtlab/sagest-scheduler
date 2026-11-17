"""Records what a job ran on, as job.json in its attempt directory, for the run report
(scripts/common/job_report.py). Works under Slurm, Flux or neither; fields the scheduler
doesn't provide are left out."""
import json
import os
import socket
import subprocess
import time

SLURM = {"job_id": "SLURM_JOB_ID", "step_id": "SLURM_STEP_ID", "account": "SLURM_JOB_ACCOUNT",
         "qos": "SLURM_JOB_QOS", "partition": "SLURM_JOB_PARTITION",
         "tasks": "SLURM_NTASKS", "cpus_per_task": "SLURM_CPUS_PER_TASK",
         "mem_per_cpu": "SLURM_MEM_PER_CPU", "mem_per_node": "SLURM_MEM_PER_NODE"}
FLUX = {"job_id": "FLUX_JOB_ID", "tasks": "FLUX_JOB_SIZE", "nodes": "FLUX_JOB_NNODES"}


def gpus():
    """Names of the GPUs this job can see, or [] without nvidia-smi."""
    try:
        done = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total",
                               "--format=csv,noheader"], encoding="utf-8", timeout=20,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [line.strip() for line in done.stdout.splitlines() if line.strip()] \
        if done.returncode == 0 else []


class JobInfo:
    def __init__(self, attempt_dir):
        self.path = os.path.join(attempt_dir, "job.json")
        self.started = time.time()
        env = os.environ
        info = {"host": socket.gethostname(), "start": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "cpus_usable": len(os.sched_getaffinity(0))}
        if env.get("SLURM_JOB_ID"):
            info["scheduler"] = "slurm"
            info.update({key: env[var] for key, var in SLURM.items() if env.get(var)})
            # Inside an srun step the step's own node count and list apply.
            info["nodes"] = env.get("SLURM_STEP_NUM_NODES") or env.get("SLURM_JOB_NUM_NODES")
            info["nodelist"] = env.get("SLURM_STEP_NODELIST") or env.get("SLURM_JOB_NODELIST")
        elif env.get("FLUX_JOB_ID"):
            info["scheduler"] = "flux"
            info.update({key: env[var] for key, var in FLUX.items() if env.get(var)})
        if env.get("CUDA_VISIBLE_DEVICES"):
            info["cuda_visible_devices"] = env["CUDA_VISIBLE_DEVICES"]
        info["gpus"] = gpus()
        info["seconds"] = {}
        self.info = {key: value for key, value in info.items() if value is not None}
        self.save()

    def time(self, phase, started):
        self.info["seconds"][phase] = round(time.time() - started, 2)
        self.save()

    def save(self):
        self.info["seconds"].pop("total", None)
        self.info["seconds"]["total"] = round(time.time() - self.started, 2)
        with open(self.path, "w") as handle:
            json.dump(self.info, handle, indent=1)

    def summary(self):
        info = self.info
        parts = [f"host {info['host']}", f"{info['cpus_usable']} cpus"]
        if "nodes" in info:
            parts.insert(0, f"{info['nodes']} node(s) {info.get('nodelist', '')}".strip())
        if info["gpus"]:
            parts.append(f"gpus {'; '.join(info['gpus'])}")
        if "job_id" in info:
            parts.insert(0, f"{info['scheduler']} job {info['job_id']}"
                         + (f".{info['step_id']}" if "step_id" in info else ""))
        return ", ".join(parts)
