"""Diagnostic test for PostgresCursorWrapper and session flow."""
import sys
import os
sys.path.insert(0, os.getcwd())

class MockPsycopgCursor:
    """A mock that mimics psycopg3's cursor behavior."""
    def __init__(self):
        self.queries = []
        self.rows = []
        self._idx = 0

    def execute(self, query, params=None):
        self.queries.append((query, params))
        return self

    def fetchone(self):
        if self._idx < len(self.rows):
            r = self.rows[self._idx]
            self._idx += 1
            return r
        return None

    def fetchall(self):
        result = self.rows[self._idx:]
        self._idx = len(self.rows)
        return result

    def close(self):
        pass

    @property
    def rowcount(self):
        return len(self.rows)


from utils.database import PostgresCursorWrapper, execute_query

# Test 1: INSERT with RETURNING id
print("=== Test 1: INSERT ===")
mock = MockPsycopgCursor()
mock.rows = [{"id": 42}]
wrapper = PostgresCursorWrapper(mock)
wrapper.execute(
    "INSERT INTO user_sessions (user_id, session_token_hash, status) VALUES (?, ?, 'active')",
    (1, "hash123"),
)
q, p = mock.queries[-1]
print(f"Query: {q}")
print(f"Params: {p}")
print(f"lastrowid: {wrapper.lastrowid}")
assert "%s" in q, "FAIL: ? not converted to %s"
assert "RETURNING id" in q, "FAIL: RETURNING id not added"
assert wrapper.lastrowid == 42, f"FAIL: lastrowid should be 42, got {wrapper.lastrowid}"
print("PASSED\n")

# Test 2: SELECT with ? placeholder
print("=== Test 2: SELECT ===")
mock = MockPsycopgCursor()
mock.rows = [{"id": 1, "status": "active", "session_token_hash": "hash123"}]
wrapper = PostgresCursorWrapper(mock)
wrapper.execute(
    "SELECT * FROM user_sessions WHERE session_token_hash = ?",
    ("hash123",),
)
row = wrapper.fetchone()
q, p = mock.queries[-1]
print(f"Query: {q}")
print(f"Params: {p}")
print(f"Result: {row}")
assert "%s" in q, "FAIL: ? not converted to %s"
assert row == {"id": 1, "status": "active", "session_token_hash": "hash123"}, "FAIL: row mismatch"
print("PASSED\n")

# Test 3: execute_query with empty params tuple
print("=== Test 3: execute_query with empty params ===")
mock2 = MockPsycopgCursor()
mock2.rows = [{"id": 1}]
try:
    # execute_query checks 'if params:' which is False for empty tuple
    cursor = execute_query(mock2, "SELECT * FROM user_sessions WHERE user_id = ?", ())
    q, p = mock2.queries[-1]
    print(f"Query: {q}")
    print(f"Params: {p}")
    if "%s" not in q:
        print("WARNING: ? was NOT converted to %s (empty params bug)")
except Exception as e:
    print(f"Error: {e}")

# Test 4: Verify hash_session_token consistency
print("\n=== Test 4: hash_session_token consistency ===")
from models.session import generate_session_token, hash_session_token
token = generate_session_token()
h1 = hash_session_token(token)
h2 = hash_session_token(token)
print(f"Token: {token}")
print(f"Hash 1: {h1}")
print(f"Hash 2: {h2}")
assert h1 == h2, "FAIL: Hash mismatch"
print(f"Hashes match: {h1 == h2}")
print("PASSED\n")

# Test 5: Check _parse_datetime with naive vs aware datetime
print("=== Test 5: _parse_datetime ===")
from app import _parse_datetime
from datetime import datetime, timezone

naive = datetime(2026, 10, 27, 6, 17, 24, 123456)
aware = datetime(2026, 10, 27, 6, 17, 24, 123456, tzinfo=timezone.utc)

naive_parsed = _parse_datetime(naive)
aware_parsed = _parse_datetime(aware)

print(f"Naive datetime str: {str(naive)}")
print(f"Naive parsed: {naive_parsed}")
print(f"Aware datetime str: {str(aware)}")
print(f"Aware parsed: {aware_parsed}")

if aware_parsed is None:
    print("WARNING: Aware datetime NOT parsed (returns None)")
print("PASSED\n")

print("=== All diagnostic tests complete ===")
