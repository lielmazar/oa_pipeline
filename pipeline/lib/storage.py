"""Uniform file access over S3 URIs (s3://bucket/key) and local paths.

Every stage reads and writes through these helpers, so the same code runs
against your S3 bucket on EC2 or against a local directory in tests.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

_clients: dict = {}


@dataclass(frozen=True)
class FileInfo:
    uri: str
    size: int


def is_s3(uri: str) -> bool:
    return uri.startswith("s3://")


def split_s3(uri: str) -> tuple[str, str]:
    bucket, _, key = uri[len("s3://"):].partition("/")
    return bucket, key


def join(base: str, *parts: str) -> str:
    parts = tuple(p.strip("/") for p in parts if p)
    if is_s3(base):
        return "/".join([base.rstrip("/"), *parts])
    return os.path.join(base, *parts)


def relative(uri: str, base: str) -> str:
    """Path of uri relative to base, always with forward slashes."""
    rel = uri[len(base.rstrip("/")):].lstrip("/") if is_s3(base) else os.path.relpath(uri, base)
    return rel.replace("\\", "/")


# ---------------------------------------------------------------------------
# S3 client
# ---------------------------------------------------------------------------

def _has_credentials() -> bool:
    if "creds" not in _clients:
        import boto3
        _clients["creds"] = boto3.Session().get_credentials() is not None
    return _clients["creds"]


def s3_client(signed: bool | None = None):
    """Per-process boto3 client. Falls back to anonymous access when no
    credentials are configured (enough to read the public OpenAlex bucket)."""
    import boto3
    from botocore import UNSIGNED
    from botocore.config import Config

    if signed is None:
        signed = _has_credentials()
    key = ("client", signed, os.getpid())
    if key not in _clients:
        cfg = Config(retries={"max_attempts": 10, "mode": "adaptive"}, max_pool_connections=64)
        if not signed:
            cfg = cfg.merge(Config(signature_version=UNSIGNED))
        _clients[key] = boto3.client("s3", config=cfg)
    return _clients[key]


def _transfer_config():
    from boto3.s3.transfer import TransferConfig
    return TransferConfig(multipart_threshold=64 * 1024 ** 2,
                          multipart_chunksize=64 * 1024 ** 2, max_concurrency=8)


# ---------------------------------------------------------------------------
# Listing and metadata
# ---------------------------------------------------------------------------

def list_files(uri: str, suffix: str | None = None) -> list[FileInfo]:
    """All files under a prefix/directory, sorted by URI."""
    out: list[FileInfo] = []
    if is_s3(uri):
        bucket, prefix = split_s3(uri)
        prefix = prefix.rstrip("/") + "/" if prefix else ""
        paginator = s3_client().get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                if suffix is None or obj["Key"].endswith(suffix):
                    out.append(FileInfo(f"s3://{bucket}/{obj['Key']}", obj["Size"]))
    else:
        root = Path(uri)
        if root.exists():
            for p in root.rglob("*"):
                if p.is_file() and (suffix is None or p.name.endswith(suffix)):
                    out.append(FileInfo(str(p), p.stat().st_size))
    return sorted(out, key=lambda f: f.uri)


def size(uri: str) -> int | None:
    """File size in bytes, or None when it does not exist."""
    if is_s3(uri):
        from botocore.exceptions import ClientError
        bucket, key = split_s3(uri)
        try:
            return s3_client().head_object(Bucket=bucket, Key=key)["ContentLength"]
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
    p = Path(uri)
    return p.stat().st_size if p.is_file() else None


def exists(uri: str) -> bool:
    return size(uri) is not None


# ---------------------------------------------------------------------------
# Reading and writing
# ---------------------------------------------------------------------------

def read_bytes(uri: str) -> bytes:
    if is_s3(uri):
        bucket, key = split_s3(uri)
        return s3_client().get_object(Bucket=bucket, Key=key)["Body"].read()
    return Path(uri).read_bytes()


def write_bytes(uri: str, data: bytes, content_type: str | None = None) -> None:
    if is_s3(uri):
        bucket, key = split_s3(uri)
        extra = {"ContentType": content_type} if content_type else {}
        s3_client().put_object(Bucket=bucket, Key=key, Body=data, **extra)
    else:
        p = Path(uri)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)


def read_json(uri: str):
    return json.loads(read_bytes(uri))


def write_json(uri: str, obj) -> None:
    write_bytes(uri, json.dumps(obj, indent=2, ensure_ascii=False, default=str).encode("utf-8"),
                "application/json")


def write_text(uri: str, text: str, content_type: str = "text/plain; charset=utf-8") -> None:
    write_bytes(uri, text.encode("utf-8"), content_type)


def download(uri: str, local_path: Path) -> Path:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    if is_s3(uri):
        bucket, key = split_s3(uri)
        s3_client().download_file(bucket, key, str(local_path), Config=_transfer_config())
    else:
        shutil.copyfile(uri, local_path)
    return local_path


def upload(local_path: Path, uri: str) -> None:
    if is_s3(uri):
        bucket, key = split_s3(uri)
        s3_client().upload_file(str(local_path), bucket, key, Config=_transfer_config())
    else:
        dest = Path(uri)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local_path, dest)


def copy(src: str, dst: str) -> None:
    """Copy one file between any combination of S3 and local locations.
    S3-to-S3 copies run server-side (no data passes through this machine)."""
    if is_s3(src) and is_s3(dst):
        sb, sk = split_s3(src)
        db, dk = split_s3(dst)
        s3_client(signed=True).copy({"Bucket": sb, "Key": sk}, db, dk, Config=_transfer_config())
    elif is_s3(src):
        download(src, Path(dst))
    else:
        upload(Path(src), dst)


def delete_prefix(uri: str) -> int:
    """Delete everything under a prefix/directory. Returns number of files removed."""
    files = list_files(uri)
    if is_s3(uri):
        bucket, _ = split_s3(uri)
        client = s3_client()
        for i in range(0, len(files), 1000):
            chunk = [{"Key": split_s3(f.uri)[1]} for f in files[i:i + 1000]]
            client.delete_objects(Bucket=bucket, Delete={"Objects": chunk})
    elif Path(uri).exists():
        shutil.rmtree(uri)
    return len(files)
