"""Safely migrate database-referenced local material files to Backblaze B2."""

import argparse
import certifi
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from flask import Flask
from dotenv import load_dotenv
from werkzeug.utils import secure_filename

from config import Config, _normalize_postgres_url
from routes.main import resolve_material_disk_path
from utils.database import (
    close_cursor,
    get_connection,
    get_connection_for_url,
    get_cursor,
)
from utils.storage import B2_PATH_PREFIX, B2Storage, B2StorageError, b2_key

logger = logging.getLogger("studyhub.b2_migration")
CHUNK_SIZE = 1024 * 1024
PRODUCTION_DATABASE_HOST = (
    "ep-sparkling-truth-b4b9nq18-pooler.c-6.us-east-2.aws.neon.tech"
)
DEVELOPMENT_DATABASE_HOST = "ep-tiny-hill-b43s1931-pooler.c-6.us-east-2.aws.neon.tech"
PRODUCTION_MATERIAL_IDS = (2, 3, 4, 5, 7, 8)
ROLLBACK_BACKUP_FORMAT = "studyhub-material-path-rollback-v1"
ROLLBACK_BACKUP_FIELDS = {
    "format",
    "target",
    "database_host",
    "database",
    "created_at",
    "material_ids",
    "records",
    "sha256",
}
ROLLBACK_RECORD_FIELDS = {"id", "file_path", "file_name", "title", "file_size"}
_PRODUCTION_DATABASE_URL = None
_PRODUCTION_CONNECT_OPTIONS = None
B2_ENVIRONMENT_VARIABLES = (
    "B2_KEY_ID",
    "B2_APPLICATION_KEY",
    "B2_BUCKET_NAME",
    "B2_ENDPOINT_URL",
    "B2_REGION",
)


def _load_storage_environment():
    env_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        ".env",
    )
    load_dotenv(dotenv_path=env_path, override=False)
    for name in B2_ENVIRONMENT_VARIABLES:
        setattr(Config, name, os.environ.get(name, ""))


def _check_storage():
    try:
        _load_storage_environment()
        B2Storage().check_bucket_access()
    except (B2StorageError, OSError, UnicodeError, ValueError):
        print("FAIL")
        return 1
    print("PASS")
    return 0


def _normalize_host(host):
    return (host or "").strip().rstrip(".").lower()


def _hostname_from_url(database_url):
    try:
        return _normalize_host(urlsplit(database_url).hostname)
    except (TypeError, ValueError):
        return ""


def _connection_identity(database_url, production=False):
    parsed = urlsplit(database_url)
    return {
        "host": _normalize_host(parsed.hostname),
        "database": unquote(parsed.path.lstrip("/")),
        "user": unquote(parsed.username or ""),
        "production": production,
    }


def _remove_ssl_url_options(database_url):
    parsed = urlsplit(database_url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in {"sslmode", "sslrootcert"}
    ]
    return urlunsplit(parsed._replace(query=urlencode(query)))


def _get_migration_connection(expected_identity):
    if expected_identity.get("production"):
        database_url = _PRODUCTION_DATABASE_URL
        if not database_url or _hostname_from_url(
            database_url
        ) != expected_identity.get("host"):
            raise RuntimeError(
                "The explicitly selected Production database URL is unavailable or mismatched."
            )
        return get_connection_for_url(
            database_url,
            connect_kwargs=dict(_PRODUCTION_CONNECT_OPTIONS or {}),
        )
    if expected_identity.get("host") == PRODUCTION_DATABASE_HOST:
        raise RuntimeError(
            "Production cannot be accessed through the Development connection pool."
        )
    return get_connection()


