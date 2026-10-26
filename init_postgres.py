"""Initialize the PostgreSQL database schema for StudyHub.

This script creates the database schema using PostgreSQL-compatible DDL.
It does NOT transfer any data - use migrate_to_postgres.py for that.

Usage:
    python init_postgres.py
"""

import os
import sys
import psycopg
from config import Config


SCHEMA_SQL = """
-- ==========================================================
-- Core tables
-- ==========================================================

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    profile_image VARCHAR(255) DEFAULT 'default.png',
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login TIMESTAMP DEFAULT NULL,
    deleted_by_user INTEGER DEFAULT 0,
    deletion_requested_at TIMESTAMP DEFAULT NULL,
    deletion_processed TIMESTAMP DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS admins (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    profile_image VARCHAR(255) DEFAULT 'current.png',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login TIMESTAMP DEFAULT NULL,
    reset_token VARCHAR(255) DEFAULT NULL,
    reset_token_expires_at TIMESTAMP DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS categories (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO categories (name, description) VALUES
    ('Notes', 'Class notes and lecture notes'),
    ('Assignment', 'Assignments and homework'),
    ('Book', 'Reference books and textbooks'),
    ('Practical', 'Lab practicals and experiments'),
    ('Question Paper', 'Previous year question papers'),
    ('Important Question', 'Important questions for exams'),
    ('Syllabus', 'Subject syllabus'),
    ('Other', 'Other study materials')
ON CONFLICT (name) DO NOTHING;

CREATE TABLE IF NOT EXISTS subjects (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    code VARCHAR(50) UNIQUE NOT NULL,
    description TEXT,
    icon VARCHAR(50) DEFAULT 'fa-book',
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS materials (
    id SERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    subject_id INTEGER NOT NULL,
    category_id INTEGER NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    file_path VARCHAR(500) NOT NULL,
    file_type VARCHAR(50),
    file_size INTEGER DEFAULT 0,
    uploaded_by INTEGER DEFAULT NULL,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    download_count INTEGER DEFAULT 0,
    view_count INTEGER DEFAULT 0,
    is_featured INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE,
    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE,
    FOREIGN KEY (uploaded_by) REFERENCES admins(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS favorites (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    material_id INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE CASCADE,
    UNIQUE(user_id, material_id)
);

CREATE TABLE IF NOT EXISTS contact_messages (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(100) NOT NULL,
    subject VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,
    is_read INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS announcements (
    id SERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS guest_views (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(255) NOT NULL,
    material_id INTEGER NOT NULL,
    viewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS guest_downloads (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(255) NOT NULL,
    material_id INTEGER NOT NULL,
    downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS settings (
    id SERIAL PRIMARY KEY,
    key VARCHAR(100) UNIQUE NOT NULL,
    value TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO settings (key, value) VALUES
    ('site_name', 'StudyHub'),
    ('site_description', 'Your one-stop destination for all 3rd year college study materials.'),
    ('contact_email', 'support@studyhub.edu'),
    ('support_email', 'support@studyhub.edu'),
    ('footer_text', 'StudyHub. All rights reserved.'),
    ('organization_name', 'StudyHub'),
    ('address', 'Sathvara Vidhyarthi Bhavan Vadaj Ahmedabad'),
    ('phone', '+91 79906 82016'),
    ('latitude', '23.0225'),
    ('longitude', '72.5714'),
    ('office_hours', '9:00 AM - 6:00 PM, Monday to Friday')
ON CONFLICT (key) DO NOTHING;

-- ==========================================================
-- Login session management and security audit tables
-- ==========================================================

CREATE TABLE IF NOT EXISTS user_sessions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    session_token_hash VARCHAR(64) NOT NULL,
    device_name VARCHAR(255) DEFAULT NULL,
    browser_name VARCHAR(100) DEFAULT NULL,
    operating_system VARCHAR(100) DEFAULT NULL,
    ip_address VARCHAR(45) DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_activity_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP DEFAULT NULL,
    revoked_at TIMESTAMP DEFAULT NULL,
    logout_at TIMESTAMP DEFAULT NULL,
    status VARCHAR(20) DEFAULT 'active',
    termination_reason TEXT DEFAULT NULL,
    ended_by_admin INTEGER DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_user_sessions_user_id ON user_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_user_sessions_token ON user_sessions(session_token_hash);
CREATE INDEX IF NOT EXISTS idx_user_sessions_status ON user_sessions(status);
CREATE INDEX IF NOT EXISTS idx_user_sessions_expires_at ON user_sessions(expires_at);

CREATE TABLE IF NOT EXISTS pending_notifications (
    id SERIAL PRIMARY KEY,
    recipient_email VARCHAR(255) NOT NULL,
    subject VARCHAR(255) NOT NULL,
    body TEXT NOT NULL,
    purpose VARCHAR(50),
    related_id INTEGER,
    dedupe_key VARCHAR(255),
    attempt INTEGER DEFAULT 0,
    status VARCHAR(20) DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_attempt_at TIMESTAMP DEFAULT NULL
);
CREATE INDEX IF NOT EXISTS idx_pending_notif_dedupe ON pending_notifications(dedupe_key, status);
CREATE INDEX IF NOT EXISTS idx_pending_notif_status ON pending_notifications(status, created_at);

CREATE TABLE IF NOT EXISTS login_history (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    device_description VARCHAR(255) DEFAULT NULL,
    browser_name VARCHAR(100) DEFAULT NULL,
    operating_system VARCHAR(100) DEFAULT NULL,
    ip_address VARCHAR(45) DEFAULT NULL,
    login_method VARCHAR(50) DEFAULT NULL,
    result VARCHAR(20) DEFAULT 'info',
    failure_reason TEXT DEFAULT NULL,
    related_session_id INTEGER DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_login_history_user_id ON login_history(user_id);
CREATE INDEX IF NOT EXISTS idx_login_history_created_at ON login_history(created_at);
CREATE INDEX IF NOT EXISTS idx_login_history_event_type ON login_history(event_type);
CREATE INDEX IF NOT EXISTS idx_login_history_result ON login_history(result);

CREATE TABLE IF NOT EXISTS failed_login_attempts (
    id SERIAL PRIMARY KEY,
    user_id INTEGER DEFAULT NULL,
    email_masked VARCHAR(255) DEFAULT NULL,
    ip_address VARCHAR(45) DEFAULT NULL,
    device_description VARCHAR(255) DEFAULT NULL,
    failure_category VARCHAR(50) DEFAULT 'invalid_credentials',
    rate_limited INTEGER DEFAULT 0,
    attempted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_failed_attempts_email ON failed_login_attempts(email_masked);
CREATE INDEX IF NOT EXISTS idx_failed_attempts_ip ON failed_login_attempts(ip_address);
CREATE INDEX IF NOT EXISTS idx_failed_attempts_time ON failed_login_attempts(attempted_at);

CREATE TABLE IF NOT EXISTS admin_audit_log (
    id SERIAL PRIMARY KEY,
    admin_id INTEGER DEFAULT NULL,
    action VARCHAR(100) NOT NULL,
    target_type VARCHAR(50) DEFAULT NULL,
    target_id INTEGER DEFAULT NULL,
    details TEXT DEFAULT NULL,
    ip_address VARCHAR(45) DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (admin_id) REFERENCES admins(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_admin_audit_created_at ON admin_audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_admin_audit_admin_id ON admin_audit_log(admin_id);

-- ==========================================================
-- Functions to replace SQLite triggers
-- ==========================================================

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create triggers (PostgreSQL does not support CREATE TRIGGER IF NOT EXISTS)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'update_subjects_updated_at'
    ) THEN
        CREATE TRIGGER update_subjects_updated_at
            AFTER UPDATE ON subjects
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column();
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'update_materials_updated_at'
    ) THEN
        CREATE TRIGGER update_materials_updated_at
            AFTER UPDATE ON materials
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column();
    END IF;
END;
$$;
"""


def init_postgres():
    if not Config.USE_POSTGRES:
        print("DATABASE_URL is not set in .env - exiting")
        return

    conn = psycopg.connect(Config.DATABASE_URL, autocommit=True)
    cursor = conn.cursor()

    cursor.execute(SCHEMA_SQL)
    cursor.close()
    conn.close()
    print("PostgreSQL database schema created successfully!")


if __name__ == '__main__':
    init_postgres()
