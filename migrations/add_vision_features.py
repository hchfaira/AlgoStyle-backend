"""
Migration: add vision_features JSONB column to garment_items.

Stores the pre-computed Layer 1 vision analysis result verbatim so that
recommendation_service can forward lightweight feature dicts instead of
raw base64 images to LLM_project, cutting per-request payload by ~99%.

Populated automatically when a garment is added via the /items endpoint
(add_garment calls llm_client.analyze_garment_image and stores the result).
Legacy garments without vision_features fall back to sending the raw image.

Run once:
    cd backend && venv/bin/python migrations/add_vision_features.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from db import engine
from sqlalchemy import text


def run():
    with engine.begin() as conn:
        conn.execute(text("""
            ALTER TABLE garment_items
            ADD COLUMN IF NOT EXISTS vision_features JSONB
        """))
    print("Migration complete: vision_features added to garment_items.")


if __name__ == "__main__":
    run()
