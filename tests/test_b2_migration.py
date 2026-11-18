import hashlib
import json
import os
import subprocess
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
import psycopg
from flask import Flask

from config import Config
import migrate_materials_to_b2 as migration
from utils.database import DatabaseConnectionError
from utils.storage import B2StorageError
from migrate_materials_to_b2 import (
    DEVELOPMENT_DATABASE_HOST,
    PRODUCTION_DATABASE_HOST,
    _configure_database_target,
    _create_rollback_backup,
    _get_materials,
    _get_migration_connection,
    _load_rollback_backup,
    _make_rollback_backup_payload,
    _object_key,
    _rollback_material_paths,
    _validate_connected_server,
    _update_file_path,
    _validate_backup_file,
    _verify_rollback_backup_against_database,
    _write_rollback_backup,
    PRODUCTION_MATERIAL_IDS,
    _write_report,
    main,
    migrate,
)
import routes.main as main_routes


def _development_identity():
    return {
        'host': DEVELOPMENT_DATABASE_HOST,
        'database': 'neondb',
        'user': 'studyhub_dev',
        'production': False,
    }


def _production_identity():
    return {
        'host': PRODUCTION_DATABASE_HOST,
        'database': 'neondb',
        'user': 'studyhub_prod',
        'production': True,
    }


class FakeStorage:
    is_configured = True

    def __init__(self):
        self.objects = {}
        self.uploaded = []
        self.verified = []
        self.object_checks = []

    def object_info(self, key):
        self.object_checks.append(key)
        value = self.objects.get(key)
        if value is None:
            return None
        return {'ContentLength': len(value)}

    def upload_fileobj(self, source, key, content_type=None):
        content = source.read()
        self.objects[key] = content
        self.uploaded.append(key)
        digest = hashlib.sha256(content).hexdigest()
        self.verify_object(key, len(content), digest)
        return len(content), digest

    def verify_object(self, key, size, sha256):
        content = self.objects[key]
        if (
            len(content) != size
            or hashlib.sha256(content).hexdigest() != sha256
        ):
            raise B2StorageError('Simulated B2 integrity verification failure.')
        self.verified.append(key)
        return True


def _material(path='static/uploads/notes/lecture.pdf'):
    return {
        'id': 42,
        'title': 'Lecture notes',
        'file_name': 'lecture.pdf',
        'file_path': path,
        'file_size': 17,
    }


def _production_materials():
    return [
        {
            'id': material_id,
            'file_path': 'static/uploads/notes/material-{}.pdf'.format(material_id),
            'file_name': 'material-{}.pdf'.format(material_id),
            'title': 'Material {}'.format(material_id),
            'file_size': material_id * 100,
        }
        for material_id in PRODUCTION_MATERIAL_IDS
    ]


def _create_production_pdf_files(tmp_path, materials):
    paths = {}
    for material in materials:
        material_id = material['id']
        size = material['file_size']
        content = (bytes([material_id]) * size)[:size]
        path = tmp_path / '{}.pdf'.format(material_id)
        path.write_bytes(content)
        paths[material_id] = (str(path), 'notes/{}'.format(
            material['file_name']
        ))
    return paths


def assert_verified(storage):
    assert storage.verified == ['materials/42/lecture.pdf']


def test_apply_verifies_upload_before_updating_database_path(tmp_path, monkeypatch):
    source = tmp_path / 'lecture.pdf'
    source.write_bytes(b'verified material')
    storage = FakeStorage()
    updates = []

    def record_update(
        material_id, old_path, new_path, expected_identity, expected_record=None
    ):
        assert_verified(storage)
        updates.append((material_id, old_path, new_path, expected_identity))

    monkeypatch.setattr('migrate_materials_to_b2.B2Storage', lambda: storage)
    monkeypatch.setattr(
        'migrate_materials_to_b2._get_materials',
        lambda expected_identity, material_ids=None: [_material()],
    )
    monkeypatch.setattr(
        'migrate_materials_to_b2.resolve_material_disk_path',
        lambda material: (str(source), 'notes/lecture.pdf'),
    )
    monkeypatch.setattr(
        'migrate_materials_to_b2._update_file_path',
        record_update,
    )

    results = migrate(dry_run=False, expected_identity=_development_identity())

    assert results[0]['status'] == 'success'
    assert storage.uploaded == ['materials/42/lecture.pdf']
    assert storage.verified == ['materials/42/lecture.pdf']
    assert updates == [(
        42,
        'static/uploads/notes/lecture.pdf',
        'b2://materials/42/lecture.pdf',
        _development_identity(),
    )]
    assert source.exists()


def test_missing_local_file_is_reported_without_database_update(monkeypatch):
    storage = FakeStorage()
    updates = []
    monkeypatch.setattr('migrate_materials_to_b2.B2Storage', lambda: storage)
    monkeypatch.setattr(
        'migrate_materials_to_b2._get_materials',
        lambda expected_identity, material_ids=None: [_material()],
    )
    monkeypatch.setattr(
        'migrate_materials_to_b2.resolve_material_disk_path',
        lambda material: (None, 'notes/lecture.pdf'),
    )
    monkeypatch.setattr(
        'migrate_materials_to_b2._update_file_path',
        lambda *args: updates.append(args),
    )

    with pytest.raises(RuntimeError, match='preflight failed'):
        migrate(dry_run=False, expected_identity=_development_identity())

    assert updates == []
    assert storage.uploaded == []


