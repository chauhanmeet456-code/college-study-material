import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import Config
Config.USE_POSTGRES = False
Config.USE_SQLITE = True
from utils.database import get_connection

conn = get_connection()
row = conn.execute(
    "SELECT sql FROM sqlite_master WHERE type='table' AND name='pending_notifications'"
).fetchone()
if row:
    print("SCHEMA:")
    print(row[0])
else:
    print("Table not found")
conn.close()
