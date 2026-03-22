"""
Migration: create wardrobe_analysis_cache table.

Stores the persisted result of analyze_wardrobe() so the LLM is only called
once per wardrobe change (add/update/delete garment) rather than on every
server restart or cache-miss.

Schema
------
  user_id     VARCHAR  PK  → users.id  (CASCADE DELETE)
  result_json JSONB        full WardrobeInsightsResponse as JSON
  is_dirty    BOOLEAN  DEFAULT TRUE
              TRUE  = wardrobe changed → recompute on next read
              FALSE = result_json is current
  cached_at   TIMESTAMP    when the last LLM run completed
  updated_at  TIMESTAMP    last row touch

Run once:
    cd backend && venv/bin/python migrations/add_wardrobe_analysis_cache.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from db import engine
from sqlalchemy import text


def run():
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS wardrobe_analysis_cache (
                user_id     VARCHAR PRIMARY KEY
                            REFERENCES users(id) ON DELETE CASCADE,
                result_json JSONB,
                is_dirty    BOOLEAN NOT NULL DEFAULT TRUE,
                cached_at   TIMESTAMP,
                updated_at  TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """))
    print("Migration complete: wardrobe_analysis_cache table created.")


if __name__ == "__main__":
    run()