@pytest.mark.parametrize(
    ('material_id', 'failure'),
    ((7, 'missing'), (8, 'missing'), (7, 'unreadable'), (8, 'unreadable')),
)
def test_targeted_preflight_late_local_failure_prevents_all_writes(
    tmp_path, monkeypatch, material_id, failure
):
    materials = _production_materials()
    paths = _create_production_pdf_files(tmp_path, materials)
    if failure == 'missing':
        (tmp_path / '{}.pdf'.format(material_id)).unlink()
    storage = FakeStorage()
    updates = []
    monkeypatch.setattr('migrate_materials_to_b2.B2Storage', lambda: storage)
    monkeypatch.setattr(
        'migrate_materials_to_b2._get_materials',
        lambda identity, material_ids=None: materials,
    )
    monkeypatch.setattr(
        'migrate_materials_to_b2.resolve_material_disk_path',
        lambda material: paths[material['id']],
    )
    monkeypatch.setattr(
        'migrate_materials_to_b2._update_file_path',
        lambda *args, **kwargs: updates.append(args),
    )
    if failure == 'unreadable':
        checksum = migration._file_checksum

        def unreadable_checksum(path):
            if path == paths[material_id][0]:
                raise PermissionError('simulated unreadable PDF')
            return checksum(path)

        monkeypatch.setattr(
            'migrate_materials_to_b2._file_checksum',
            unreadable_checksum,
        )

    with pytest.raises(RuntimeError, match='preflight failed'):
        migrate(
            dry_run=False,
            expected_identity=_production_identity(),
            material_ids=PRODUCTION_MATERIAL_IDS,
            expected_records=materials,
        )

    assert storage.uploaded == []
    assert storage.object_checks == []
    assert updates == []


def test_targeted_preflight_size_mismatch_prevents_all_writes(
    tmp_path, monkeypatch
):
    materials = _production_materials()
    paths = _create_production_pdf_files(tmp_path, materials)
    materials[-1]['file_size'] += 1
    storage = FakeStorage()
    updates = []
    monkeypatch.setattr('migrate_materials_to_b2.B2Storage', lambda: storage)
    monkeypatch.setattr(
        'migrate_materials_to_b2._get_materials',
        lambda identity, material_ids=None: materials,
    )
    monkeypatch.setattr(
        'migrate_materials_to_b2.resolve_material_disk_path',
        lambda material: paths[material['id']],
    )
    monkeypatch.setattr(
        'migrate_materials_to_b2._update_file_path',
        lambda *args, **kwargs: updates.append(args),
    )

    with pytest.raises(RuntimeError, match='file size does not match'):
        migrate(
            dry_run=False,
            expected_identity=_production_identity(),
            material_ids=PRODUCTION_MATERIAL_IDS,
            expected_records=materials,
        )

    assert storage.uploaded == []
    assert storage.object_checks == []
    assert updates == []


def test_targeted_preflight_sha_mismatch_prevents_all_writes(
    tmp_path, monkeypatch
):
    materials = _production_materials()
    paths = _create_production_pdf_files(tmp_path, materials)
    storage = FakeStorage()
    final = materials[-1]
    key = _object_key(final)
    correct = (bytes([final['id']]) * final['file_size'])[:final['file_size']]
    storage.objects[key] = b'x' * len(correct)
    updates = []
    monkeypatch.setattr('migrate_materials_to_b2.B2Storage', lambda: storage)
    monkeypatch.setattr(
        'migrate_materials_to_b2._get_materials',
        lambda identity, material_ids=None: materials,
    )
    monkeypatch.setattr(
        'migrate_materials_to_b2.resolve_material_disk_path',
        lambda material: paths[material['id']],
    )
    monkeypatch.setattr(
        'migrate_materials_to_b2._update_file_path',
        lambda *args, **kwargs: updates.append(args),
    )

    with pytest.raises(RuntimeError, match='integrity verification'):
        migrate(
            dry_run=False,
            expected_identity=_production_identity(),
            material_ids=PRODUCTION_MATERIAL_IDS,
            expected_records=materials,
        )

    assert storage.uploaded == []
    assert updates == []


def test_targeted_preflight_success_migrates_all_six_expected_keys(
    tmp_path, monkeypatch
):
    materials = _production_materials()
    paths = _create_production_pdf_files(tmp_path, materials)
    storage = FakeStorage()
    updates = []
    monkeypatch.setattr('migrate_materials_to_b2.B2Storage', lambda: storage)
    monkeypatch.setattr(
        'migrate_materials_to_b2._get_materials',
        lambda identity, material_ids=None: materials,
    )
    monkeypatch.setattr(
        'migrate_materials_to_b2.resolve_material_disk_path',
        lambda material: paths[material['id']],
    )

    def update_path(material_id, *args, **kwargs):
        assert 'materials/{}/'.format(material_id) in storage.verified[-1]
        updates.append(material_id)

    monkeypatch.setattr(
        'migrate_materials_to_b2._update_file_path',
        update_path,
    )

    results = migrate(
        dry_run=False,
        expected_identity=_production_identity(),
        material_ids=PRODUCTION_MATERIAL_IDS,
        expected_records=materials,
    )

    expected_keys = [
        'materials/{}/{}'.format(record['id'], record['file_name'])
        for record in materials
    ]
    assert [result['status'] for result in results] == ['success'] * 6
    assert storage.uploaded == expected_keys
    assert storage.verified == expected_keys
    assert updates == list(PRODUCTION_MATERIAL_IDS)
    assert all(os.path.isfile(paths[record['id']][0]) for record in materials)


def test_dry_run_does_not_upload_or_update_database(monkeypatch, tmp_path):
    source = tmp_path / 'lecture.pdf'
    source.write_bytes(b'verified material')
    storage = FakeStorage()
    updates = []
    monkeypatch.setattr('migrate_materials_to_b2.B2Storage', lambda: storage)
    monkeypatch.setattr(
        'migrate_materials_to_b2._get_materials',
        lambda expected_identity, material_ids=None: [_material()],
    )
    monkeypatch.setattr(
        'migrate_materials_to_b2.resolve_material_disk_path',
        lambda material: (str(source), 'notes/lecture.pdf'),
    )
    monkeypatch.setattr(
        'migrate_materials_to_b2._update_file_path',
        lambda *args: updates.append(args),
    )

    results = migrate(dry_run=True, expected_identity=_development_identity())

    assert results[0]['status'] == 'planned'
    assert updates == []
    assert storage.uploaded == []
    assert source.exists()


