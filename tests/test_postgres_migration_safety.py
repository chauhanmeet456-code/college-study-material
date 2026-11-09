import hashlib
import sqlite3

import pytest

from migrate_to_postgres import (
    _convert_value,
    _migration_value,
    main,
    migration_column_pairs,
    open_sqlite_readonly,
    resolve_target_url,
)


LOCAL_URL = "postgresql://postgres@localhost:5432/college_study"


def test_local_migration_requires_explicit_development_url():
    selected, error = resolve_target_url(
        "development",
        {"DATABASE_URL": "postgresql://user@db.example.test:5432/study"},
    )

    assert selected is None
    assert "never selected locally" in error


def test_development_migration_rejects_non_loopback_target():
    selected, error = resolve_target_url(
        "development",
        {"DEV_DATABASE_URL": "postgresql://user@db.example.test:5432/study"},
    )

    assert selected is None
    assert "must be a loopback" in error


def test_render_migration_must_explicitly_select_production():
    selected, error = resolve_target_url(
        "development",
        {"RENDER": "true", "DEV_DATABASE_URL": LOCAL_URL},
    )

    assert selected is None
    assert "--target production" in error


def test_production_migration_uses_only_production_database_url():
    selected, error = resolve_target_url(
        "production",
        {
            "DEV_DATABASE_URL": LOCAL_URL,
            "DATABASE_URL": "postgresql://user@prod.example.test:5432/study",
        },
    )

    assert selected == "postgresql://user@prod.example.test:5432/study"
    assert error is None


def test_legacy_oauth_subject_is_mapped_and_hashed():
    pairs = migration_column_pairs(
        "user_oauth_accounts",
        ["provider", "provider_subject", "user_id", "linked_at"],
        {"provider": {}, "provider_subject_hash": {}, "user_id": {}, "linked_at": {}},
    )

    assert pairs == [
        ("provider", "provider"),
        ("provider_subject", "provider_subject_hash"),
        ("user_id", "user_id"),
        ("linked_at", "linked_at"),
    ]
    assert _migration_value(
        "user_oauth_accounts",
        "provider_subject",
        "provider_subject_hash",
        "legacy-subject",
        {"data_type": "character varying", "udt_name": "varchar"},
    ) == hashlib.sha256(b"legacy-subject").hexdigest()


def test_sqlite_source_is_read_only_and_missing_file_is_not_created(tmp_path):
    source = tmp_path / "source.sqlite"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")

    conn = open_sqlite_readonly(source)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO sample DEFAULT VALUES")
    conn.close()

    missing = tmp_path / "missing.sqlite"
    with pytest.raises(FileNotFoundError):
        open_sqlite_readonly(missing)
    assert not missing.exists()


def test_migration_value_conversion_preserves_null_and_common_types():
    assert _convert_value(None, {"data_type": "boolean", "udt_name": "bool"}) is None
    assert _convert_value(0, {"data_type": "boolean", "udt_name": "bool"}) is False
    assert _convert_value(1, {"data_type": "boolean", "udt_name": "bool"}) is True
    assert _convert_value(
        "2025-01-02", {"data_type": "date", "udt_name": "date"}
    ).isoformat() == "2025-01-02"
    assert _convert_value(
        "2025-01-02T03:04:05", {"data_type": "timestamp without time zone", "udt_name": "timestamp"}
    ).isoformat() == "2025-01-02T03:04:05"


def test_production_and_reset_writes_require_explicit_confirmations(capsys):
    assert main(["--target", "production", "--apply"]) == 2
    assert "require --confirm-production" in capsys.readouterr().err

    assert main(["--target", "development", "--reset", "--apply"]) == 2
    assert "--confirm-reset" in capsys.readouterr().err

    assert main(["--target", "production", "--reset", "--apply", "--confirm-production", "--confirm-reset"]) == 2
    assert "disabled for production" in capsys.readouterr().err


def test_schema_initialization_requires_apply(capsys):
    assert main(["--target", "development", "--initialize-schema"]) == 2
    assert "--initialize-schema requires --apply" in capsys.readouterr().err


def test_migration_requires_explicit_target():
    with pytest.raises(SystemExit) as exc_info:
        main(["--check-config"])

    assert exc_info.value.code == 2
