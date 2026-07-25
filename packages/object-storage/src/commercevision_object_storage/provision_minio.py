"""Idempotent MinIO bucket provisioning for deployable environments."""

from __future__ import annotations

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from commercevision_contracts.config import load_settings


def provision_minio_buckets() -> None:
    settings = load_settings("object-storage-init")
    if settings.object_store_backend != "minio":
        raise RuntimeError("MinIO provisioning requires CV_OBJECT_STORE_BACKEND=minio")
    client = boto3.client(
        "s3",
        endpoint_url=settings.object_store_endpoint,
        aws_access_key_id=settings.object_store_access_key,
        aws_secret_access_key=settings.object_store_secret_key.get_secret_value(),
        aws_session_token=(
            settings.object_store_session_token.get_secret_value()
            if settings.object_store_session_token is not None
            else None
        ),
        region_name=settings.object_store_region,
        verify=settings.object_store_tls_verify,
        config=Config(
            signature_version="s3v4",
            connect_timeout=settings.object_store_connect_timeout_seconds,
            read_timeout=settings.object_store_read_timeout_seconds,
            retries={"max_attempts": 5, "mode": "standard"},
            s3={
                "addressing_style": (
                    "path" if settings.object_store_force_path_style else "virtual"
                )
            },
        ),
    )
    for bucket in dict.fromkeys(settings.object_store_buckets.values()):
        try:
            client.create_bucket(Bucket=bucket)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code not in {
                "BucketAlreadyExists",
                "BucketAlreadyOwnedByYou",
            }:
                raise
        client.put_bucket_versioning(
            Bucket=bucket,
            VersioningConfiguration={"Status": "Enabled"},
        )
        client.head_bucket(Bucket=bucket)


def main() -> None:
    provision_minio_buckets()


if __name__ == "__main__":
    main()
