"""deploy/remote.py (local launcher) and deploy/job_agent.py (runs on the instance)."""
from __future__ import annotations

import json
import subprocess
import sys
import tarfile
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "deploy"))

import job_agent  # noqa: E402
import remote  # noqa: E402

from pipeline.lib import storage  # noqa: E402


def test_load_env_parses_expands_and_lets_environment_win(tmp_path):
    a = tmp_path / "a.env"
    a.write_text("# comment\nOA_BUCKET=bucket-a\nOA_STORAGE_ROOT=s3://${OA_BUCKET}/openalex\n"
                 "EC2_KEY_NAME=\nQUOTED=\"x y\"\n")
    b = tmp_path / "b.env"
    b.write_text("OA_BUCKET=bucket-b\n")
    env = remote.load_env([a, b, tmp_path / "missing.env"], environ={"EC2_KEY_NAME": "mykey", "OTHER": "1"})
    assert env["OA_BUCKET"] == "bucket-b"
    assert env["OA_STORAGE_ROOT"] == "s3://bucket-b/openalex"
    assert env["EC2_KEY_NAME"] == "mykey" and env["QUOTED"] == "x y" and "OTHER" not in env


def test_repo_env_file_is_complete():
    s = remote.Settings.from_env(remote.load_env([REPO / "deploy" / "aws.env"], environ={}))
    assert s.region == "us-east-1" and s.storage_root == f"s3://{s.bucket}/openalex"
    assert s.auto_terminate and not s.spot and s.instance_profile == "oa-pipeline-ec2"


def _settings(**kw) -> remote.Settings:
    env = remote.load_env([REPO / "deploy" / "aws.env"], environ={})
    env.update({"OA_BUCKET": "mine", "OA_STORAGE_ROOT": "s3://mine/oa"})
    s = remote.Settings.from_env(env)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def test_user_data_is_valid_bash_and_carries_the_job(tmp_path):
    s = _settings()
    ud = remote.user_data(s, "20260101-000000-run-all", ["python", "-m", "pipeline.run_all", "--smoke"], "smoke")
    script = tmp_path / "ud.sh"
    script.write_text(ud)
    subprocess.run(["bash", "-n", str(script)], check=True)
    assert "'mine', 'oa/deploy/jobs/20260101-000000-run-all/code.tar.gz'" in ud
    assert "export OA_RUN_ID=smoke" in ud and "export OA_STORAGE_ROOT=s3://mine/oa" in ud
    assert "-- python -m pipeline.run_all --smoke" in ud
    assert "shutdown -h now" in ud and "shutdown -h +720" in ud
    assert "shutdown -h now" not in remote.user_data(_settings(auto_terminate=False), "j", ["true"], None)
    assert len(ud.encode()) < 16_000   # EC2 user-data limit


def test_bundle_contains_tracked_files(tmp_path):
    meta = remote.build_bundle(tmp_path / "b.tar.gz")
    names = tarfile.open(tmp_path / "b.tar.gz").getnames()
    assert "app/README.md" in names and "app/pyproject.toml" in names
    assert not any(n.startswith("app/.venv") for n in names)
    assert meta["files"] == len(names) and len(meta["git_commit"]) == 40


def test_job_agent_streams_log_and_status(tmp_path):
    root = str(tmp_path / "job")
    code = job_agent.main(["--job-root", root, "--log-file", str(tmp_path / "j.log"), "--",
                           sys.executable, "-c", "print('hello from the job')"])
    assert code == 0
    assert "hello from the job" in Path(root, "job.log").read_text()
    status = json.loads(Path(root, "status.json").read_text())
    assert status["state"] == "succeeded" and status["exit_code"] == 0

    code = job_agent.main(["--job-root", root, "--log-file", str(tmp_path / "j.log"), "--",
                           sys.executable, "-c", "import sys; sys.exit(3)"])
    assert code == 3 and json.loads(Path(root, "status.json").read_text())["state"] == "failed"


@pytest.fixture
def aws_mock(monkeypatch):
    for k, v in {"AWS_ACCESS_KEY_ID": "t", "AWS_SECRET_ACCESS_KEY": "t", "AWS_DEFAULT_REGION": "us-east-1"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with mock_aws():
        storage._clients.clear()
        boto3.client("s3").create_bucket(Bucket="mine")
        iam = boto3.client("iam")
        iam.create_role(RoleName="oa-pipeline-ec2", AssumeRolePolicyDocument="{}")
        iam.create_instance_profile(InstanceProfileName="oa-pipeline-ec2")
        iam.add_role_to_instance_profile(InstanceProfileName="oa-pipeline-ec2", RoleName="oa-pipeline-ec2")
        ami = boto3.client("ec2").describe_images(Owners=["amazon"])["Images"][0]["ImageId"]
        monkeypatch.setattr(remote, "_ami", lambda s: ami)
        yield
    storage._clients.clear()


def test_launch_status_list_terminate(aws_mock, capsys):
    s = _settings()
    assert remote.cmd_check(s) == 0
    assert remote.cmd_launch(s, ["python", "-m", "pipeline.run_all", "--smoke"], "smoke") == 0
    job_id = remote._jobs(s)[-1]
    assert job_id.endswith("-run-all")
    job = storage.read_json(f"s3://mine/oa/deploy/jobs/{job_id}/job.json")
    assert job["run_id"] == "smoke" and job["instance_id"].startswith("i-")
    assert storage.exists(f"s3://mine/oa/deploy/jobs/{job_id}/code.tar.gz")
    tags = boto3.client("ec2").describe_instances(InstanceIds=[job["instance_id"]])[
        "Reservations"][0]["Instances"][0]["Tags"]
    assert {"Key": "oa-pipeline-job", "Value": job_id} in tags

    # Simulate the instance's agent writing progress.
    storage.write_text(f"s3://mine/oa/deploy/jobs/{job_id}/job.log", "stage 2 progress 3/10\n")
    storage.write_json(f"s3://mine/oa/deploy/jobs/{job_id}/status.json", {"state": "succeeded", "exit_code": 0})
    capsys.readouterr()
    remote.cmd_status(s, None)
    out = capsys.readouterr().out
    assert job_id in out and "succeeded" in out and "stage 2 progress 3/10" in out
    assert remote.cmd_logs(s, job_id[:15], follow=True) == 0
    remote.cmd_list(s)
    assert job_id in capsys.readouterr().out
    remote.cmd_terminate(s, None)
    state = boto3.client("ec2").describe_instances(InstanceIds=[job["instance_id"]])[
        "Reservations"][0]["Instances"][0]["State"]["Name"]
    assert state in ("shutting-down", "terminated")