def test_existing_mismatched_b2_object_is_not_overwritten(tmp_path, monkeypatch):
    source = tmp_path / 'lecture.pdf'
    source.write_bytes(b'verified material')
    storage = FakeStorage()
    storage.objects['materials/42/lecture.pdf'] = b'old unrelated object'
    updates = []
    monkeypatch.setattr('migrate_materials_to_b2.B2Storage', lambda: storage)
    monkeypatch.setattr(
        'migrate_materials_to_b2._get_materials',
        lambda expected_identity, material_ids=None: [_material()],
    )
    monkeypatch.setattr(
        'migrate_materials_to_b2.resolve_material_disk_path',
        lambda material: (str(source), 'notes/lecture.pdf'),
    )
    monkeypatch.setattr(
        'migrate_materials_to_b2._update_file_path',
        lambda *args: updates.append(args),
    )

    with pytest.raises(RuntimeError, match='preflight failed'):
        migrate(dry_run=False, expected_identity=_development_identity())

    assert storage.uploaded == []
    assert storage.objects['materials/42/lecture.pdf'] == b'old unrelated object'
    assert updates == []


def test_migration_report_counts_each_result(tmp_path):
    results = [
        {'status': 'success'},
        {'status': 'failed'},
        {'status': 'missing'},
        {'status': 'skipped'},
        {'status': 'planned'},
    ]

    counts = _write_report(str(tmp_path / 'report.json'), True, results)

    assert counts == {
        'success': 1,
        'failed': 1,
        'missing': 1,
        'skipped': 1,
        'planned': 1,
    }


def test_migration_refuses_non_postgres_database(monkeypatch):
    monkeypatch.setattr(Config, 'USE_POSTGRES', True)
    monkeypatch.setattr(Config, 'DATABASE_URL', 'postgresql://dev@localhost/studyhub')
    monkeypatch.setattr(Config, 'DATABASE_ENVIRONMENT', 'development')

    with pytest.raises(SystemExit) as error:
        main([
            '--target', 'production',
            '--confirm-production-host', 'wrong.example',
            '--dry-run',
        ])

    assert error.value.code == 2


def test_check_storage_loads_dotenv_and_skips_database_setup(
    monkeypatch, capsys, tmp_path
):
    seen = {}
    values = {
        'B2_KEY_ID': 'test-key-id',
        'B2_APPLICATION_KEY': 'test-application-key',
        'B2_BUCKET_NAME': 'test-bucket',
        'B2_ENDPOINT_URL': 'https://s3.example.test',
        'B2_REGION': 'eu-central-003',
    }

    def fake_load_dotenv(dotenv_path, override):
        seen['path'] = dotenv_path
        seen['override'] = override
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        return True

    class StorageCheck:
        def check_bucket_access(self):
            assert {
                name: getattr(Config, name)
                for name in values
            } == values
            seen['checked'] = True
            return True

    monkeypatch.setattr(migration, 'load_dotenv', fake_load_dotenv)
    monkeypatch.setattr(migration, 'B2Storage', StorageCheck)
    monkeypatch.setattr(
        migration,
        '_configure_database_target',
        lambda *args, **kwargs: pytest.fail('Database target was configured'),
    )
    monkeypatch.setattr(
        migration,
        'get_connection',
        lambda: pytest.fail('Database connection was opened'),
    )
    monkeypatch.setattr(
        migration,
        'get_connection_for_url',
        lambda *args, **kwargs: pytest.fail('Database connection was opened'),
    )

    assert main(['--check-storage']) == 0
    captured = capsys.readouterr()

    assert captured.out == 'PASS\n'
    assert captured.err == ''
    assert seen['path'] == os.path.join(
        os.path.dirname(os.path.abspath(migration.__file__)),
        '.env',
    )
    assert seen['override'] is False
    assert seen['checked'] is True


def test_check_storage_failure_prints_only_fail(monkeypatch, capsys):
    monkeypatch.setattr(
        migration,
        'load_dotenv',
        lambda **kwargs: False,
    )

    class StorageCheck:
        def check_bucket_access(self):
            raise B2StorageError('failure details must not be printed')

    monkeypatch.setattr(migration, 'B2Storage', StorageCheck)
    monkeypatch.setattr(
        migration,
        '_configure_database_target',
        lambda *args, **kwargs: pytest.fail('Database target was configured'),
    )

    assert main(['--check-storage']) == 1
    captured = capsys.readouterr()

    assert captured.out == 'FAIL\n'
    assert captured.err == ''


def test_production_database_requires_exact_explicit_confirmation(
    monkeypatch, tmp_path
):
    ca_bundle = tmp_path / 'certifi-cacert.pem'
    ca_bundle.write_bytes(b'test CA bundle')
    monkeypatch.setattr(
        'migrate_materials_to_b2.certifi.where',
        lambda: str(ca_bundle),
    )
    monkeypatch.setenv(
        'STUDYHUB_B2_PRODUCTION_DATABASE_URL',
        'postgresql://migration:secret@{}/neondb?sslmode=require&sslrootcert=%2Fold%2Fbundle.pem'.format(
            PRODUCTION_DATABASE_HOST
        ),
    )
    monkeypatch.setattr(Config, 'DATABASE_URL', 'postgresql://dev@localhost/devdb')
    monkeypatch.setattr(Config, 'DATABASE_CONFIG_ERROR', None)
    monkeypatch.setattr(Config, 'DATABASE_ENVIRONMENT', 'development')
    monkeypatch.setattr(Config, 'USE_SQLITE', False)
    monkeypatch.setattr(Config, 'USE_POSTGRES', True)

    with pytest.raises(ValueError, match='exact configured production hostname'):
        _configure_database_target('production')

    expected_identity = _configure_database_target(
        'production',
        confirm_production_host=PRODUCTION_DATABASE_HOST,
    )

    assert expected_identity == {
        'host': PRODUCTION_DATABASE_HOST,
        'database': 'neondb',
        'user': 'migration',
        'production': True,
    }
    ssl_options = parse_qs(urlsplit(migration._PRODUCTION_DATABASE_URL).query)
    assert 'sslmode' not in ssl_options
    assert 'sslrootcert' not in ssl_options
    assert migration._PRODUCTION_CONNECT_OPTIONS == {
        'sslmode': 'verify-full',
        'sslrootcert': str(ca_bundle),
        'connect_timeout': 30,
    }
    assert Config.DATABASE_URL == 'postgresql://dev@localhost/devdb'
    assert Config.USE_POSTGRES is True
    assert Config.USE_SQLITE is False


