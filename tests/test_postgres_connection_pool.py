from types import SimpleNamespace

import psycopg

import utils.database as database


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.closed = False

    def fetchone(self):
        if not self.connection.alive:
            raise psycopg.OperationalError('connection terminated')
        return (1,)

    def close(self):
        self.closed = True


class FakePostgresConnection:
    def __init__(self, *, rollback_fails=False):
        self.closed = False
        self.alive = True
        self.rollback_fails = rollback_fails
        self.rollback_calls = 0
        self.queries = []
        self.pgconn = SimpleNamespace(
            status=psycopg.pq.ConnStatus.OK,
            transaction_status=psycopg.pq.TransactionStatus.IDLE,
        )

    def execute(self, query):
        self.queries.append(query)
        if self.closed or not self.alive:
            raise psycopg.OperationalError('connection terminated')
        self.pgconn.transaction_status = psycopg.pq.TransactionStatus.INTRANS
        return FakeCursor(self)

    def rollback(self):
        self.rollback_calls += 1
        if self.rollback_fails:
            raise psycopg.OperationalError('rollback failed')
        self.pgconn.transaction_status = psycopg.pq.TransactionStatus.IDLE

    def close(self):
        self.closed = True
        self.pgconn.status = psycopg.pq.ConnStatus.BAD


def _pool_with_empty_thread_local(monkeypatch):
    pool = database.PostgreSQLConnectionPool()
    monkeypatch.setattr(database._tls, 'pg_pool', [], raising=False)
    return pool


def test_select_connection_is_rolled_back_before_returning_to_pool(monkeypatch):
    pool = _pool_with_empty_thread_local(monkeypatch)
    connection = FakePostgresConnection()
    connection.execute('SELECT 1')
    wrapped = database.PooledConnection(connection, pool)

    wrapped.close()
    wrapped.close()

    assert connection.rollback_calls == 1
    assert (
        connection.pgconn.transaction_status
        == psycopg.pq.TransactionStatus.IDLE
    )
    assert database._tls.pg_pool == [connection]
    assert wrapped.closed


def test_failed_transaction_is_reset_before_connection_reuse(monkeypatch):
    pool = _pool_with_empty_thread_local(monkeypatch)
    connection = FakePostgresConnection()
    connection.pgconn.transaction_status = psycopg.pq.TransactionStatus.INERROR

    pool.put(connection)

    assert connection.rollback_calls == 1
    assert not connection.closed
    assert (
        connection.pgconn.transaction_status
        == psycopg.pq.TransactionStatus.IDLE
    )
    assert database._tls.pg_pool == [connection]


def test_connection_is_discarded_when_rollback_fails(monkeypatch):
    pool = _pool_with_empty_thread_local(monkeypatch)
    connection = FakePostgresConnection(rollback_fails=True)
    connection.pgconn.transaction_status = psycopg.pq.TransactionStatus.INERROR

    pool.put(connection)

    assert connection.rollback_calls == 1
    assert connection.closed
    assert database._tls.pg_pool == []


def test_neon_terminated_connection_is_discarded_on_checkout(monkeypatch):
    pool = _pool_with_empty_thread_local(monkeypatch)
    stale = FakePostgresConnection()
    stale.alive = False
    database._tls.pg_pool.append(stale)
    fresh = FakePostgresConnection()
    monkeypatch.setattr(
        pool, '_create_connection', lambda: fresh,
    )

    result = pool.get()

    assert result is fresh
    assert stale.closed
    assert not fresh.closed
    assert fresh.queries == []


def test_checked_out_connection_is_idle_after_health_check(monkeypatch):
    pool = _pool_with_empty_thread_local(monkeypatch)
    connection = FakePostgresConnection()
    database._tls.pg_pool.append(connection)

    result = pool.get()

    assert result is connection
    assert connection.queries == ['SELECT 1']
    assert connection.rollback_calls == 1
    assert (
        connection.pgconn.transaction_status
        == psycopg.pq.TransactionStatus.IDLE
    )
