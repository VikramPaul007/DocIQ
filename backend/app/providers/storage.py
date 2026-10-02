"""Object storage (MinIO, S3 API), with application-level AES-256-GCM
encryption on top — MinIO/S3 server-side encryption alone would protect data
at rest against someone with disk access, not against someone with S3 API
access but not the document encryption key, which is the actual scenario we
want to be defended against (anyone who gets to the bucket alone gets
ciphertext, not documents)."""

from __future__ import annotations

import os
import uuid

import boto3
from botocore.client import BaseClient
from botocore.exceptions import ClientError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings

_NONCE_SIZE = 12  # bytes; AES-GCM's recommended nonce size


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


def _key() -> bytes:
    return bytes.fromhex(settings.document_encryption_key)


def encrypt(data: bytes) -> bytes:
    """Random nonce prefix + ciphertext (GCM tag included) — self-contained,
    no separate nonce column needed."""
    nonce = os.urandom(_NONCE_SIZE)
    ciphertext = AESGCM(_key()).encrypt(nonce, data, None)
    return nonce + ciphertext


def decrypt(blob: bytes) -> bytes:
    nonce, ciphertext = blob[:_NONCE_SIZE], blob[_NONCE_SIZE:]
    return AESGCM(_key()).decrypt(nonce, ciphertext, None)


def put_document(storage_key: uuid.UUID, data: bytes) -> None:
    client = get_s3_client()
    client.put_object(Bucket=settings.minio_bucket, Key=str(storage_key), Body=encrypt(data))


def get_document(storage_key: uuid.UUID) -> bytes:
    client = get_s3_client()
    obj = client.get_object(Bucket=settings.minio_bucket, Key=str(storage_key))
    return decrypt(obj["Body"].read())
