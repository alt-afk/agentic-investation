"""
logger/s3.py
============
One responsibility: gzip-compress a dict and upload it to S3 atomically.

S3 layout
---------
  s3://{AGENT_LOGS_BUCKET}/{casebook_id}/execution-{execution_id}.json.gz

Environment
-----------
  AGENT_LOGS_BUCKET   – S3 bucket name (default: "agent-logs")
  AWS_REGION          – optional, boto3 respects standard AWS env vars
"""

from __future__ import annotations

import gzip
import json
import os

import boto3

_BUCKET = os.environ.get("AGENT_LOGS_BUCKET", "agent-logs")

# Lazy singleton — created on first upload, not at import time.
_client = None


def _s3():
    global _client
    if _client is None:
        _client = boto3.client("s3")
    return _client


def upload_artifact(casebook_id: str, execution_id: str, data: dict) -> str:
    """
    Serialize `data` → JSON → gzip → PUT to S3.
    Returns the s3:// URI of the written object.

    compresslevel=6 is the standard balanced setting (speed vs size).
    Content-Encoding: gzip lets HTTP clients decompress transparently
    if the object is ever served via a signed URL.
    """
    key  = f"{casebook_id}/execution-{execution_id}.json.gz"
    body = gzip.compress(
        json.dumps(data, default=str, indent=2).encode("utf-8"),
        compresslevel=6,
    )

    _s3().put_object(
        Bucket=_BUCKET,
        Key=key,
        Body=body,
        ContentType="application/json",
        ContentEncoding="gzip",
    )

    return f"s3://{_BUCKET}/{key}"