def test_migration_production_connection_uses_confirmed_production_url(
    monkeypatch, tmp_path
):
    ca_bundle = tmp_path / 'certifi-cacert.pem'
    ca_bundle.write_bytes(b'test CA bundle')
    monkeypatch.setattr(
        'migrate_materials_to_b2.certifi.where',
        lambda: str(ca_bundle),
    )
    monkeypatch.setenv(
        'STUDYHUB_B2_PRODUCTION_DATABASE_URL',
        'postgresql://migration:secret@{}/neondb'.format(PRODUCTION_DATABASE_HOST),
    )
    development_url = 'postgresql://dev:secret@{}/neondb'.format(
        DEVELOPMENT_DATABASE_HOST
    )
    monkeypatch.setattr(Config, 'DATABASE_URL', development_url)
    monkeypatch.setattr(Config, 'DATABASE_CONFIG_ERROR', None)
    monkeypatch.setattr(Config, 'DATABASE_ENVIRONMENT', 'development')
    monkeypatch.setattr(Config, 'USE_POSTGRES', True)

    identity = _configure_database_target(
        'production',
        confirm_production_host=PRODUCTION_DATABASE_HOST,
    )
    sentinel = object()
    pool_urls = []
    monkeypatch.setattr(
        'migrate_materials_to_b2.get_connection_for_url',
        lambda url, connect_kwargs=None: (
            pool_urls.append((url, connect_kwargs)) or sentinel
        ),
    )
    monkeypatch.setattr(
        'migrate_materials_to_b2.get_connection',
        lambda: pytest.fail('Production used the Development connection pool'),
    )

    assert _get_migration_connection(identity) is sentinel
    pool_url, connect_options = pool_urls[0]
    assert urlsplit(pool_url).hostname == PRODUCTION_DATABASE_HOST
    assert connect_options == {
        'sslmode': 'verify-full',
        'sslrootcert': str(ca_bundle),
        'connect_timeout': 30,
    }
    assert Config.DATABASE_URL == development_url


def test_migration_pool_failure_reports_safe_original_driver_error(
    monkeypatch, tmp_path, caplog
):
    ca_bundle = tmp_path / 'certifi-cacert.pem'
    ca_bundle.write_bytes(b'test CA bundle')
    monkeypatch.setattr(
        'migrate_materials_to_b2.certifi.where',
        lambda: str(ca_bundle),
    )
    monkeypatch.setenv(
        'STUDYHUB_B2_PRODUCTION_DATABASE_URL',
        'postgresql://migration:fake-secret@{}/neondb'.format(
            PRODUCTION_DATABASE_HOST
        ),
    )
    development_url = 'postgresql://dev@{}/neondb'.format(
        DEVELOPMENT_DATABASE_HOST
    )
    monkeypatch.setattr(Config, 'DATABASE_URL', development_url)
    monkeypatch.setattr(Config, 'DATABASE_CONFIG_ERROR', None)
    monkeypatch.setattr(Config, 'DATABASE_ENVIRONMENT', 'development')
    monkeypatch.setattr(Config, 'USE_POSTGRES', True)
    monkeypatch.setattr('utils.database.time.sleep', lambda delay: None)
    monkeypatch.setattr('utils.database._tls.pg_url_pools', {}, raising=False)
    connect_calls = []

    def fail_connect(url, **kwargs):
        connect_calls.append((url, kwargs))
        raise psycopg.OperationalError(
            'certificate verify failed; '
            'postgresql://migration:fake-secret@production.example/neondb '
            'password=fake-secret'
        )

    monkeypatch.setattr('utils.database.psycopg.connect', fail_connect)
    identity = _configure_database_target(
        'production',
        confirm_production_host=PRODUCTION_DATABASE_HOST,
    )

    with pytest.raises(DatabaseConnectionError) as raised:
        _get_migration_connection(identity)

    assert 'certificate verify failed' in str(raised.value)
    assert 'database URL redacted' in str(raised.value)
    assert 'fake-secret' not in str(raised.value)
    assert 'fake-secret' not in caplog.text
    assert len(connect_calls) == 3
    for url, kwargs in connect_calls:
        assert urlsplit(url).hostname == PRODUCTION_DATABASE_HOST
        assert 'sslmode' not in parse_qs(urlsplit(url).query)
        assert 'sslrootcert' not in parse_qs(urlsplit(url).query)
        assert kwargs['sslmode'] == 'verify-full'
        assert kwargs['sslrootcert'] == str(ca_bundle)
        assert kwargs['application_name'] == 'studyhub'
    assert Config.DATABASE_URL == development_url


def test_migration_development_connection_keeps_development_pool(monkeypatch):
    development_url = 'postgresql://dev:secret@{}/neondb'.format(
        DEVELOPMENT_DATABASE_HOST
    )
    monkeypatch.setattr(Config, 'DATABASE_URL', development_url)
    monkeypatch.setattr(Config, 'DATABASE_CONFIG_ERROR', None)
    monkeypatch.setattr(Config, 'DATABASE_ENVIRONMENT', 'development')
    monkeypatch.setattr(Config, 'USE_POSTGRES', True)
    sentinel = object()
    calls = []
    monkeypatch.setattr(
        'migrate_materials_to_b2.get_connection',
        lambda: calls.append('development') or sentinel,
    )
    monkeypatch.setattr(
        'migrate_materials_to_b2.get_connection_for_url',
        lambda url: pytest.fail('Development used the explicit Production pool'),
    )

    identity = _configure_database_target('development')

    assert _get_migration_connection(identity) is sentinel
    assert calls == ['development']
    assert Config.DATABASE_URL == development_url


def test_development_host_is_rejected_for_production(monkeypatch):
    monkeypatch.setenv(
        'STUDYHUB_B2_PRODUCTION_DATABASE_URL',
        'postgresql://migration:secret@{}/neondb'.format(
            DEVELOPMENT_DATABASE_HOST
        ),
    )

    with pytest.raises(ValueError, match='points to Development'):
        _configure_database_target(
            'production',
            confirm_production_host=PRODUCTION_DATABASE_HOST,
        )


