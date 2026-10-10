"""Run pipeline jobs on EC2 from your own machine — no login to the instance.

`launch` packages the code in your working copy, uploads it to your bucket,
and starts a fresh EC2 instance whose start-up script installs everything,
runs your command, streams the log to S3, and terminates the instance when
done. The instance needs no SSH key, no open port and no GitHub access.

    python deploy/remote.py check                                  # verify AWS setup
    python deploy/remote.py launch -- python -m pipeline.run_all --smoke --run-id smoke
    python deploy/remote.py launch --instance-type c6id.8xlarge -- python -m pipeline.run_all
    python deploy/remote.py logs --follow                          # latest job
    python deploy/remote.py status [JOB_ID]
    python deploy/remote.py list
    python deploy/remote.py terminate [JOB_ID | i-…]

Settings come from deploy/aws.env, then deploy/aws.local.env, then the environment.
"""
from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENV_FILES = [REPO / "deploy" / "aws.env", REPO / "deploy" / "aws.local.env"]
FINAL_STATES = {"succeeded", "failed", "interrupted"}
GONE = {"terminated", "stopped", "shutting-down", "stopping"}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def load_env(paths: list[Path], environ: dict | None = None) -> dict[str, str]:
    """KEY=VALUE files (comments with #, optional quotes, ${VAR} expansion).
    Later files override earlier ones; real environment variables override all."""
    environ = dict(os.environ if environ is None else environ)
    values: dict[str, str] = {}
    for path in paths:
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            if raw.lstrip().startswith("#") or not (m := _LINE.match(raw)):
                continue
            key, val = m.groups()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
                val = val[1:-1]
            values[key] = val
    values.update({k: v for k, v in environ.items() if k in values})
    for _ in range(5):   # resolve ${VAR} references (a few levels deep)
        values = {k: re.sub(r"\$\{(\w+)\}", lambda m: values.get(m.group(1), environ.get(m.group(1), "")), v)
                  for k, v in values.items()}
    return values


