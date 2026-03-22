"""
Migration: add purchase_price and worn_count to garment_items.
Run once:
    cd backend && venv/bin/python migrations/add_purchase_price_worn_count.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from db import engine
from sqlalchemy import text

def run():
    with engine.begin() as conn:
        conn.execute(text("""
            ALTER TABLE garment_items
            ADD COLUMN IF NOT EXISTS purchase_price FLOAT,
            ADD COLUMN IF NOT EXISTS worn_count     INTEGER DEFAULT 0
        """))
    print("Migration complete: purchase_price, worn_count added to garment_items.")

if __name__ == "__main__":
    run()