def test_default_development_target_rejects_production_config(monkeypatch):
    monkeypatch.setattr(
        Config,
        'DATABASE_URL',
        'postgresql://migration:secret@{}/neondb'.format(
            PRODUCTION_DATABASE_HOST
        ),
    )
    monkeypatch.setattr(Config, 'DATABASE_ENVIRONMENT', 'development')
    monkeypatch.setattr(Config, 'USE_POSTGRES', True)

    with pytest.raises(ValueError, match='cannot be used with the Development target'):
        _configure_database_target('development')


def test_actual_wrong_host_is_rejected_before_material_query(monkeypatch):
    class FakeConnection:
        info = type('Info', (), {'host': DEVELOPMENT_DATABASE_HOST})()

        def close(self):
            pass

        def rollback(self):
            pass

    monkeypatch.setattr(
        'migrate_materials_to_b2._get_migration_connection',
        lambda expected_identity: FakeConnection(),
    )
    monkeypatch.setattr(
        'migrate_materials_to_b2.get_cursor',
        lambda connection: pytest.fail('A material query ran before host validation'),
    )

    with pytest.raises(RuntimeError, match='no material query was executed'):
        _get_materials(_production_identity())


def test_actual_wrong_host_is_rejected_before_path_update(monkeypatch):
    class FakeConnection:
        info = type('Info', (), {'host': DEVELOPMENT_DATABASE_HOST})()

        def close(self):
            pass

        def rollback(self):
            pass

    monkeypatch.setattr(
        'migrate_materials_to_b2._get_migration_connection',
        lambda expected_identity: FakeConnection(),
    )
    monkeypatch.setattr(
        'migrate_materials_to_b2.get_cursor',
        lambda connection: pytest.fail('Database update ran before host validation'),
    )

    with pytest.raises(RuntimeError, match='no material query was executed'):
        _update_file_path(
            42,
            'static/uploads/notes/lecture.pdf',
            'b2://materials/42/lecture.pdf',
            _production_identity(),
        )


def test_apply_still_requires_backup_file(monkeypatch):
    monkeypatch.setattr(Config, 'USE_POSTGRES', True)
    monkeypatch.setattr(
        Config, 'DATABASE_URL',
        'postgresql://dev@{}/neondb'.format(DEVELOPMENT_DATABASE_HOST),
    )
    monkeypatch.setattr(Config, 'DATABASE_ENVIRONMENT', 'development')

    with pytest.raises(SystemExit) as error:
        main(['--apply'])

    assert error.value.code == 2