def _configure_database_target(
    target,
    confirm_production_host=None,
    production_url_env="STUDYHUB_B2_PRODUCTION_DATABASE_URL",
):
    """Select a safe database target without mutating application configuration."""
    global _PRODUCTION_DATABASE_URL, _PRODUCTION_CONNECT_OPTIONS
    _PRODUCTION_DATABASE_URL = None
    _PRODUCTION_CONNECT_OPTIONS = None
    if target == "production":
        confirmed_host = _normalize_host(confirm_production_host)
        if confirmed_host != PRODUCTION_DATABASE_HOST:
            raise ValueError(
                "Production requires --confirm-production-host with the exact configured production hostname."
            )
        if confirmed_host == DEVELOPMENT_DATABASE_HOST:
            raise ValueError(
                "The Development database cannot be selected as Production."
            )
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", production_url_env or ""):
            raise ValueError(
                "--production-url-env must be a valid environment-variable name."
            )
        raw_url = os.environ.get(production_url_env)
        if not raw_url:
            raise ValueError(
                "The selected Production connection environment variable is not set."
            )
        database_url, error = _normalize_postgres_url(raw_url)
        if error:
            raise ValueError(
                "The selected Production connection is not a valid PostgreSQL URL."
            )
        configured_host = _hostname_from_url(database_url)
        if configured_host == DEVELOPMENT_DATABASE_HOST:
            raise ValueError(
                "The configured Production connection points to Development."
            )
        if configured_host != confirmed_host:
            raise ValueError(
                "The configured Production connection hostname does not match the confirmed Production hostname."
            )
        identity = _connection_identity(database_url, production=True)
        if identity["database"] != "neondb" or not identity["user"]:
            raise ValueError(
                "The Production connection must specify the expected neondb database and a database user."
            )
        sslrootcert = certifi.where()
        if not sslrootcert or not os.path.isfile(sslrootcert):
            raise ValueError(
                "The certifi CA bundle is unavailable; Production TLS verification cannot proceed."
            )
        _PRODUCTION_DATABASE_URL = _remove_ssl_url_options(database_url)
        _PRODUCTION_CONNECT_OPTIONS = {
            "sslmode": "verify-full",
            "sslrootcert": sslrootcert,
            "connect_timeout": 30,
        }
        return identity

    if target != "development":
        raise ValueError("Migration target must be development or production.")
    if not Config.USE_POSTGRES or not Config.DATABASE_URL:
        raise ValueError(
            "Development migration requires the locally selected PostgreSQL database."
        )
    if Config.DATABASE_ENVIRONMENT != "development":
        raise ValueError(
            "The configured database is not Development; select a target explicitly."
        )
    configured_host = _hostname_from_url(Config.DATABASE_URL)
    if configured_host == PRODUCTION_DATABASE_HOST:
        raise ValueError(
            "The Production database cannot be used with the Development target."
        )
    identity = _connection_identity(Config.DATABASE_URL)
    if not identity["host"] or not identity["database"]:
        raise ValueError(
            "The selected Development PostgreSQL URL has no valid identity."
        )
    return identity


def _validate_connected_server(connection, expected_identity):
    """Verify TLS endpoint and read-only identity facts from the live server."""
    info = getattr(connection, "info", None)
    actual_host = _normalize_host(getattr(info, "host", None))
    if not expected_identity or actual_host != expected_identity["host"]:
        raise RuntimeError(
            "Connected PostgreSQL host did not match the explicitly selected database; no material query was executed."
        )
    pgconn = getattr(connection, "pgconn", None)
    if expected_identity.get("production") and not getattr(pgconn, "ssl_in_use", False):
        raise RuntimeError(
            "The Production PostgreSQL connection is not using TLS; no material query was executed."
        )

    cursor = None
    try:
        cursor = get_cursor(connection)
        cursor.execute(
            "SELECT current_database() AS database_name, "
            "current_user AS database_user, "
            "inet_server_addr()::text AS server_address, "
            "inet_server_port() AS server_port, "
            "current_setting('server_version_num') AS server_version_num"
        )
        identity = cursor.fetchone() or {}
    finally:
        close_cursor(cursor)

    if (
        identity.get("database_name") != expected_identity["database"]
        or identity.get("database_user") != expected_identity["user"]
        or not identity.get("server_address")
        or not identity.get("server_port")
        or not identity.get("server_version_num")
    ):
        raise RuntimeError(
            "Connected PostgreSQL server identity did not match the selected database; no material query was executed."
        )


