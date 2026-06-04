import asyncio
import json
import logging
from typing import Any, BinaryIO, cast
from urllib.parse import urlparse

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.core.config import settings

logger = logging.getLogger(__name__)


class S3Client:
    def __init__(self) -> None:
        self.session = boto3.session.Session()
        self.client = self.session.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            region_name="us-east-1",
            config=Config(signature_version="s3v4"),
        )

    async def upload_file(self, file_obj: BinaryIO, object_name: str, content_type: str) -> str:
        try:
            await asyncio.to_thread(
                self.client.upload_fileobj,
                Fileobj=file_obj,
                Bucket=settings.S3_BUCKET_NAME,
                Key=object_name,
                ExtraArgs={"ContentType": content_type},
            )

            return f"{settings.S3_PUBLIC_ENDPOINT}/{settings.S3_BUCKET_NAME}/{object_name}"

        except ClientError as ex:
            logger.error(f"S3 file upload failed: {ex}")
            raise ex

    async def download_file(self, object_name: str) -> bytes:
        """Download an object's raw bytes by its key."""
        try:
            obj = await asyncio.to_thread(
                self.client.get_object,
                Bucket=settings.S3_BUCKET_NAME,
                Key=object_name,
            )
            return cast(bytes, await asyncio.to_thread(obj["Body"].read))
        except ClientError as ex:
            logger.error(f"S3 file download failed: {ex}")
            raise ex

    def object_key_from_url(self, file_url: str) -> str | None:
        """Extract the bucket-relative object key from a stored image URL.

        URLs are stored as ``{S3_PUBLIC_ENDPOINT}/{bucket}/{key}``. Returns
        the ``{key}`` part, or None if the URL doesn't point at our bucket.
        """
        parsed = urlparse(file_url)
        path_parts = parsed.path.lstrip("/").split("/", 1)
        if len(path_parts) == 2 and path_parts[0] == settings.S3_BUCKET_NAME:
            return path_parts[1]
        return None

    async def delete_file(self, object_name: str) -> None:
        try:
            await asyncio.to_thread(
                self.client.delete_object,
                Bucket=settings.S3_BUCKET_NAME,
                Key=object_name,
            )
        except ClientError as ex:
            logger.error(f"S3 file delete failed: {ex}")
            raise ex

    async def delete_image_from_s3(self, file_url: str) -> None:
        try:
            object_key = self.object_key_from_url(file_url)
            if object_key is not None:
                await self.delete_file(object_key)
        except Exception as ex:
            logger.error(f"S3 image delete failed: {ex}")

    async def clear_bucket(self) -> int:
        deleted_count = 0
        paginator = self.client.get_paginator("list_objects_v2")

        async def _delete_page(objects: list[dict[str, Any]]) -> None:
            nonlocal deleted_count
            await asyncio.to_thread(
                self.client.delete_objects,
                Bucket=settings.S3_BUCKET_NAME,
                Delete={"Objects": [{"Key": obj["Key"]} for obj in objects], "Quiet": True},
            )
            deleted_count += len(objects)

        try:
            pages = await asyncio.to_thread(
                lambda: list(paginator.paginate(Bucket=settings.S3_BUCKET_NAME))
            )
            for page in pages:
                # boto3 stubs type these as ObjectTypeDef (TypedDict). We only
                # touch the "Key" field, so cast to plain dicts for the helper.
                objects = cast(list[dict[str, Any]], page.get("Contents", []))
                if objects:
                    await _delete_page(objects)
        except ClientError as ex:
            logger.error(f"S3 bucket clear failed: {ex}")
            raise ex

        return deleted_count

    async def ensure_bucket_exists(self) -> None:
        try:
            await asyncio.to_thread(self.client.head_bucket, Bucket=settings.S3_BUCKET_NAME)
        except ClientError:
            logger.info(f"Bucket {settings.S3_BUCKET_NAME} not found. Creating...")
            await asyncio.to_thread(self.client.create_bucket, Bucket=settings.S3_BUCKET_NAME)

            policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "PublicRead",
                        "Effect": "Allow",
                        "Principal": "*",
                        "Action": ["s3:GetObject"],
                        "Resource": [f"arn:aws:s3:::{settings.S3_BUCKET_NAME}/*"],
                    }
                ],
            }

            await asyncio.to_thread(
                self.client.put_bucket_policy,
                Bucket=settings.S3_BUCKET_NAME,
                Policy=json.dumps(policy),
            )


s3_client = S3Client()