def _bool(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    profile: str
    region: str
    bucket: str
    storage_root: str
    run_id: str
    instance_profile: str
    instance_type: str
    spot: bool
    volume_gb: int
    auto_terminate: bool
    max_hours: float
    ami_parameter: str
    subnet_id: str
    security_group_id: str
    key_name: str
    name_tag: str

    @classmethod
    def from_env(cls, env: dict[str, str]) -> "Settings":
        def need(k: str) -> str:
            if not env.get(k):
                raise SystemExit(f"{k} is not set — edit deploy/aws.env")
            return env[k]
        return cls(
            profile=env.get("AWS_PROFILE", ""), region=need("AWS_REGION"), bucket=need("OA_BUCKET"),
            storage_root=need("OA_STORAGE_ROOT").rstrip("/"), run_id=env.get("OA_RUN_ID", ""),
            instance_profile=need("EC2_INSTANCE_PROFILE"), instance_type=need("EC2_INSTANCE_TYPE"),
            spot=_bool(env.get("EC2_SPOT", "false")), volume_gb=int(env.get("EC2_ROOT_VOLUME_GB", "30")),
            auto_terminate=_bool(env.get("EC2_AUTO_TERMINATE", "true")),
            max_hours=float(env.get("EC2_MAX_HOURS", "12")), ami_parameter=need("EC2_AMI_PARAMETER"),
            subnet_id=env.get("EC2_SUBNET_ID", ""), security_group_id=env.get("EC2_SECURITY_GROUP_ID", ""),
            key_name=env.get("EC2_KEY_NAME", ""), name_tag=env.get("EC2_NAME_TAG", "oa-pipeline"))

    @property
    def jobs_root(self) -> str:
        return f"{self.storage_root}/deploy/jobs"

    def job_root(self, job_id: str) -> str:
        return f"{self.jobs_root}/{job_id}"

    def apply(self) -> None:
        """Make boto3 (also inside pipeline.lib.storage) use these credentials and region."""
        if self.profile:
            os.environ["AWS_PROFILE"] = self.profile
        os.environ["AWS_DEFAULT_REGION"] = self.region


def aws(s: Settings, service: str):
    import boto3
    return boto3.Session(profile_name=s.profile or None, region_name=s.region).client(service)


# ---------------------------------------------------------------------------
# Code bundle and start-up script
# ---------------------------------------------------------------------------

def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()


def build_bundle(dest: Path) -> dict:
    """tar.gz of all git-tracked files as they are in your working copy (uncommitted edits included)."""
    files = [f for f in _git("ls-files", "-z").split("\0") if f and (REPO / f).is_file()]
    with tarfile.open(dest, "w:gz") as tar:
        for f in files:
            tar.add(REPO / f, arcname=f"app/{f}")
    return {"git_commit": _git("rev-parse", "HEAD"), "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
            "uncommitted_changes": bool(_git("status", "--porcelain", "--untracked-files=no")),
            "files": len(files), "bytes": dest.stat().st_size}


def user_data(s: Settings, job_id: str, command: list[str], run_id: str | None) -> str:
    """Bash start-up script for the instance (runs as root on first boot)."""
    from pipeline.lib import storage
    bucket, key = storage.split_s3(f"{s.job_root(job_id)}/code.tar.gz")
    env = {"OA_STORAGE_ROOT": s.storage_root, "OA_TMP_DIR": "/mnt/scratch",
           "AWS_DEFAULT_REGION": s.region, "PYTHONUNBUFFERED": "1"}
    if run_id or s.run_id:
        env["OA_RUN_ID"] = run_id or s.run_id
    exports = "\n".join(f"export {k}={shlex.quote(v)}" for k, v in env.items())
    on_exit = "shutdown -h now" if s.auto_terminate else "echo 'EC2_AUTO_TERMINATE=false: instance kept running'"
    return f"""#!/bin/bash
# oa-pipeline job {job_id}
set -euxo pipefail
finish() {{ echo "oa-pipeline start-up script exited with code $?"; {on_exit}; }}
trap finish EXIT
shutdown -h +{int(s.max_hours * 60)} "oa-pipeline EC2_MAX_HOURS reached" || true

export DEBIAN_FRONTEND=noninteractive
# Wait for first-boot unattended-upgrades instead of failing on the dpkg lock.
apt-get -o DPkg::Lock::Timeout=600 update -y
apt-get -o DPkg::Lock::Timeout=600 install -y python3-venv python3-pip

# Scratch space: instance-store NVMe when the instance type has one.
mkdir -p /mnt/scratch
DEV=$(lsblk -dpno NAME,MODEL | awk '/Instance Storage/ {{print $1; exit}}')
if [ -n "${{DEV:-}}" ]; then mkfs.ext4 -F "$DEV" && mount "$DEV" /mnt/scratch; fi

mkdir -p /opt/oa && cd /opt/oa
python3 -m venv venv
venv/bin/pip install -q --upgrade pip boto3
venv/bin/python - <<'PY'
import boto3, tarfile
boto3.client("s3", region_name={s.region!r}).download_file({bucket!r}, {key!r}, "/opt/oa/code.tar.gz")
tarfile.open("/opt/oa/code.tar.gz").extractall("/opt/oa", filter="data")
PY
cd /opt/oa/app
/opt/oa/venv/bin/pip install -q -e .
/opt/oa/venv/bin/python -c "import duckdb; c = duckdb.connect(); c.execute('INSTALL httpfs; INSTALL aws')"

{exports}
/opt/oa/venv/bin/python deploy/job_agent.py --job-root {shlex.quote(s.job_root(job_id))} -- {shlex.join(command)}
"""


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _ok(msg: str) -> None:
    print(f"  OK    {msg}")


def _fail(msg: str, hint: str) -> None:
    print(f"  FAIL  {msg}\n        → {hint}")


def cmd_check(s: Settings) -> int:
    from botocore.exceptions import ClientError
    failures = 0
    print(f"region={s.region} profile={s.profile or '(default)'} bucket={s.bucket} "
          f"instance_profile={s.instance_profile}\n")
    try:
        ident = aws(s, "sts").get_caller_identity()
        _ok(f"credentials: {ident['Arn']}")
    except Exception as e:  # noqa: BLE001
        _fail(f"credentials: {e}", "run `aws configure` (AWS_PROFILE in deploy/aws.env)")
        return 1
    checks = [
        ("bucket " + s.bucket, lambda: aws(s, "s3").head_bucket(Bucket=s.bucket),
         "OA_BUCKET must match S3 → Buckets, and oa-pipeline-s3 must be attached to your user"),
        ("instance profile " + s.instance_profile,
         lambda: aws(s, "iam").get_instance_profile(InstanceProfileName=s.instance_profile),
         "EC2_INSTANCE_PROFILE must match IAM → Roles (the EC2 role); attach oa-pipeline-operator to your user"),
        ("Ubuntu AMI lookup", lambda: _ami(s),
         "attach oa-pipeline-operator (ssm:GetParameter) to your user"),
    ]
    for label, fn, hint in checks:
        try:
            fn()
            _ok(label)
        except Exception as e:  # noqa: BLE001
            failures += 1
            _fail(f"{label}: {e}", hint)
    try:
        aws(s, "ec2").run_instances(**_launch_params(s, "dry-run", ["true"], None), DryRun=True)
    except ClientError as e:
        if e.response["Error"]["Code"] == "DryRunOperation":
            _ok(f"permission to launch {s.instance_type} with role {s.instance_profile} (dry run)")
        else:
            failures += 1
            _fail(f"launch dry run: {e}", "attach oa-pipeline-operator to your user; check "
                  "EC2_SUBNET_ID / EC2_SECURITY_GROUP_ID / EC2_KEY_NAME if set")
    except Exception as e:  # noqa: BLE001
        failures += 1
        _fail(f"launch dry run: {e}", "see message")
    print("\nAll checks passed." if not failures else f"\n{failures} check(s) failed.")
    return 1 if failures else 0


def _ami(s: Settings) -> str:
    return aws(s, "ssm").get_parameter(Name=s.ami_parameter)["Parameter"]["Value"]


def _launch_params(s: Settings, job_id: str, command: list[str], run_id: str | None) -> dict:
    params = {
        "ImageId": _ami(s), "InstanceType": s.instance_type, "MinCount": 1, "MaxCount": 1,
        "UserData": user_data(s, job_id, command, run_id),
        "IamInstanceProfile": {"Name": s.instance_profile},
        "InstanceInitiatedShutdownBehavior": "terminate" if (s.auto_terminate or s.spot) else "stop",
        "BlockDeviceMappings": [{"DeviceName": "/dev/sda1", "Ebs": {
            "VolumeSize": s.volume_gb, "VolumeType": "gp3", "DeleteOnTermination": True}}],
        "MetadataOptions": {"HttpTokens": "required"},
        "TagSpecifications": [{"ResourceType": "instance", "Tags": [
            {"Key": "Name", "Value": f"{s.name_tag}-{job_id}"}, {"Key": "oa-pipeline-job", "Value": job_id}]}],
    }
    if s.subnet_id:
        params["SubnetId"] = s.subnet_id
    if s.security_group_id:
        params["SecurityGroupIds"] = [s.security_group_id]
    if s.key_name:
        params["KeyName"] = s.key_name
    if s.spot:
        params["InstanceMarketOptions"] = {"MarketType": "spot", "SpotOptions": {
            "SpotInstanceType": "one-time", "InstanceInterruptionBehavior": "terminate"}}
    return params


def _job_id(command: list[str]) -> str:
    words = [w for w in command if not w.startswith("-") and w not in ("python", "python3", "-m")]
    slug = re.sub(r"[^a-z0-9]+", "-", (words[0].split(".")[-1] if words else "job").lower()).strip("-")
    return f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{slug or 'job'}"


def cmd_launch(s: Settings, command: list[str], run_id: str | None) -> int:
    from pipeline.lib import storage
    if not command:
        raise SystemExit("Give the command to run after --, e.g.  launch -- python -m pipeline.run_all --smoke")
    job_id = _job_id(command)
    root = s.job_root(job_id)
    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp) / "code.tar.gz"
        meta = build_bundle(bundle)
        storage.upload(bundle, f"{root}/code.tar.gz")
    if meta["uncommitted_changes"]:
        print("note: bundle includes uncommitted changes from your working copy")
    params = _launch_params(s, job_id, command, run_id)
    resp = aws(s, "ec2").run_instances(**params)
    instance_id = resp["Instances"][0]["InstanceId"]
    job = {"job_id": job_id, "instance_id": instance_id, "command": command,
           "run_id": run_id or s.run_id, "instance_type": s.instance_type, "spot": s.spot,
           "auto_terminate": s.auto_terminate, "region": s.region,
           "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **meta}
    storage.write_json(f"{root}/job.json", job)
    print(f"Launched job {job_id} on {instance_id} ({s.instance_type}{', spot' if s.spot else ''}).")
    print(f"Start-up takes ~3-5 minutes before the command begins. Follow it with:\n"
          f"  python deploy/remote.py logs {job_id} --follow")
    return 0


def _jobs(s: Settings) -> list[str]:
    from pipeline.lib import storage
    names = {storage.relative(f.uri, s.jobs_root).split("/")[0]
             for f in storage.list_files(s.jobs_root, suffix="job.json")}
    return sorted(names)


def _resolve(s: Settings, job_id: str | None) -> str:
    jobs = _jobs(s)
    if not jobs:
        raise SystemExit("No jobs yet — start one with: python deploy/remote.py launch -- <command>")
    if not job_id:
        return jobs[-1]
    matches = [j for j in jobs if j.startswith(job_id) or job_id in j]
    if len(matches) != 1:
        raise SystemExit(f"Job {job_id!r} matches {len(matches)} jobs: {matches[:10]}")
    return matches[0]


def _read(uri: str):
    from pipeline.lib import storage
    return storage.read_json(uri) if storage.exists(uri) else None


def _instance_state(s: Settings, instance_id: str) -> str:
    try:
        r = aws(s, "ec2").describe_instances(InstanceIds=[instance_id])
        return r["Reservations"][0]["Instances"][0]["State"]["Name"]
    except Exception:  # noqa: BLE001 — terminated instances eventually disappear
        return "unknown (probably terminated)"


def _console_tail(s: Settings, instance_id: str, n: int) -> str:
    try:
        out = aws(s, "ec2").get_console_output(InstanceId=instance_id, Latest=True).get("Output") or ""
    except Exception as e:  # noqa: BLE001
        return f"(console output unavailable: {e})"
    return "\n".join(out.splitlines()[-n:]) or "(no console output yet — the instance is still booting)"


def cmd_status(s: Settings, job_id: str | None, lines: int = 25) -> int:
    from pipeline.lib import storage
    job_id = _resolve(s, job_id)
    root = s.job_root(job_id)
    job = _read(f"{root}/job.json")
    status = _read(f"{root}/status.json") or {"state": "starting (installing on the instance)"}
    state = _instance_state(s, job["instance_id"])
    print(f"job       {job_id}\ncommand   {' '.join(job['command'])}\n"
          f"instance  {job['instance_id']} ({job['instance_type']}) — {state}\n"
          f"job state {status['state']}" + (f", exit code {status['exit_code']}" if "exit_code" in status else ""))
    if status["state"] == "running" and state in GONE:
        print("          the instance stopped while the job was running (spot reclaim or EC2_MAX_HOURS).\n"
              "          Launch the same command again: finished partitions are skipped.")
    log_uri = f"{root}/job.log"
    print("\n--- last lines of job.log ---" if storage.exists(log_uri) else "\n--- instance console (start-up) ---")
    print("\n".join(storage.read_bytes(log_uri).decode().splitlines()[-lines:]) if storage.exists(log_uri)
          else _console_tail(s, job["instance_id"], lines))
    return 0


def cmd_logs(s: Settings, job_id: str | None, follow: bool) -> int:
    from pipeline.lib import storage
    job_id = _resolve(s, job_id)
    root = s.job_root(job_id)
    job = _read(f"{root}/job.json")
    printed = 0
    waiting_note = False
    while True:
        if storage.exists(f"{root}/job.log"):
            data = storage.read_bytes(f"{root}/job.log").decode(errors="replace")
            sys.stdout.write(data[printed:])
            sys.stdout.flush()
            printed = len(data)
        elif not waiting_note:
            print(f"(no log yet — instance {job['instance_id']} is still installing; "
                  f"`remote.py status` shows its console)")
            waiting_note = True
        status = _read(f"{root}/status.json") or {}
        if not follow:
            return 0
        if status.get("state") in FINAL_STATES:
            print(f"\n[job {status['state']}" + (f", exit code {status['exit_code']}]" if "exit_code" in status else "]"))
            return 0 if status.get("state") == "succeeded" else 1
        if _instance_state(s, job["instance_id"]) in GONE:
            print("\n[instance is gone before the job finished — see `remote.py status`]")
            return 1
        time.sleep(20)


def cmd_list(s: Settings) -> int:
    jobs = _jobs(s)[-20:]
    if not jobs:
        print("No jobs yet.")
        return 0
    print(f"{'JOB':<40} {'STATE':<12} {'INSTANCE':<21} {'EC2':<12} COMMAND")
    for j in jobs:
        root = s.job_root(j)
        job = _read(f"{root}/job.json") or {}
        st = (_read(f"{root}/status.json") or {}).get("state", "starting")
        print(f"{j:<40} {st:<12} {job.get('instance_id', '?'):<21} "
              f"{_instance_state(s, job['instance_id']) if 'instance_id' in job else '?':<12} "
              f"{' '.join(job.get('command', []))}")
    return 0


def cmd_terminate(s: Settings, target: str | None) -> int:
    if target and target.startswith("i-"):
        instance_id = target
    else:
        job = _read(f"{s.job_root(_resolve(s, target))}/job.json")
        instance_id = job["instance_id"]
    aws(s, "ec2").terminate_instances(InstanceIds=[instance_id])
    print(f"Terminating {instance_id}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command: list[str] = []
    if "--" in argv:
        i = argv.index("--")
        argv, command = argv[:i], argv[i + 1:]
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="Verify credentials, bucket, role and launch permission")
    la = sub.add_parser("launch", help="Start an instance that runs the command after --")
    la.add_argument("--instance-type", help="Override EC2_INSTANCE_TYPE")
    la.add_argument("--spot", action="store_true", help="Use Spot pricing")
    la.add_argument("--keep", action="store_true", help="Don't terminate the instance when the job ends")
    la.add_argument("--run-id", help="Run name passed to the pipeline (OA_RUN_ID)")
    st = sub.add_parser("status", help="Job state, instance state and the last log lines")
    st.add_argument("job", nargs="?")
    st.add_argument("-n", type=int, default=25)
    lg = sub.add_parser("logs", help="Print a job's log")
    lg.add_argument("job", nargs="?")
    lg.add_argument("--follow", "-f", action="store_true")
    sub.add_parser("list", help="Recent jobs")
    te = sub.add_parser("terminate", help="Terminate a job's instance")
    te.add_argument("target", nargs="?", help="Job id (default: latest) or instance id")
    args = p.parse_args(argv)

    s = Settings.from_env(load_env(ENV_FILES))
    if args.cmd == "launch":
        s.instance_type = args.instance_type or s.instance_type
        s.spot = s.spot or args.spot
        s.auto_terminate = s.auto_terminate and not args.keep
    s.apply()
    sys.path.insert(0, str(REPO))
    if args.cmd == "check":
        return cmd_check(s)
    if args.cmd == "launch":
        return cmd_launch(s, command, args.run_id)
    if args.cmd == "status":
        return cmd_status(s, args.job, args.n)
    if args.cmd == "logs":
        return cmd_logs(s, args.job, args.follow)
    if args.cmd == "list":
        return cmd_list(s)
    return cmd_terminate(s, args.target)


if __name__ == "__main__":
    sys.exit(main())
