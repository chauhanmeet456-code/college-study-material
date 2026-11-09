from config import _load_project_env, resolve_database_configuration


LOCAL_URL = 'postgresql://postgres@localhost:5432/college_study'
PRODUCTION_URL = (
    'postgresql://database-user:database-password@'
    'example.neon.tech/studyhub?sslmode=require'
)


def test_local_dev_url_takes_priority_over_inherited_production_url():
    selected, error, environment = resolve_database_configuration({
        'DEV_DATABASE_URL': LOCAL_URL,
        'DATABASE_URL': PRODUCTION_URL,
    })

    assert selected == LOCAL_URL
    assert error is None
    assert environment == 'development'


def test_dev_database_url_takes_priority_over_sqlite_flag():
    selected, error, environment = resolve_database_configuration({
        'DEV_DATABASE_URL': LOCAL_URL,
        'USE_SQLITE': 'true',
        'DATABASE_BACKEND': 'postgresql',
    })

    assert selected == LOCAL_URL
    assert error is None
    assert environment == 'development'


def test_explicit_postgres_backend_requires_development_url():
    selected, error, environment = resolve_database_configuration({
        'DATABASE_BACKEND': 'postgresql',
        'USE_SQLITE': 'true',
    })

    assert selected is None
    assert 'explicitly selected' in error
    assert 'DEV_DATABASE_URL' in error
    assert environment == 'development'


def test_project_env_fills_blank_values_without_overriding_environment(tmp_path):
    env_file = tmp_path / '.env'
    env_file.write_text(
        'DEV_DATABASE_URL=postgresql://local.example/db\n'
        'DATABASE_URL=postgresql://production.example/db\n',
        encoding='utf-8',
    )
    environ = {
        'DEV_DATABASE_URL': '',
        'DATABASE_URL': 'postgresql://managed-environment.example/db',
    }

    _load_project_env(env_file, environ)

    assert environ['DEV_DATABASE_URL'] == 'postgresql://local.example/db'
    assert environ['DATABASE_URL'] == 'postgresql://managed-environment.example/db'


def test_project_env_is_not_loaded_on_render(tmp_path):
    env_file = tmp_path / '.env'
    env_file.write_text('DATABASE_URL=local-file-value\n', encoding='utf-8')
    environ = {'RENDER': 'true'}

    _load_project_env(env_file, environ)

    assert 'DATABASE_URL' not in environ


def test_local_environment_never_falls_back_to_remote_production_url():
    selected, error, environment = resolve_database_configuration({
        'DATABASE_URL': PRODUCTION_URL,
    })

    assert selected is None
    assert 'DEV_DATABASE_URL' in error
    assert 'intentionally ignored' in error
    assert environment == 'development'


def test_local_legacy_mysql_configuration_is_preserved():
    selected, error, environment = resolve_database_configuration({
        'DATABASE_URL': PRODUCTION_URL,
        'DB_HOST': 'localhost',
        'DB_NAME': 'college_study',
        'DB_USER': 'studyhub',
    })

    assert selected is None
    assert error is None
    assert environment == 'development'


def test_render_uses_production_url_even_when_dev_url_is_present():
    selected, error, environment = resolve_database_configuration({
        'RENDER': 'true',
        'DEV_DATABASE_URL': LOCAL_URL,
        'DATABASE_URL': PRODUCTION_URL,
    })

    assert selected == PRODUCTION_URL
    assert error is None
    assert environment == 'render'


def test_render_requires_production_database_url():
    selected, error, environment = resolve_database_configuration({
        'RENDER_SERVICE_ID': 'srv-example',
        'DEV_DATABASE_URL': LOCAL_URL,
    })

    assert selected is None
    assert error == 'Production PostgreSQL DATABASE_URL is not configured.'
    assert environment == 'render'


def test_render_does_not_fall_back_to_sqlite_when_database_is_missing():
    selected, error, environment = resolve_database_configuration({
        'RENDER': 'true',
        'USE_SQLITE': 'true',
    })

    assert selected is None
    assert error == 'Production PostgreSQL DATABASE_URL is not configured.'
    assert environment == 'render'


def test_invalid_urls_are_rejected_without_echoing_credentials():
    secret_url = 'postgresql://private-user:very-secret@localhost:not-a-port/db'
    selected, error, environment = resolve_database_configuration({
        'DEV_DATABASE_URL': secret_url,
    })

    assert selected is None
    assert error == 'PostgreSQL URL is malformed.'
    assert 'very-secret' not in error
    assert environment == 'development'


def test_invalid_ssl_mode_is_rejected():
    selected, error, _ = resolve_database_configuration({
        'RENDER': 'true',
        'DATABASE_URL': PRODUCTION_URL.replace(
            'sslmode=require', 'sslmode=insecure'
        ),
    })

    assert selected is None
    assert error == 'PostgreSQL URL contains an invalid SSL mode.'


def test_explicit_sqlite_does_not_require_a_postgres_url():
    selected, error, environment = resolve_database_configuration({
        'USE_SQLITE': 'true',
    })

    assert selected is None
    assert error is None
    assert environment == 'development'


def test_dev_database_url_precedes_explicit_sqlite_without_backend_override():
    selected, error, environment = resolve_database_configuration({
        'DEV_DATABASE_URL': LOCAL_URL,
        'USE_SQLITE': 'true',
    })

    assert selected == LOCAL_URL
    assert error is None
    assert environment == 'development'


def test_explicit_local_sqlite_ignores_inherited_remote_database_url():
    selected, error, environment = resolve_database_configuration({
        'USE_SQLITE': 'true',
        'DATABASE_URL': PRODUCTION_URL,
    })

    assert selected is None
    assert error is None
    assert environment == 'development'
