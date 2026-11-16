import hashlib
import io

import pytest
from botocore.exceptions import ClientError
from werkzeug.datastructures import FileStorage

from config import Config
from utils.storage import B2Storage, B2StorageError, b2_key


class FakePaginator:
    def paginate(self, **kwargs):
        assert kwargs['Prefix'] == 'materials/item.pdf'
        return [{
            'Versions': [
                {'Key': 'materials/item.pdf', 'VersionId': 'v1'},
                {'Key': 'materials/item.pdf.backup', 'VersionId': 'other'},
            ],
            'DeleteMarkers': [
                {'Key': 'materials/item.pdf', 'VersionId': 'marker1'},
            ],
        }]


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.bad_sha = False
        self.deleted = []
        self.head_buckets = []
        self.bucket_error = None

    def head_bucket(self, Bucket):
        self.head_buckets.append(Bucket)
        if self.bucket_error:
            raise self.bucket_error
        return {'ResponseMetadata': {'HTTPStatusCode': 200}}

    def upload_fileobj(self, stream, bucket, key, ExtraArgs):
        body = stream.read()
        self.objects[key] = {
            'body': body,
            'metadata': ExtraArgs['Metadata'],
            'content_type': ExtraArgs.get('ContentType'),
        }

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError(
                {'Error': {'Code': '404'}},
                'HeadObject',
            )
        stored = self.objects[Key]
        metadata = dict(stored['metadata'])
        if self.bad_sha:
            metadata['sha256'] = '0' * 64
        return {
            'ContentLength': len(stored['body']),
            'Metadata': metadata,
        }

    def get_object(self, Bucket, Key):
        return {'Body': io.BytesIO(self.objects[Key]['body'])}

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        self.signed_args = (operation, Params, ExpiresIn)
        return 'https://private-b2.example/signed'

    def get_paginator(self, operation):
        assert operation == 'list_object_versions'
        return FakePaginator()

    def delete_objects(self, Bucket, Delete):
        self.deleted.extend(Delete['Objects'])
        return {'Deleted': Delete['Objects']}


@pytest.fixture
def b2(monkeypatch):
    for name, value in (
        ('B2_KEY_ID', 'key-id'),
        ('B2_APPLICATION_KEY', 'application-key'),
        ('B2_BUCKET_NAME', 'private-bucket'),
        ('B2_ENDPOINT_URL', 'https://s3.example.test'),
        ('B2_REGION', 'us-west-004'),
    ):
        monkeypatch.setattr(Config, name, value)
    client = FakeS3()
    monkeypatch.setattr('utils.storage.boto3.client', lambda *args, **kwargs: client)
    return B2Storage(), client


def test_upload_checks_size_and_sha256_before_success(b2):
    storage, client = b2
    content = b'private material'

    size, digest = storage.upload_fileobj(
        io.BytesIO(content), 'materials/one/material.pdf', 'application/pdf'
    )

    assert size == len(content)
    assert digest == hashlib.sha256(content).hexdigest()
    assert client.objects['materials/one/material.pdf']['body'] == content


def test_bucket_access_check_uses_only_head_bucket(b2):
    storage, client = b2

    assert storage.check_bucket_access() is True

    assert client.head_buckets == ['private-bucket']
    assert client.objects == {}
    assert client.deleted == []


def test_bucket_access_check_reports_failure_without_mutating_objects(b2):
    storage, client = b2
    client.bucket_error = ClientError(
        {'Error': {'Code': '403'}},
        'HeadBucket',
    )

    with pytest.raises(B2StorageError, match='bucket access check failed'):
        storage.check_bucket_access()

    assert client.head_buckets == ['private-bucket']
    assert client.objects == {}
    assert client.deleted == []


def test_upload_rejects_mismatched_remote_checksum(b2):
    storage, client = b2
    client.bad_sha = True

    with pytest.raises(B2StorageError, match='SHA-256'):
        storage.upload_fileobj(io.BytesIO(b'content'), 'materials/one/file.pdf')


def test_stored_object_verification_checks_downloaded_bytes(b2):
    storage, client = b2
    content = b'original bytes'
    size, _ = storage.upload_fileobj(
        io.BytesIO(content), 'materials/one/file.pdf'
    )
    client.objects['materials/one/file.pdf']['body'] = b'changed bytes!'

    with pytest.raises(B2StorageError, match='stored SHA-256'):
        storage.verify_stored_object('materials/one/file.pdf', size)


def test_presigned_url_is_private_and_uses_configured_expiry(b2, monkeypatch):
    storage, client = b2
    monkeypatch.setattr(Config, 'B2_PRESIGNED_URL_EXPIRY', 300)

    url = storage.get_presigned_url(
        'materials/one/file.pdf', 'study.pdf', inline=False
    )

    assert url.startswith('https://')
    assert client.signed_args[0] == 'get_object'
    assert client.signed_args[1]['ResponseContentDisposition'] == (
        'attachment; filename="study.pdf"'
    )
    assert client.signed_args[2] == 300


def test_deletion_purges_versions_and_markers_for_exact_key(b2):
    storage, client = b2

    storage.delete_object_versions('materials/item.pdf')

    assert client.deleted == [
        {'Key': 'materials/item.pdf', 'VersionId': 'v1'},
        {'Key': 'materials/item.pdf', 'VersionId': 'marker1'},
    ]


def test_admin_material_upload_uses_b2_without_writing_local_file(monkeypatch):
    import routes.admin as admin_routes

    class UploadStorage:
        is_configured = True

        def upload_fileobj(self, stream, key, content_type=None):
            assert stream.read() == b'uploaded directly'
            assert content_type == 'application/pdf'
            self.key = key
            return 17, 'checksum'

    storage = UploadStorage()
    monkeypatch.setattr(admin_routes, 'get_storage', lambda: storage)
    upload = FileStorage(
        stream=io.BytesIO(b'uploaded directly'),
        filename='lecture.pdf',
        content_type='application/pdf',
    )

    filename, file_path, file_type, file_size = admin_routes._upload_material_file(
        upload, 'notes'
    )

    assert filename.endswith('.pdf')
    assert file_path == 'b2://' + storage.key
    assert file_type == 'pdf'
    assert file_size == 17


@pytest.mark.parametrize('reference_count', (0, 1))
def test_admin_deletion_only_purges_unshared_b2_objects(
    monkeypatch, reference_count
):
    import routes.admin as admin_routes

    class Cursor:
        def execute(self, query, params):
            assert 'COUNT(*)' in query
            assert params == ('b2://materials/shared/file.pdf',)

        def fetchone(self):
            return {'count': reference_count}

        def close(self):
            pass

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    class DeleteStorage:
        def __init__(self):
            self.deleted = []

        def delete_object_versions(self, key):
            self.deleted.append(key)

    storage = DeleteStorage()
    monkeypatch.setattr(admin_routes, 'get_connection', Connection)
    monkeypatch.setattr(admin_routes, 'get_storage', lambda: storage)

    result = admin_routes._delete_b2_file_if_unreferenced(
        'b2://materials/shared/file.pdf'
    )

    assert result is None
    assert storage.deleted == (
        [] if reference_count else ['materials/shared/file.pdf']
    )


@pytest.mark.parametrize(
    'path',
    ('b2://', 'b2:///absolute.pdf', 'b2://materials/../private.pdf'),
)
def test_invalid_stored_b2_keys_are_rejected(path):
    with pytest.raises(B2StorageError):
        b2_key(path)