def _validate_backup_file(path):
    """Require a readable PostgreSQL archive, not merely a non-empty file."""
    if not path or not os.path.isfile(path) or os.path.getsize(path) <= 0:
        raise ValueError(
            "--backup-file must name an existing, non-empty PostgreSQL archive."
        )
    restore_executable = shutil.which("pg_restore")
    if not restore_executable:
        raise ValueError(
            "pg_restore is required to validate --backup-file; install PostgreSQL client tools."
        )
    try:
        subprocess.run(
            [restore_executable, "--list", os.path.abspath(path)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        raise ValueError(
            "--backup-file is not a valid readable PostgreSQL archive; pg_restore --list failed."
        ) from None


def _record_from_material(material):
    return {
        "id": material.get("id"),
        "file_path": material.get("file_path"),
        "file_name": material.get("file_name"),
        "title": material.get("title"),
        "file_size": material.get("file_size"),
    }


def _canonical_json(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _rollback_backup_digest(payload):
    unsigned_payload = {key: value for key, value in payload.items() if key != "sha256"}
    return hashlib.sha256(_canonical_json(unsigned_payload).encode("utf-8")).hexdigest()


def _require_production_identity(expected_identity):
    if (
        not expected_identity
        or not expected_identity.get("production")
        or expected_identity.get("host") != PRODUCTION_DATABASE_HOST
        or expected_identity.get("database") != "neondb"
    ):
        raise ValueError(
            "Targeted rollback backups are available only for the explicitly selected Production database."
        )


def _validate_rollback_records(records):
    if not isinstance(records, list) or len(records) != len(PRODUCTION_MATERIAL_IDS):
        raise ValueError(
            "Rollback backup must contain exactly the six approved material records."
        )
    validated = []
    for record in records:
        if not isinstance(record, dict) or set(record) != ROLLBACK_RECORD_FIELDS:
            raise ValueError("Rollback backup contains an invalid material record.")
        if type(record["id"]) is not int:
            raise ValueError("Rollback backup contains an invalid material ID.")
        if (
            not isinstance(record["file_path"], str)
            or not record["file_path"]
            or record["file_path"].startswith(B2_PATH_PREFIX)
            or not isinstance(record["file_name"], str)
            or not record["file_name"]
            or not isinstance(record["title"], str)
            or not record["title"]
            or (
                record["file_size"] is not None
                and (type(record["file_size"]) is not int or record["file_size"] < 0)
            )
        ):
            raise ValueError("Rollback backup contains invalid saved field values.")
        validated.append(record)
    if tuple(record["id"] for record in validated) != PRODUCTION_MATERIAL_IDS:
        raise ValueError(
            "Rollback backup material IDs must be exactly 2, 3, 4, 5, 7, and 8 in order."
        )
    return validated


def _load_rollback_backup(path):
    if not path or not os.path.isfile(path):
        raise ValueError("The targeted rollback backup file does not exist.")

    def reject_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Rollback backup contains duplicate JSON keys.")
            result[key] = value
        return result

    try:
        with open(path, "r", encoding="utf-8") as backup_file:
            payload = json.load(
                backup_file,
                object_pairs_hook=reject_duplicate_keys,
            )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("The targeted rollback backup is not valid JSON.") from exc

    if not isinstance(payload, dict) or set(payload) != ROLLBACK_BACKUP_FIELDS:
        raise ValueError(
            "The targeted rollback backup has an invalid format or fields."
        )
    if (
        payload.get("format") != ROLLBACK_BACKUP_FORMAT
        or payload.get("target") != "production"
        or payload.get("database_host") != PRODUCTION_DATABASE_HOST
        or payload.get("database") != "neondb"
        or payload.get("material_ids") != list(PRODUCTION_MATERIAL_IDS)
    ):
        raise ValueError(
            "The targeted rollback backup does not identify the approved Production records."
        )
    _validate_rollback_records(payload.get("records"))
    digest = payload.get("sha256")
    if not isinstance(digest, str) or not hmac.compare_digest(
        digest, _rollback_backup_digest(payload)
    ):
        raise ValueError("The targeted rollback backup SHA-256 integrity check failed.")
    return payload


def _make_rollback_backup_payload(materials):
    records = _validate_rollback_records(
        [
            _record_from_material(material)
            for material in sorted(materials, key=lambda row: row["id"])
        ]
    )
    payload = {
        "format": ROLLBACK_BACKUP_FORMAT,
        "target": "production",
        "database_host": PRODUCTION_DATABASE_HOST,
        "database": "neondb",
        "created_at": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "material_ids": list(PRODUCTION_MATERIAL_IDS),
        "records": records,
    }
    payload["sha256"] = _rollback_backup_digest(payload)
    return payload


def _write_rollback_backup(path, payload):
    absolute_path = os.path.abspath(path)
    parent = os.path.dirname(absolute_path)
    os.makedirs(parent, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(absolute_path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as backup_file:
            backup_file.write(_canonical_json(payload))
            backup_file.write("\n")
            backup_file.flush()
            os.fsync(backup_file.fileno())
    except Exception:
        try:
            os.remove(absolute_path)
        except OSError:
            logger.exception("Could not remove incomplete rollback backup file.")
        raise


def _verify_rollback_backup_against_database(path, expected_identity):
    _require_production_identity(expected_identity)
    payload = _load_rollback_backup(path)
    materials = _get_materials(
        expected_identity,
        material_ids=PRODUCTION_MATERIAL_IDS,
    )
    current_records = [
        _record_from_material(material)
        for material in sorted(materials, key=lambda row: row["id"])
    ]
    if current_records != payload["records"]:
        raise ValueError(
            "Rollback backup does not exactly match the current Production material records."
        )
    return payload


def _create_rollback_backup(path, expected_identity):
    _require_production_identity(expected_identity)
    materials = _get_materials(
        expected_identity,
        material_ids=PRODUCTION_MATERIAL_IDS,
    )
    payload = _make_rollback_backup_payload(materials)
    _write_rollback_backup(path, payload)
    return _verify_rollback_backup_against_database(path, expected_identity)


def _rollback_material_paths(path, expected_identity):
    _require_production_identity(expected_identity)
    payload = _load_rollback_backup(path)
    connection = _get_migration_connection(expected_identity)
    cursor = None
    restored = []
    already_original = []
    try:
        _validate_connected_server(connection, expected_identity)
        cursor = get_cursor(connection)
        placeholders = ", ".join("?" for _ in PRODUCTION_MATERIAL_IDS)
        cursor.execute(
            "SELECT id, file_path, file_name, title, file_size "
            "FROM materials WHERE id IN ({}) ORDER BY id FOR UPDATE".format(
                placeholders
            ),
            PRODUCTION_MATERIAL_IDS,
        )
        rows = cursor.fetchall()
        current_by_id = {row["id"]: row for row in rows}
        if tuple(sorted(current_by_id)) != PRODUCTION_MATERIAL_IDS:
            raise RuntimeError(
                "Production does not contain exactly the approved material records; rollback was not applied."
            )

        for original in payload["records"]:
            current = current_by_id[original["id"]]
            if any(
                current.get(field) != original[field]
                for field in ("file_name", "title", "file_size")
            ):
                raise RuntimeError(
                    "Material {} changed after backup; rollback was not applied.".format(
                        original["id"]
                    )
                )
            if current.get("file_path") == original["file_path"]:
                already_original.append(original["id"])
                continue

            expected_b2_path = "b2://" + _object_key(original)
            if current.get("file_path") != expected_b2_path:
                raise RuntimeError(
                    "Material {} is not in the expected migrated state; rollback was not applied.".format(
                        original["id"]
                    )
                )

            cursor.execute(
                "UPDATE materials SET file_path = ? "
                "WHERE id = ? AND file_path = ? AND file_name = ? AND title = ? "
                "AND file_size IS NOT DISTINCT FROM ?",
                (
                    original["file_path"],
                    original["id"],
                    expected_b2_path,
                    original["file_name"],
                    original["title"],
                    original["file_size"],
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(
                    "Material {} changed during rollback; no rollback changes were committed.".format(
                        original["id"]
                    )
                )
            restored.append(original["id"])

        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        close_cursor(cursor)
        connection.close()
    return {
        "restored_ids": restored,
        "already_original_ids": already_original,
    }


def _file_checksum(path):
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as source:
        while True:
            chunk = source.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _get_materials(expected_identity, material_ids=None):
    connection = _get_migration_connection(expected_identity)
    cursor = None
    try:
        _validate_connected_server(connection, expected_identity)
        cursor = get_cursor(connection)
        query = "SELECT id, title, file_name, file_path, file_size FROM materials"
        if material_ids is not None:
            if tuple(material_ids) != PRODUCTION_MATERIAL_IDS:
                raise ValueError(
                    "Targeted material selection must be exactly the approved Production IDs."
                )
            placeholders = ", ".join("?" for _ in material_ids)
            query += " WHERE id IN ({})".format(placeholders)
            query += " ORDER BY id"
            cursor.execute(query, material_ids)
        else:
            cursor.execute(query + " ORDER BY id")
        return cursor.fetchall()
    finally:
        close_cursor(cursor)
        connection.close()


def _update_file_path(
    material_id,
    old_path,
    new_path,
    expected_identity,
    expected_record=None,
):
    connection = _get_migration_connection(expected_identity)
    cursor = None
    try:
        _validate_connected_server(connection, expected_identity)
        cursor = get_cursor(connection)
        if expected_record is None:
            cursor.execute(
                "UPDATE materials SET file_path = ? " "WHERE id = ? AND file_path = ?",
                (new_path, material_id, old_path),
            )
        else:
            cursor.execute(
                "UPDATE materials SET file_path = ? "
                "WHERE id = ? AND file_path = ? AND file_name = ? AND title = ? "
                "AND file_size IS NOT DISTINCT FROM ?",
                (
                    new_path,
                    material_id,
                    old_path,
                    expected_record["file_name"],
                    expected_record["title"],
                    expected_record["file_size"],
                ),
            )
        if cursor.rowcount != 1:
            connection.rollback()
            raise RuntimeError(
                "Material {} changed during migration; its file path was not updated.".format(
                    material_id
                )
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        close_cursor(cursor)
        connection.close()


def _object_key(material):
    file_name = secure_filename(str(material.get("file_name") or ""))
    if not file_name:
        file_name = "material-{}".format(material["id"])
    return "materials/{}/{}".format(material["id"], file_name)


def _result(material, status, message="", source_path=None, key=None):
    return {
        "material_id": material.get("id"),
        "title": material.get("title"),
        "source": source_path or material.get("file_path"),
        "object_key": key,
        "status": status,
        "message": message,
    }


def _preflight_apply_materials(materials, storage, expected_records_by_id=None):
    preflight = {}
    failures = []
    targeted_apply = expected_records_by_id is not None
    if (
        targeted_apply
        and tuple(
            material.get("id")
            for material in sorted(materials, key=lambda row: row.get("id"))
        )
        != PRODUCTION_MATERIAL_IDS
    ):
        raise RuntimeError(
            "Apply preflight requires exactly the six approved Production material records."
        )

    for material in materials:
        material_id = material.get("id")
        expected_record = (
            expected_records_by_id.get(material_id)
            if expected_records_by_id is not None
            else None
        )
        current_path = str(material.get("file_path") or "")
        if current_path.startswith(B2_PATH_PREFIX):
            continue

        try:
            disk_path, resolved_path = resolve_material_disk_path(material)
        except (OSError, RuntimeError, ValueError):
            failures.append(
                "material {} path could not be resolved".format(material_id)
            )
            continue
        if not disk_path or not os.path.isfile(disk_path):
            failures.append("material {} local file is missing".format(material_id))
            continue
        if targeted_apply and not str(material.get("file_name") or "").lower().endswith(
            ".pdf"
        ):
            failures.append("material {} is not a PDF".format(material_id))
            continue

        try:
            size, sha256 = _file_checksum(disk_path)
        except (OSError, ValueError):
            failures.append("material {} local file is unreadable".format(material_id))
            continue

        expected_size = (
            expected_record.get("file_size")
            if expected_record is not None
            else material.get("file_size")
        )
        if targeted_apply and (type(expected_size) is not int or expected_size <= 0):
            failures.append(
                "material {} has no valid expected file size".format(material_id)
            )
            continue
        if expected_size not in (None, 0) and size != expected_size:
            failures.append(
                "material {} file size does not match its Production record".format(
                    material_id
                )
            )
            continue

        key = _object_key(material)
        preflight[material_id] = {
            "disk_path": disk_path,
            "resolved_path": resolved_path,
            "size": size,
            "sha256": sha256,
            "key": key,
            "existing": False,
        }

    if failures:
        raise RuntimeError(
            "Apply preflight failed; no B2 uploads or database updates were started: "
            + "; ".join(failures)
        )

    object_failures = []
    for material_id, item in preflight.items():
        try:
            existing = storage.object_info(item["key"])
            if existing is not None:
                storage.verify_object(
                    item["key"],
                    item["size"],
                    item["sha256"],
                )
                item["existing"] = True
        except (B2StorageError, OSError, RuntimeError):
            object_failures.append(
                "material {} existing B2 object failed integrity verification".format(
                    material_id
                )
            )
    if object_failures:
        raise RuntimeError(
            "Apply preflight failed; no B2 uploads or database updates were started: "
            + "; ".join(object_failures)
        )
    return preflight


def migrate(
    dry_run=True,
    expected_identity=None,
    material_ids=None,
    expected_records=None,
):
    if expected_identity is None:
        expected_identity = _configure_database_target("development")
    app = Flask(
        "b2_migration",
        static_folder=os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "static"
        ),
    )
    storage = B2Storage()
    results = []

    with app.app_context():
        materials = _get_materials(expected_identity, material_ids=material_ids)
        expected_records_by_id = None
        if expected_records is not None:
            expected_records_by_id = {
                record["id"]: record for record in expected_records
            }
            current_records = [
                _record_from_material(material)
                for material in sorted(materials, key=lambda row: row["id"])
            ]
            if current_records != expected_records:
                raise RuntimeError(
                    "Production material records changed after rollback backup verification; migration was not started."
                )
        preflight_files = None
        if not dry_run:
            if not storage.is_configured:
                raise B2StorageError(
                    "Backblaze B2 is not configured. Set all B2_* environment variables."
                )
            preflight_files = _preflight_apply_materials(
                materials,
                storage,
                expected_records_by_id=expected_records_by_id,
            )
        for material in materials:
            material_id = material.get("id")
            current_path = str(material.get("file_path") or "")
            key = None
            try:
                if current_path.startswith(B2_PATH_PREFIX):
                    key = b2_key(current_path)
                    info = storage.object_info(key)
                    if info is None:
                        results.append(
                            _result(
                                material,
                                "failed",
                                "Database points to a B2 object that does not exist.",
                                key=key,
                            )
                        )
                    elif (
                        material.get("file_size") not in (None, 0)
                        and info.get("ContentLength") != material["file_size"]
                    ):
                        results.append(
                            _result(
                                material,
                                "failed",
                                "B2 object size does not match the database file size.",
                                key=key,
                            )
                        )
                    elif dry_run:
                        results.append(
                            _result(
                                material,
                                "skipped",
                                "Already references an existing B2 object; apply mode will verify its SHA-256.",
                                key=key,
                            )
                        )
                    else:
                        storage.verify_stored_object(
                            key, expected_size=material.get("file_size")
                        )
                        results.append(
                            _result(
                                material,
                                "skipped",
                                "Already references a B2 object whose size and SHA-256 were verified.",
                                key=key,
                            )
                        )
                    continue

                disk_path, resolved_path = resolve_material_disk_path(material)
                if not disk_path or not os.path.isfile(disk_path):
                    results.append(
                        _result(
                            material,
                            "missing",
                            "No local file could be resolved; database record was not changed.",
                            source_path=resolved_path,
                        )
                    )
                    continue

                if dry_run:
                    size, _ = _file_checksum(disk_path)
                    key = _object_key(material)
                    object_info = (
                        storage.object_info(key) if storage.is_configured else None
                    )
                    if object_info and object_info.get("ContentLength") == size:
                        results.append(
                            _result(
                                material,
                                "planned",
                                "Matching-size B2 key already exists; apply mode will verify its SHA-256.",
                                disk_path,
                                key,
                            )
                        )
                    else:
                        results.append(
                            _result(
                                material,
                                "planned",
                                "Local file is ready to upload; local copy will be retained.",
                                disk_path,
                                key,
                            )
                        )
                    continue

                file_plan = preflight_files.get(material_id)
                if file_plan is None:
                    raise RuntimeError(
                        "Material {} did not pass apply preflight.".format(material_id)
                    )
                disk_path = file_plan["disk_path"]
                resolved_path = file_plan["resolved_path"]
                size = file_plan["size"]
                sha256 = file_plan["sha256"]
                key = file_plan["key"]
                if not file_plan["existing"]:
                    with open(disk_path, "rb") as source:
                        uploaded_size, uploaded_sha256 = storage.upload_fileobj(
                            source,
                            key,
                            content_type=mimetypes.guess_type(
                                material.get("file_name") or ""
                            )[0],
                        )
                    if uploaded_size != size or uploaded_sha256 != sha256:
                        raise B2StorageError(
                            "Uploaded B2 content does not match its preflight size and SHA-256."
                        )

                new_path = B2_PATH_PREFIX + key
                _update_file_path(
                    material_id,
                    current_path,
                    new_path,
                    expected_identity,
                    expected_record=(
                        expected_records_by_id.get(material_id)
                        if expected_records_by_id is not None
                        else None
                    ),
                )
                results.append(
                    _result(
                        material,
                        "success",
                        "Uploaded and verified; database file_path updated. Local file retained.",
                        disk_path,
                        key,
                    )
                )
            except (B2StorageError, OSError, RuntimeError) as exc:
                logger.exception("Could not migrate material %s.", material_id)
                results.append(_result(material, "failed", str(exc), key=key))
            except Exception as exc:
                logger.exception(
                    "Unexpected migration error for material %s.", material_id
                )
                results.append(
                    _result(
                        material,
                        "failed",
                        "Unexpected {}: {}".format(type(exc).__name__, exc),
                        key=key,
                    )
                )
    return results


def _write_report(path, dry_run, results):
    counts = {
        status: sum(1 for result in results if result["status"] == status)
        for status in ("success", "failed", "missing", "skipped", "planned")
    }
    report = {
        "created_at": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "mode": "dry-run" if dry_run else "apply",
        "counts": counts,
        "results": results,
    }
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as report_file:
        json.dump(report, report_file, indent=2, ensure_ascii=False)
        report_file.write("\n")
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Migrate material files referenced by the configured database to B2. "
            "Dry-run is the default; no local files are removed."
        )
    )
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument(
        "--apply",
        action="store_true",
        help="Upload, verify, then update paths (requires a Production rollback backup).",
    )
    operation.add_argument(
        "--dry-run",
        action="store_true",
        help="List files and planned object keys without uploading or updating records (default).",
    )
    operation.add_argument(
        "--check-storage",
        action="store_true",
        help="Check B2 bucket access using a read-only HeadBucket request.",
    )
    operation.add_argument(
        "--create-rollback-backup",
        metavar="PATH",
        help="Create and Production-verify a targeted JSON rollback backup for the six approved material IDs.",
    )
    operation.add_argument(
        "--verify-rollback-backup",
        metavar="PATH",
        help="Verify a targeted rollback backup against the current Production material records.",
    )
    operation.add_argument(
        "--rollback",
        action="store_true",
        help="Restore original file_path values from --rollback-backup after guarded Production checks.",
    )
    parser.add_argument(
        "--target",
        choices=("development", "production"),
        default="development",
        help="Database target; Development is default, Production requires explicit confirmation.",
    )
    parser.add_argument(
        "--confirm-production-host",
        help="For --target production, repeat the exact Production PostgreSQL hostname.",
    )
    parser.add_argument(
        "--production-url-env",
        default="STUDYHUB_B2_PRODUCTION_DATABASE_URL",
        help="Environment variable containing the Production PostgreSQL URL (never printed).",
    )
    parser.add_argument(
        "--backup-file",
        help="Path to a PostgreSQL archive readable by pg_restore; optional if --rollback-backup is supplied.",
    )
    parser.add_argument(
        "--rollback-backup",
        help="Targeted JSON rollback backup; may satisfy --apply instead of --backup-file, or be used with --rollback.",
    )
    parser.add_argument(
        "--report",
        help="JSON report destination (defaults to a timestamped file in reports/).",
    )
    args = parser.parse_args(argv)

    if args.check_storage:
        return _check_storage()

    if args.target != "production" and args.confirm_production_host:
        parser.error(
            "--confirm-production-host is valid only with --target production."
        )
    try:
        expected_identity = _configure_database_target(
            args.target,
            confirm_production_host=args.confirm_production_host,
            production_url_env=args.production_url_env,
        )
    except ValueError as exc:
        parser.error(str(exc))

    targeted_operation = (
        args.create_rollback_backup
        or args.verify_rollback_backup
        or args.rollback
        or (args.apply and args.rollback_backup)
    )
    if targeted_operation and args.target != "production":
        parser.error("Targeted rollback operations require --target production.")
    if args.backup_file and not args.apply:
        parser.error("--backup-file is valid only with --apply.")
    if args.rollback_backup and not (args.apply or args.rollback):
        parser.error("--rollback-backup is valid only with --apply or --rollback.")
    if args.create_rollback_backup:
        if args.backup_file or args.rollback_backup:
            parser.error(
                "--create-rollback-backup cannot be combined with backup input options."
            )
        try:
            _create_rollback_backup(args.create_rollback_backup, expected_identity)
        except (OSError, ValueError, RuntimeError) as exc:
            parser.error(str(exc))
        print(
            "WARNING: This is a targeted rollback backup for material IDs "
            "2, 3, 4, 5, 7, and 8; it is NOT a full database backup."
        )
        print(
            "Created and verified targeted rollback backup: {}".format(
                os.path.abspath(args.create_rollback_backup)
            )
        )
        return 0

    if args.verify_rollback_backup:
        if args.backup_file or args.rollback_backup:
            parser.error(
                "--verify-rollback-backup cannot be combined with backup input options."
            )
        try:
            _verify_rollback_backup_against_database(
                args.verify_rollback_backup,
                expected_identity,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            parser.error(str(exc))
        print(
            "Verified targeted rollback backup against Production material IDs "
            "2, 3, 4, 5, 7, and 8. This is NOT a full database backup."
        )
        return 0

    if args.rollback:
        if not args.rollback_backup or args.backup_file:
            parser.error(
                "--rollback requires --rollback-backup and cannot use --backup-file."
            )
        try:
            result = _rollback_material_paths(
                args.rollback_backup,
                expected_identity,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            parser.error(str(exc))
        print(
            "Rollback restored file_path for {} material(s); {} were already at "
            "their original path. No records or files were deleted.".format(
                len(result["restored_ids"]),
                len(result["already_original_ids"]),
            )
        )
        return 0

    verified_rollback = None
    if args.apply:
        if not Config.USE_POSTGRES:
            parser.error("This migration must target PostgreSQL.")
        if not args.rollback_backup:
            parser.error(
                "--apply requires a verified --rollback-backup for the six approved Production records."
            )
        if args.backup_file:
            try:
                _validate_backup_file(args.backup_file)
            except ValueError as exc:
                parser.error(str(exc))
        try:
            verified_rollback = _verify_rollback_backup_against_database(
                args.rollback_backup,
                expected_identity,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            parser.error(str(exc))
        if not B2Storage().is_configured:
            parser.error(
                "All B2_* environment variables must be configured before --apply."
            )
        if verified_rollback:
            print(
                "WARNING: Using a targeted rollback backup for material IDs "
                "2, 3, 4, 5, 7, and 8; this is NOT a full database backup."
            )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    report_path = args.report or os.path.join(
        "reports",
        "b2-migration-{}.json".format(datetime.now().strftime("%Y%m%d-%H%M%S")),
    )
    results = migrate(
        dry_run=not args.apply,
        expected_identity=expected_identity,
        material_ids=(PRODUCTION_MATERIAL_IDS if args.apply else None),
        expected_records=(verified_rollback["records"] if verified_rollback else None),
    )
    counts = _write_report(report_path, not args.apply, results)
    print("Migration report: {}".format(os.path.abspath(report_path)))
    print(
        "Summary: {success} successful, {failed} failed, {missing} missing, "
        "{skipped} skipped, {planned} planned.".format(**counts)
    )
    if args.apply and not counts["failed"] and not counts["missing"]:
        print(
            "All database-referenced local material files were migrated and verified."
        )
        return 0
    if args.apply:
        print(
            "Migration is incomplete. Review the report and rerun after resolving issues."
        )
        return 1
    return 1 if counts["failed"] or counts["missing"] else 0


if __name__ == "__main__":
    sys.exit(main())
