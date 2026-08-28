#!/usr/bin/env python3
"""One-off migration: add search_type column to eval_runs table.

Run once against the target database:
    python scripts/migrate_add_eval_runs_search_type.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from kb_mcp.kb.database import get_engine, get_database_url

engine = get_engine()
database_url = get_database_url()

if not database_url.startswith("postgresql"):
    print("This migration targets PostgreSQL only.")
    sys.exit(1)

sql = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'eval_runs' AND column_name = 'search_type'
    ) THEN
        ALTER TABLE eval_runs ADD COLUMN search_type VARCHAR(32) DEFAULT 'semantic';
        RAISE NOTICE 'Column search_type added to eval_runs.';
    ELSE
        RAISE NOTICE 'Column search_type already exists, skipping.';
    END IF;
END
$$;
"""

from sqlalchemy import text

with engine.connect() as conn:
    conn.execute(text(sql))
    conn.commit()

print("Done.")
