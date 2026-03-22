"""
Migration: add llm_attributes JSONB column to garment_items.

Stores the LLM-native garment shape verbatim at save time so that
_algogarment_to_llm() can return it directly without re-mapping flat columns.

Run once:
    cd backend && venv/bin/python migrations/add_llm_attributes.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from db import engine
from sqlalchemy import text

def run():
    with engine.begin() as conn:
        conn.execute(text("""
            ALTER TABLE garment_items
            ADD COLUMN IF NOT EXISTS llm_attributes JSONB
        """))
    print("Migration complete: llm_attributes added to garment_items.")

if __name__ == "__main__":
    run()
