"""SQLite schema definitions for tiered memory persistence."""

from __future__ import annotations

import sqlite3


SCHEMA_VERSION = 5

CREATE_ITEMS_TABLE = """
CREATE TABLE IF NOT EXISTS memory_items (
    id TEXT PRIMARY KEY,
    tier TEXT NOT NULL,
    namespace TEXT NOT NULL DEFAULT 'global',
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0.5,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_retrieved_at TEXT,
    expires_at TEXT,
    decay_half_life_days INTEGER,
    provenance TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active',
    token_count INTEGER NOT NULL DEFAULT 0,
    metadata TEXT NOT NULL DEFAULT '{}'
);
"""

CREATE_FTS_TABLE = """
CREATE VIRTUAL TABLE IF NOT EXISTS memory_items_fts USING fts5(
    id,
    title,
    content,
    tags,
    namespace,
    content='memory_items',
    content_rowid='rowid'
);
"""

CREATE_FTS_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS memory_items_ai AFTER INSERT ON memory_items BEGIN
    INSERT INTO memory_items_fts(rowid, id, title, content, tags, namespace)
    VALUES (NEW.rowid, NEW.id, NEW.title, NEW.content, NEW.tags, NEW.namespace);
END;

CREATE TRIGGER IF NOT EXISTS memory_items_ad AFTER DELETE ON memory_items BEGIN
    INSERT INTO memory_items_fts(memory_items_fts, rowid, id, title, content, tags, namespace)
    VALUES('delete', OLD.rowid, OLD.id, OLD.title, OLD.content, OLD.tags, OLD.namespace);
END;

CREATE TRIGGER IF NOT EXISTS memory_items_au AFTER UPDATE ON memory_items BEGIN
    INSERT INTO memory_items_fts(memory_items_fts, rowid, id, title, content, tags, namespace)
    VALUES('delete', OLD.rowid, OLD.id, OLD.title, OLD.content, OLD.tags, OLD.namespace);
    INSERT INTO memory_items_fts(rowid, id, title, content, tags, namespace)
    VALUES (NEW.rowid, NEW.id, NEW.title, NEW.content, NEW.tags, NEW.namespace);
END;
"""

CREATE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_items_tier ON memory_items(tier);
CREATE INDEX IF NOT EXISTS idx_items_namespace ON memory_items(namespace);
CREATE INDEX IF NOT EXISTS idx_items_status ON memory_items(status);
CREATE INDEX IF NOT EXISTS idx_items_expires ON memory_items(expires_at);
CREATE INDEX IF NOT EXISTS idx_items_created ON memory_items(created_at);
"""

CREATE_META_TABLE = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

CREATE_COMPACTION_JOURNAL_TABLE = """
CREATE TABLE IF NOT EXISTS compaction_journal (
    compaction_id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    source_item_ids TEXT NOT NULL DEFAULT '[]',
    archival_item_id TEXT,
    status TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
);
"""

CREATE_COMPACTION_JOURNAL_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_compaction_journal_status ON compaction_journal(status);
CREATE INDEX IF NOT EXISTS idx_compaction_journal_namespace ON compaction_journal(namespace);
"""

CREATE_COMMIT_UNDO_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS commit_undo_log (
    commit_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    tier TEXT NOT NULL,
    pre_state TEXT NOT NULL,
    operation TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (commit_id, item_id)
);
"""

CREATE_COMMIT_UNDO_LOG_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_undo_commit ON commit_undo_log(commit_id);
"""

CREATE_ENTITIES_TABLE = """
CREATE TABLE IF NOT EXISTS memory_entities (
    item_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_value TEXT NOT NULL,
    entity_normalized TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.8,
    PRIMARY KEY (item_id, entity_type, entity_normalized)
);
"""

CREATE_ENTITIES_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_entities_normalized ON memory_entities(entity_normalized);
CREATE INDEX IF NOT EXISTS idx_entities_type ON memory_entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_item ON memory_entities(item_id);
"""

CREATE_CLAIMS_TABLE = """
CREATE TABLE IF NOT EXISTS memory_claims (
    item_id TEXT NOT NULL,
    entity_normalized TEXT NOT NULL,
    attribute TEXT NOT NULL,
    value TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_until TEXT,
    superseded_by TEXT,
    PRIMARY KEY (item_id, entity_normalized, attribute)
);
"""

CREATE_CLAIMS_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_claims_entity_attr ON memory_claims(entity_normalized, attribute);
CREATE INDEX IF NOT EXISTS idx_claims_valid ON memory_claims(valid_until);
CREATE INDEX IF NOT EXISTS idx_claims_item ON memory_claims(item_id);
"""

BASE_SCHEMA_SCRIPTS = (
    CREATE_META_TABLE,
    CREATE_ITEMS_TABLE,
    CREATE_COMPACTION_JOURNAL_TABLE,
    CREATE_COMMIT_UNDO_LOG_TABLE,
    CREATE_ENTITIES_TABLE,
    CREATE_CLAIMS_TABLE,
    CREATE_FTS_TABLE,
    CREATE_FTS_TRIGGERS,
    CREATE_INDEXES,
    CREATE_COMPACTION_JOURNAL_INDEXES,
    CREATE_COMMIT_UNDO_LOG_INDEXES,
    CREATE_ENTITIES_INDEXES,
    CREATE_CLAIMS_INDEXES,
)


def apply_base_schema(cursor: sqlite3.Cursor) -> None:
    """Create memory tables, indexes, FTS tables, and triggers."""
    for script in BASE_SCHEMA_SCRIPTS:
        cursor.executescript(script)


def record_schema_version(cursor: sqlite3.Cursor, version: int = SCHEMA_VERSION) -> None:
    """Record the active memory store schema version."""
    cursor.execute(
        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)",
        ("schema_version", str(version)),
    )
