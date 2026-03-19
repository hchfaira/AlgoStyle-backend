"""
Migration: add planned_date, reminder_setting, user_timezone to custom_outfits
Run once:  python migrations/add_outfit_planned_date.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db import engine
from sqlalchemy import text

DDL = [
    # planned_date: ISO-8601 date the user plans to wear this outfit
    """
    ALTER TABLE custom_outfits
    ADD COLUMN IF NOT EXISTS planned_date TIMESTAMP WITH TIME ZONE DEFAULT NULL;
    """,
    # reminder_setting: JSON blob  {"type": "push"|"email"|"none", "minutes_before": 60}
    """
    ALTER TABLE custom_outfits
    ADD COLUMN IF NOT EXISTS reminder_setting JSONB DEFAULT NULL;
    """,
    # user_timezone: IANA tz string e.g. "Europe/Paris"
    """
    ALTER TABLE custom_outfits
    ADD COLUMN IF NOT EXISTS user_timezone VARCHAR(64) DEFAULT NULL;
    """,
    # source: how the outfit was created  build | ai | score | prompt
    """
    ALTER TABLE custom_outfits
    ADD COLUMN IF NOT EXISTS source VARCHAR(32) DEFAULT 'build';
    """,
    # ai_grade / ai_score: store the HybridOutfitRecommender output
    """
    ALTER TABLE custom_outfits
    ADD COLUMN IF NOT EXISTS ai_grade VARCHAR(4) DEFAULT NULL;
    """,
    """
    ALTER TABLE custom_outfits
    ADD COLUMN IF NOT EXISTS ai_score FLOAT DEFAULT NULL;
    """,
    # explanation_brief / explanation_detailed from LLM
    """
    ALTER TABLE custom_outfits
    ADD COLUMN IF NOT EXISTS explanation_brief TEXT DEFAULT NULL;
    """,
    """
    ALTER TABLE custom_outfits
    ADD COLUMN IF NOT EXISTS explanation_detailed TEXT DEFAULT NULL;
    """,
]

def run():
    with engine.connect() as conn:
        for stmt in DDL:
            conn.execute(text(stmt.strip()))
            print("✓", stmt.strip().splitlines()[0][:72])
        conn.commit()
    print("\n✅  Migration complete.")

if __name__ == "__main__":
    run()
