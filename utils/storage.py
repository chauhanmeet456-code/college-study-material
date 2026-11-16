"""Private Backblaze B2 storage using its S3-compatible API."""

import hashlib
import logging
import os

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from config import Config


logger = logging.getLogger('studyhub.storage')
B2_PATH_PREFIX = 'b2://'
_CHUNK_SIZE = 1024 * 1024


class B2StorageError(Exception):
    """Raised when a configured B2 operation fails or cannot be verified."""


def is_b2_path(file_path):
    return isinstance(file_path, str) and file_path.startswith(B2_PATH_PREFIX)


def b2_key(file_path):
    if not is_b2_path(file_path):
        return None
    key = file_path[len(B2_PATH_PREFIX):]
    if not key or key.startswith('/') or '\\' in key:
        raise B2StorageError('The stored Backblaze object key is invalid.')
    if any(part in ('', '.', '..') for part in key.split('/')):
        raise B2StorageError('The stored Backblaze object key is invalid.')
    return key


def _client_error_message(operation, exc):
    response = getattr(exc, 'response', {}) or {}
    error = response.get('Error', {}) or {}
    code = error.get('Code', 'unknown')
    return 'Backblaze B2 {} failed ({}).'.format(operation, code)


class B2Storage:
    def __init__(self):
        self._s3 = None

    @property
    def is_configured(self):
        return all((
            Config.B2_KEY_ID,
            Config.B2_APPLICATION_KEY,
            Config.B2_BUCKET_NAME,
            Config.B2_ENDPOINT_URL,
            Config.B2_REGION,
        ))

    @property
    def bucket_name(self):
        return Config.B2_BUCKET_NAME

    def _client(self, log_errors=True):
        if not self.is_configured:
            raise B2StorageError(
                'Backblaze B2 is not configured. Set all B2_* Render environment variables.'
            )
        if self._s3 is None:
            try:
                self._s3 = boto3.client(
                    's3',
                    endpoint_url=Config.B2_ENDPOINT_URL,
                    aws_access_key_id=Config.B2_KEY_ID,
                    aws_secret_access_key=Config.B2_APPLICATION_KEY,
                    region_name=Config.B2_REGION,
                    config=BotoConfig(
                        signature_version='s3v4',
                        s3={'addressing_style': 'path'},
                        retries={'max_attempts': 3, 'mode': 'standard'},
                    ),
                )
            except (BotoCoreError, ValueError) as exc:
                if log_errors:
                    logger.exception('Could not initialize the Backblaze S3 client.')
                raise B2StorageError(
                    'Could not initialize the Backblaze B2 connection.'
                ) from exc
        return self._s3

    def check_bucket_access(self):
        """Check configured bucket access without reading or changing objects."""
        try:
            self._client(log_errors=False).head_bucket(Bucket=self.bucket_name)
        except B2StorageError:
            raise
        except (BotoCoreError, ClientError) as exc:
            raise B2StorageError(
                'Backblaze B2 bucket access check failed.'
            ) from exc
        return True

    @staticmethod
    def _hash_stream(stream):
        try:
            original_position = stream.tell()
            stream.seek(0)
        except (AttributeError, OSError, ValueError) as exc:
            raise B2StorageError('The uploaded file stream cannot be verified.') from exc

        digest = hashlib.sha256()
        size = 0
        try:
            while True:
                chunk = stream.read(_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
            stream.seek(original_position)
        except (OSError, ValueError) as exc:
            raise B2StorageError('The uploaded file stream could not be read.') from exc
        return size, digest.hexdigest()

    def upload_fileobj(self, stream, key, content_type=None):
        if not key or key.startswith('/') or any(
            part in ('', '.', '..') for part in key.split('/')
        ):
            raise B2StorageError('The destination Backblaze object key is invalid.')
        size, sha256 = self._hash_stream(stream)
        extra_args = {
            'Metadata': {'sha256': sha256},
        }
        if content_type:
            extra_args['ContentType'] = content_type
        try:
            self._client().upload_fileobj(
                stream, self.bucket_name, key, ExtraArgs=extra_args
            )
            self.verify_object(key, size, sha256)
        except B2StorageError:
            raise
        except (BotoCoreError, ClientError) as exc:
            logger.exception('Backblaze upload failed for object key %s.', key)
            raise B2StorageError(_client_error_message('upload', exc)) from exc
        return size, sha256

    def _head_object(self, key):
        try:
            return self._client().head_object(Bucket=self.bucket_name, Key=key)
        except ClientError as exc:
            error = (getattr(exc, 'response', {}) or {}).get('Error', {}) or {}
            if error.get('Code') in ('404', 'NoSuchKey', 'NotFound'):
                return None
            logger.exception('Backblaze HEAD failed for object key %s.', key)
            raise B2StorageError(_client_error_message('object check', exc)) from exc
        except BotoCoreError as exc:
            logger.exception('Backblaze HEAD failed for object key %s.', key)
            raise B2StorageError('Backblaze B2 object check failed.') from exc

    def object_exists(self, key):
        return self._head_object(key) is not None

    def object_info(self, key):
        return self._head_object(key)

    def _download_sha256(self, key):
        response = self._client().get_object(Bucket=self.bucket_name, Key=key)
        digest = hashlib.sha256()
        body = response['Body']
        try:
            while True:
                chunk = body.read(_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
        finally:
            body.close()
        return digest.hexdigest()

    def verify_object(self, key, expected_size, expected_sha256):
        response = self._head_object(key)
        if response is None:
            raise B2StorageError('Backblaze did not return the uploaded object.')
        if response.get('ContentLength') != expected_size:
            raise B2StorageError(
                'Backblaze object size does not match the uploaded file.'
            )

        metadata = response.get('Metadata', {}) or {}
        remote_sha256 = metadata.get('sha256')
        if remote_sha256 is not None:
            if remote_sha256.lower() != expected_sha256.lower():
                raise B2StorageError(
                    'Backblaze object SHA-256 does not match the uploaded file.'
                )

        try:
            actual_sha256 = self._download_sha256(key)
        except (BotoCoreError, ClientError, KeyError, OSError) as exc:
            logger.exception('Backblaze checksum verification failed for %s.', key)
            raise B2StorageError('Backblaze object checksum verification failed.') from exc
        if actual_sha256.lower() != expected_sha256.lower():
            raise B2StorageError(
                'Backblaze object SHA-256 does not match the local file.'
            )
        return True

    def verify_stored_object(self, key, expected_size=None):
        response = self._head_object(key)
        if response is None:
            raise B2StorageError('Backblaze object does not exist.')
        if expected_size not in (None, 0) and response.get('ContentLength') != expected_size:
            raise B2StorageError(
                'Backblaze object size does not match the database file size.'
            )
        expected_sha256 = (response.get('Metadata', {}) or {}).get('sha256')
        if not expected_sha256:
            raise B2StorageError(
                'Backblaze object has no SHA-256 metadata and cannot be verified.'
            )
        try:
            actual_sha256 = self._download_sha256(key)
        except (BotoCoreError, ClientError, KeyError, OSError) as exc:
            logger.exception('Backblaze checksum verification failed for %s.', key)
            raise B2StorageError('Backblaze object checksum verification failed.') from exc
        if actual_sha256.lower() != expected_sha256.lower():
            raise B2StorageError(
                'Backblaze object content does not match its stored SHA-256.'
            )
        return True

    def get_presigned_url(self, key, download_name=None, inline=True):
        disposition = 'inline' if inline else 'attachment'
        if download_name:
            safe_name = (
                os.path.basename(download_name)
                .replace('"', '')
                .replace('\r', '')
                .replace('\n', '')
            )
            disposition += '; filename="{}"'.format(safe_name)
        params = {
            'Bucket': self.bucket_name,
            'Key': key,
            'ResponseContentDisposition': disposition,
        }
        try:
            return self._client().generate_presigned_url(
                'get_object',
                Params=params,
                ExpiresIn=Config.B2_PRESIGNED_URL_EXPIRY,
            )
        except (BotoCoreError, ClientError) as exc:
            logger.exception('Could not sign Backblaze object URL for %s.', key)
            raise B2StorageError('Could not create a secure Backblaze download URL.') from exc

    def delete_object_versions(self, key):
        """Purge every version and delete marker for exactly one object key."""
        try:
            paginator = self._client().get_paginator('list_object_versions')
            versions = []
            for page in paginator.paginate(Bucket=self.bucket_name, Prefix=key):
                versions.extend(
                    {'Key': item['Key'], 'VersionId': item['VersionId']}
                    for item in page.get('Versions', [])
                    if item.get('Key') == key
                )
                versions.extend(
                    {'Key': item['Key'], 'VersionId': item['VersionId']}
                    for item in page.get('DeleteMarkers', [])
                    if item.get('Key') == key
                )
            for start in range(0, len(versions), 1000):
                response = self._client().delete_objects(
                    Bucket=self.bucket_name,
                    Delete={
                        'Objects': versions[start:start + 1000],
                        'Quiet': True,
                    },
                )
                errors = response.get('Errors', [])
                if errors:
                    logger.error(
                        'Backblaze version deletion failed for %s: %s',
                        key,
                        errors,
                    )
                    raise B2StorageError(
                        'Backblaze could not delete every version of the object.'
                    )
        except B2StorageError:
            raise
        except (BotoCoreError, ClientError) as exc:
            logger.exception('Backblaze deletion failed for object key %s.', key)
            raise B2StorageError(_client_error_message('delete', exc)) from exc


def get_storage():
    return B2Storage()