def test_apply_rejects_full_archive_without_targeted_rollback_backup(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(Config, 'USE_POSTGRES', True)
    monkeypatch.setattr(
        Config,
        'DATABASE_URL',
        'postgresql://dev@{}/neondb'.format(DEVELOPMENT_DATABASE_HOST),
    )
    monkeypatch.setattr(Config, 'DATABASE_ENVIRONMENT', 'development')
    monkeypatch.setattr(
        'migrate_materials_to_b2._validate_backup_file',
        lambda path: None,
    )

    with pytest.raises(SystemExit) as error:
        main([
            '--apply',
            '--backup-file',
            str(tmp_path / 'valid.dump'),
        ])

    assert error.value.code == 2
    assert '--apply requires a verified --rollback-backup' in capsys.readouterr().err


def test_apply_rejects_mismatched_targeted_backup_before_b2_setup(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(Config, 'USE_POSTGRES', True)
    monkeypatch.setattr(
        'migrate_materials_to_b2._configure_database_target',
        lambda *args, **kwargs: _production_identity(),
    )
    def reject_backup(*args):
        raise ValueError('Rollback backup does not match Production records.')

    monkeypatch.setattr(
        'migrate_materials_to_b2._verify_rollback_backup_against_database',
        reject_backup,
    )
    monkeypatch.setattr(
        'migrate_materials_to_b2.B2Storage',
        lambda: pytest.fail('B2 was initialized before backup validation'),
    )
    monkeypatch.setattr(
        'migrate_materials_to_b2.migrate',
        lambda **kwargs: pytest.fail('Migration started before backup validation'),
    )

    with pytest.raises(SystemExit) as error:
        main([
            '--target', 'production',
            '--confirm-production-host', PRODUCTION_DATABASE_HOST,
            '--apply',
            '--rollback-backup', str(tmp_path / 'mismatched.json'),
        ])

    assert error.value.code == 2


def test_apply_rejects_missing_backup_file(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, 'USE_POSTGRES', True)
    monkeypatch.setattr(
        Config, 'DATABASE_URL',
        'postgresql://dev@{}/neondb'.format(DEVELOPMENT_DATABASE_HOST),
    )
    monkeypatch.setattr(Config, 'DATABASE_ENVIRONMENT', 'development')

    with pytest.raises(SystemExit) as error:
        main(['--apply', '--backup-file', str(tmp_path / 'not-a-backup.dump')])

    assert error.value.code == 2


def test_apply_rejects_empty_backup_file(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, 'USE_POSTGRES', True)
    monkeypatch.setattr(
        Config, 'DATABASE_URL',
        'postgresql://dev@{}/neondb'.format(DEVELOPMENT_DATABASE_HOST),
    )
    monkeypatch.setattr(Config, 'DATABASE_ENVIRONMENT', 'development')
    empty_backup = tmp_path / 'empty.dump'
    empty_backup.write_bytes(b'')

    with pytest.raises(SystemExit) as error:
        main(['--apply', '--backup-file', str(empty_backup)])

    assert error.value.code == 2


def test_backup_validation_requires_pg_restore_and_valid_archive(
    tmp_path, monkeypatch
):
    backup = tmp_path / 'database.dump'
    backup.write_bytes(b'archive bytes')
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr('migrate_materials_to_b2.shutil.which', lambda name: 'pg_restore.exe')
    monkeypatch.setattr('migrate_materials_to_b2.subprocess.run', fake_run)

    _validate_backup_file(str(backup))

    assert calls[0][0] == ['pg_restore.exe', '--list', str(backup.resolve())]
    assert calls[0][1]['check'] is True
    assert calls[0][1]['stdout']
    assert calls[0][1]['stderr']


def test_arbitrary_nonempty_file_is_rejected_as_backup(tmp_path, monkeypatch):
    backup = tmp_path / 'not-a-dump.bin'
    backup.write_bytes(b'not a PostgreSQL archive')

    def reject_archive(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])

    monkeypatch.setattr('migrate_materials_to_b2.shutil.which', lambda name: 'pg_restore.exe')
    monkeypatch.setattr('migrate_materials_to_b2.subprocess.run', reject_archive)

    with pytest.raises(ValueError, match='not a valid readable PostgreSQL archive'):
        _validate_backup_file(str(backup))


def test_backup_validation_fails_clearly_without_pg_restore(tmp_path, monkeypatch):
    backup = tmp_path / 'database.dump'
    backup.write_bytes(b'archive bytes')
    monkeypatch.setattr('migrate_materials_to_b2.shutil.which', lambda name: None)

    with pytest.raises(ValueError, match='pg_restore is required'):
        _validate_backup_file(str(backup))


def test_apply_rejects_non_postgres_archive_before_storage_setup(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(Config, 'USE_POSTGRES', True)
    monkeypatch.setattr(
        Config, 'DATABASE_URL',
        'postgresql://dev@{}/neondb'.format(DEVELOPMENT_DATABASE_HOST),
    )
    monkeypatch.setattr(Config, 'DATABASE_ENVIRONMENT', 'development')
    backup = tmp_path / 'not-a-dump.bin'
    backup.write_bytes(b'not a PostgreSQL archive')

    def reject_archive(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])

    monkeypatch.setattr('migrate_materials_to_b2.shutil.which', lambda name: 'pg_restore.exe')
    monkeypatch.setattr('migrate_materials_to_b2.subprocess.run', reject_archive)
    monkeypatch.setattr(
        'migrate_materials_to_b2.B2Storage',
        lambda: pytest.fail('B2 should not initialize before backup validation'),
    )

    with pytest.raises(SystemExit) as error:
        main(['--apply', '--backup-file', str(backup)])

    assert error.value.code == 2


def test_production_connection_requires_tls_before_identity_query(monkeypatch):
    class FakeConnection:
        info = type('Info', (), {
            'host': PRODUCTION_DATABASE_HOST,
        })()
        pgconn = SimpleNamespace(ssl_in_use=False)

    monkeypatch.setattr(
        'migrate_materials_to_b2.get_cursor',
        lambda connection: pytest.fail('Server query ran without TLS'),
    )

    with pytest.raises(RuntimeError, match='not using TLS'):
        _validate_connected_server(FakeConnection(), _production_identity())


def test_matching_live_server_identity_is_accepted(monkeypatch):
    queries = []

    class FakeCursor:
        def execute(self, query):
            queries.append(query)

        def fetchone(self):
            return {
                'database_name': 'neondb',
                'database_user': 'studyhub_prod',
                'server_address': '192.0.2.10',
                'server_port': 5432,
                'server_version_num': '160004',
            }

        def close(self):
            pass

    class FakeConnection:
        info = type('Info', (), {
            'host': PRODUCTION_DATABASE_HOST,
        })()
        pgconn = SimpleNamespace(ssl_in_use=True)

    monkeypatch.setattr(
        'migrate_materials_to_b2.get_cursor',
        lambda connection: FakeCursor(),
    )

    _validate_connected_server(FakeConnection(), _production_identity())

    assert len(queries) == 1
    assert 'current_database()' in queries[0]
    assert 'current_user' in queries[0]
    assert 'inet_server_port()' in queries[0]


def test_live_server_database_and_user_are_checked_before_material_read(
    monkeypatch,
):
    queries = []

    class FakeCursor:
        def execute(self, query):
            queries.append(query)

        def fetchone(self):
            return {
                'database_name': 'development_db',
                'database_user': 'studyhub_prod',
                'server_address': '192.0.2.10',
                'server_port': 5432,
                'server_version_num': '160004',
            }

        def close(self):
            pass

    class FakeConnection:
        info = type('Info', (), {
            'host': PRODUCTION_DATABASE_HOST,
            'ssl_in_use': True,
        })()
        pgconn = SimpleNamespace(ssl_in_use=True)

        def close(self):
            pass

    monkeypatch.setattr(
        'migrate_materials_to_b2._get_migration_connection',
        lambda expected_identity: FakeConnection(),
    )
    monkeypatch.setattr(
        'migrate_materials_to_b2.get_cursor',
        lambda connection: FakeCursor(),
    )

    with pytest.raises(RuntimeError, match='server identity'):
        _get_materials(_production_identity())

    assert len(queries) == 1
    assert 'current_database()' in queries[0]
    assert 'inet_server_addr()' in queries[0]
    assert 'FROM materials' not in queries[0]


def test_live_server_identity_is_verified_before_material_update(monkeypatch):
    queries = []

    class FakeCursor:
        rowcount = 1

        def execute(self, query, params=None):
            queries.append(query)

        def fetchone(self):
            return {
                'database_name': 'neondb',
                'database_user': 'wrong_user',
                'server_address': '192.0.2.10',
                'server_port': 5432,
                'server_version_num': '160004',
            }

        def close(self):
            pass

    class FakeConnection:
        info = type('Info', (), {
            'host': PRODUCTION_DATABASE_HOST,
            'ssl_in_use': True,
        })()
        pgconn = SimpleNamespace(ssl_in_use=True)

        def cursor(self):
            return FakeCursor()

        def close(self):
            pass

        def rollback(self):
            pass

    monkeypatch.setattr(
        'migrate_materials_to_b2._get_migration_connection',
        lambda expected_identity: FakeConnection(),
    )
    monkeypatch.setattr(
        'migrate_materials_to_b2.get_cursor',
        lambda connection: FakeCursor(),
    )

    with pytest.raises(RuntimeError, match='server identity'):
        _update_file_path(
            42,
            'static/uploads/notes/lecture.pdf',
            'b2://materials/42/lecture.pdf',
            _production_identity(),
        )

    assert len(queries) == 1
    assert 'current_database()' in queries[0]
    assert not any('UPDATE materials' in query for query in queries)


def test_failed_upload_does_not_update_database_path(tmp_path, monkeypatch):
    source = tmp_path / 'lecture.pdf'
    source.write_bytes(b'verified material')
    storage = FakeStorage()

    def fail_upload(*args, **kwargs):
        raise RuntimeError('simulated upload failure')

    storage.upload_fileobj = fail_upload
    updates = []
    monkeypatch.setattr('migrate_materials_to_b2.B2Storage', lambda: storage)
    monkeypatch.setattr(
        'migrate_materials_to_b2._get_materials',
        lambda expected_identity, material_ids=None: [_material()],
    )
    monkeypatch.setattr(
        'migrate_materials_to_b2.resolve_material_disk_path',
        lambda material: (str(source), 'notes/lecture.pdf'),
    )
    monkeypatch.setattr(
        'migrate_materials_to_b2._update_file_path',
        lambda *args: updates.append(args),
    )

    results = migrate(dry_run=False, expected_identity=_development_identity())

    assert results[0]['status'] == 'failed'
    assert 'simulated upload failure' in results[0]['message']
    assert updates == []
    assert source.exists()


def test_existing_b2_object_is_verified_before_database_update(tmp_path, monkeypatch):
    source = tmp_path / 'lecture.pdf'
    source.write_bytes(b'verified material')
    storage = FakeStorage()
    key = 'materials/42/lecture.pdf'
    storage.objects[key] = b'verified material'
    updates = []
    monkeypatch.setattr('migrate_materials_to_b2.B2Storage', lambda: storage)
    monkeypatch.setattr(
        'migrate_materials_to_b2._get_materials',
        lambda expected_identity, material_ids=None: [_material()],
    )
    monkeypatch.setattr(
        'migrate_materials_to_b2.resolve_material_disk_path',
        lambda material: (str(source), 'notes/lecture.pdf'),
    )

    def record_update(
        material_id, old_path, new_path, expected_identity, expected_record=None
    ):
        assert storage.verified == [key]
        updates.append((material_id, old_path, new_path, expected_identity))

    monkeypatch.setattr('migrate_materials_to_b2._update_file_path', record_update)

    results = migrate(dry_run=False, expected_identity=_development_identity())

    assert results[0]['status'] == 'success'
    assert storage.uploaded == []
    assert updates == [(
        42,
        'static/uploads/notes/lecture.pdf',
        'b2://materials/42/lecture.pdf',
        _development_identity(),
    )]


def test_legacy_notes_path_resolves_unique_file_in_other_upload_category(
    tmp_path, monkeypatch
):
    filename = 'e6a8db0548b548d9b49a158f2d7b6f93.mp4'
    upload_root = tmp_path / 'static' / 'uploads'
    actual_file = upload_root / 'other' / filename
    actual_file.parent.mkdir(parents=True)
    actual_file.write_bytes(b'legacy video')
    (tmp_path / 'routes').mkdir()

    monkeypatch.setattr(Config, 'UPLOAD_FOLDER', str(upload_root))
    monkeypatch.setattr(main_routes, '__file__', str(tmp_path / 'routes' / 'main.py'))
    app = Flask(
        'legacy_upload_path_test',
        static_folder=str(tmp_path / 'static'),
    )
    material = {
        'id': 10,
        'title': 'Cyber Security Part-2',
        'file_path': 'notes/{}'.format(filename),
        'file_name': filename,
    }

    with app.app_context():
        disk_path, resolved_path = main_routes.resolve_material_disk_path(material)

    assert disk_path == os.path.realpath(str(actual_file))
    assert resolved_path == 'other/{}'.format(filename)
    assert material['file_path'] == 'notes/{}'.format(filename)
    assert actual_file.read_bytes() == b'legacy video'


def test_legacy_filename_resolution_rejects_ambiguous_matches(tmp_path, monkeypatch):
    filename = 'duplicate.pdf'
    upload_root = tmp_path / 'uploads'
    for category in ('notes', 'other'):
        path = upload_root / category / filename
        path.parent.mkdir(parents=True)
        path.write_bytes(category.encode('ascii'))
    (tmp_path / 'routes').mkdir()

    monkeypatch.setattr(Config, 'UPLOAD_FOLDER', str(upload_root))
    monkeypatch.setattr(main_routes, '__file__', str(tmp_path / 'routes' / 'main.py'))
    app = Flask('ambiguous_upload_path_test', static_folder=str(tmp_path / 'static'))
    material = {
        'id': 11,
        'title': 'Ambiguous',
        'file_path': 'missing-category/{}'.format(filename),
        'file_name': filename,
    }

    with app.app_context():
        disk_path, _ = main_routes.resolve_material_disk_path(material)

    assert disk_path is None


def test_targeted_rollback_backup_creation_is_exact_and_live_verified(
    tmp_path, monkeypatch
):
    materials = _production_materials()
    reads = []

    def read_materials(identity, material_ids=None):
        reads.append((identity, material_ids))
        return materials

    monkeypatch.setattr('migrate_materials_to_b2._get_materials', read_materials)
    backup_path = tmp_path / 'production-material-paths.json'

    verified = _create_rollback_backup(str(backup_path), _production_identity())
    loaded = _load_rollback_backup(str(backup_path))

    assert verified == loaded
    assert [record['id'] for record in loaded['records']] == list(PRODUCTION_MATERIAL_IDS)
    assert all(
        set(record) == {'id', 'file_path', 'file_name', 'title', 'file_size'}
        for record in loaded['records']
    )
    assert loaded['records'] == materials
    assert len(loaded['sha256']) == 64
    assert reads == [
        (_production_identity(), PRODUCTION_MATERIAL_IDS),
        (_production_identity(), PRODUCTION_MATERIAL_IDS),
    ]


def test_targeted_backup_validation_rejects_tampering(tmp_path):
    payload = _make_rollback_backup_payload(_production_materials())
    backup_path = tmp_path / 'production-material-paths.json'
    _write_rollback_backup(str(backup_path), payload)

    payload['records'][0]['file_path'] = 'changed/path.pdf'
    backup_path.write_text(json.dumps(payload), encoding='utf-8')

    with pytest.raises(ValueError, match='SHA-256 integrity check failed'):
        _load_rollback_backup(str(backup_path))


def test_targeted_backup_does_not_overwrite_existing_file(tmp_path):
    backup_path = tmp_path / 'production-material-paths.json'
    backup_path.write_text('operator data', encoding='utf-8')
    payload = _make_rollback_backup_payload(_production_materials())

    with pytest.raises(FileExistsError):
        _write_rollback_backup(str(backup_path), payload)

    assert backup_path.read_text(encoding='utf-8') == 'operator data'


def test_targeted_backup_rejects_already_migrated_paths():
    materials = _production_materials()
    materials[0]['file_path'] = 'b2://materials/2/material-2.pdf'

    with pytest.raises(ValueError, match='invalid saved field values'):
        _make_rollback_backup_payload(materials)


def test_targeted_backup_must_match_current_production_rows(
    tmp_path, monkeypatch
):
    materials = _production_materials()
    payload = _make_rollback_backup_payload(materials)
    backup_path = tmp_path / 'production-material-paths.json'
    _write_rollback_backup(str(backup_path), payload)
    changed = [dict(record) for record in materials]
    changed[0]['title'] = 'Changed after backup'
    monkeypatch.setattr(
        'migrate_materials_to_b2._get_materials',
        lambda identity, material_ids=None: changed,
    )

    with pytest.raises(ValueError, match='does not exactly match'):
        _verify_rollback_backup_against_database(
            str(backup_path),
            _production_identity(),
        )


def test_targeted_rollback_restores_only_file_paths(
    tmp_path, monkeypatch
):
    originals = _production_materials()
    payload = _make_rollback_backup_payload(originals)
    backup_path = tmp_path / 'production-material-paths.json'
    _write_rollback_backup(str(backup_path), payload)
    current_rows = [dict(record) for record in originals]
    for row in current_rows:
        row['file_path'] = 'b2://' + _object_key(row)

    class FakeCursor:
        rowcount = 0

        def __init__(self):
            self.rows = []
            self.updates = []
            self.statements = []

        def execute(self, query, params=()):
            self.statements.append(query)
            if query.startswith('SELECT id, file_path'):
                self.rows = current_rows
            elif query.startswith('UPDATE materials SET file_path'):
                new_path, material_id = params[:2]
                row = next(item for item in current_rows if item['id'] == material_id)
                row['file_path'] = new_path
                self.updates.append((material_id, new_path))
                self.rowcount = 1

        def fetchall(self):
            return self.rows

        def close(self):
            pass

    class FakeConnection:
        def __init__(self):
            self.committed = False
            self.rolled_back = False

        def commit(self):
            self.committed = True

        def rollback(self):
            self.rolled_back = True

        def close(self):
            pass

    cursor = FakeCursor()
    connection = FakeConnection()
    monkeypatch.setattr(
        'migrate_materials_to_b2._validate_connected_server',
        lambda connection, identity: None,
    )
    monkeypatch.setattr(
        'migrate_materials_to_b2._get_migration_connection',
        lambda expected_identity: connection,
    )
    monkeypatch.setattr(
        'migrate_materials_to_b2.get_cursor',
        lambda connection: cursor,
    )

    result = _rollback_material_paths(str(backup_path), _production_identity())

    assert result == {
        'restored_ids': list(PRODUCTION_MATERIAL_IDS),
        'already_original_ids': [],
    }
    assert [row['file_path'] for row in current_rows] == [
        record['file_path'] for record in originals
    ]
    assert cursor.updates == [
        (record['id'], record['file_path']) for record in originals
    ]
    assert all('SET file_path' in query for query in cursor.statements if query.startswith('UPDATE'))
    assert not any('DELETE' in query for query in cursor.statements)
    assert connection.committed
    assert not connection.rolled_back


def test_targeted_apply_preflights_backup_and_limits_records(
    tmp_path, monkeypatch
):
    materials = _production_materials()
    payload = _make_rollback_backup_payload(materials)
    backup_path = tmp_path / 'production-material-paths.json'
    _write_rollback_backup(str(backup_path), payload)
    calls = []
    migration_kwargs = {}

    class ConfiguredStorage:
        is_configured = True

    def read_materials(identity, material_ids=None):
        calls.append('verify-backup')
        assert identity == _production_identity()
        assert material_ids == PRODUCTION_MATERIAL_IDS
        return materials

    def create_storage():
        calls.append('storage-config-check')
        return ConfiguredStorage()

    def run_migration(**kwargs):
        calls.append('migration')
        migration_kwargs.update(kwargs)
        return [
            {'status': 'success', 'material_id': record['id']}
            for record in materials
        ]

    monkeypatch.setattr(
        'migrate_materials_to_b2._configure_database_target',
        lambda *args, **kwargs: _production_identity(),
    )
    monkeypatch.setattr('migrate_materials_to_b2._get_materials', read_materials)
    monkeypatch.setattr('migrate_materials_to_b2.B2Storage', create_storage)
    monkeypatch.setattr('migrate_materials_to_b2.migrate', run_migration)
    monkeypatch.setattr(
        'migrate_materials_to_b2._write_report',
        lambda path, dry_run, results: {
            'success': len(results),
            'failed': 0,
            'missing': 0,
            'skipped': 0,
            'planned': 0,
        },
    )
    monkeypatch.setattr(Config, 'USE_POSTGRES', True)

    exit_code = main([
        '--target', 'production',
        '--confirm-production-host', PRODUCTION_DATABASE_HOST,
        '--apply',
        '--rollback-backup', str(backup_path),
        '--report', str(tmp_path / 'report.json'),
    ])

    assert exit_code == 0
    assert calls == ['verify-backup', 'storage-config-check', 'migration']
    assert migration_kwargs['dry_run'] is False
    assert migration_kwargs['material_ids'] == PRODUCTION_MATERIAL_IDS
    assert migration_kwargs['expected_records'] == payload['records']
