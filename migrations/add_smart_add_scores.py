"""
Migration: add smart_add_scores and neo4j_indexed columns to garment_items.

smart_add_scores  — JSON blob written by the fire-and-forget background job
                    after a garment is saved. Populated by Layer 2
                    (simulate-addition) and Layer 3 (body/color profile).
                    Shape:
                    {
                      "status": "pending" | "done" | "failed",
                      "computed_at": "<ISO datetime>",
                      # Layer 2 — wardrobe impact
                      "pair_count": 8,
                      "outfit_count": 4,
                      "versatility_score": 0.72,
                      "is_gap_fill": true,
                      "gap_fill_reason": "missing smart-casual top",
                      "duplicate_id": null,       # garment_id if similar piece found
                      "duplicate_similarity": null,
                      # Layer 3 — profile fit
                      "body_compatibility": 0.85,
                      "color_season_match": true,
                      "color_season_label": "Autumn Warm",
                      "profile_notes": "Good for your inverted-triangle silhouette"
                    }

neo4j_indexed     — True once COMPATIBLE_WITH relations have been created in
                    the graph database by the background job.

Run with:
    python migrations/add_smart_add_scores.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import text
from db import engine


def upgrade():
    with engine.connect() as conn:
        conn.execute(text("""
            ALTER TABLE garment_items
            ADD COLUMN IF NOT EXISTS smart_add_scores JSONB,
            ADD COLUMN IF NOT EXISTS neo4j_indexed BOOLEAN NOT NULL DEFAULT FALSE;
        """))
        # Index on neo4j_indexed so we can efficiently query un-indexed garments
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_garment_items_neo4j_indexed
            ON garment_items (user_id, neo4j_indexed)
            WHERE neo4j_indexed = FALSE;
        """))
        conn.commit()
    print("✅ Migration add_smart_add_scores applied.")


def downgrade():
    with engine.connect() as conn:
        conn.execute(text("""
            ALTER TABLE garment_items
            DROP COLUMN IF EXISTS smart_add_scores,
            DROP COLUMN IF EXISTS neo4j_indexed;
        """))
        conn.commit()
    print("✅ Migration add_smart_add_scores rolled back.")


if __name__ == "__main__":
    upgrade()
