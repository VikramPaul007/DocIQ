"""Object storage (MinIO, S3 API). Encrypted put/get land with document
upload in a later milestone; for now this just gives the API and the health
check a shared client and an idempotent bucket-ensure."""

from __future__ import annotations

import boto3
from botocore.client import BaseClient
from botocore.exceptions import ClientError

from app.config import settings


def get_s3_client() -> BaseClient:
    scheme = "https" if settings.minio_secure else "http"
    return boto3.client(
        "s3",
        endpoint_url=f"{scheme}://{settings.minio_endpoint}",
        aws_access_key_id=settings.minio_root_user,
        aws_secret_access_key=settings.minio_root_password,
        region_name="us-east-1",
    )


def ensure_bucket(client: BaseClient) -> None:
    try:
        client.head_bucket(Bucket=settings.minio_bucket)
    except ClientError:
        client.create_bucket(Bucket=settings.minio_bucket)


def storage_healthy() -> bool:
    try:
        get_s3_client().head_bucket(Bucket=settings.minio_bucket)
        return True
    except Exception:
        return False
