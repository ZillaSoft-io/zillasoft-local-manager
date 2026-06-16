"""AWS S3 verification + CloudFront invalidation for the website (spec §8.2).

boto3 clients are built lazily from .env credentials, or injected for tests.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class AwsDeploy:
    def __init__(self, config, s3_client=None, cloudfront_client=None):
        self._config = config
        self._s3c = s3_client
        self._cfc = cloudfront_client

    def configured(self) -> bool:
        return self._config.is_set(self._config.get_raw("AWS_ACCESS_KEY_ID"))

    def _creds(self) -> dict:
        return {
            "aws_access_key_id": self._config.require("AWS_ACCESS_KEY_ID"),
            "aws_secret_access_key": self._config.require("AWS_SECRET_ACCESS_KEY"),
        }

    def _s3(self):
        if self._s3c is None:
            import boto3  # lazy: only needed for live AWS
            region = self._config.get_raw("PROJECT_WEBSITE_S3_REGION", "us-east-1")
            self._s3c = boto3.client("s3", region_name=region, **self._creds())
        return self._s3c

    def _cf(self):
        if self._cfc is None:
            import boto3
            self._cfc = boto3.client("cloudfront", **self._creds())
        return self._cfc

    def verify_s3(self, bucket: str) -> dict:
        resp = self._s3().list_objects_v2(Bucket=bucket, MaxKeys=1)
        count = resp.get("KeyCount", 0)
        return {"target": "s3", "ok": count > 0, "bucket": bucket,
                "key_count": count}

    def invalidate(self, distribution_id: str,
                   paths: Optional[list] = None) -> dict:
        paths = paths or ["/*"]
        resp = self._cf().create_invalidation(
            DistributionId=distribution_id,
            InvalidationBatch={
                "Paths": {"Quantity": len(paths), "Items": paths},
                "CallerReference": f"zlm-{int(time.time())}"})
        inv = resp.get("Invalidation", {})
        return {"target": "cloudfront", "ok": True,
                "invalidation_id": inv.get("Id"), "status": inv.get("Status")}
