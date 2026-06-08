"""
Add enhanced body-analysis columns to image_consulting_results table.

12-season colour analysis: season_sub, chroma, season_confidence
Enhanced morphology:       body_shape_secondary, body_shape_scores, waist_hip_ratio
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import text
from db import engine


def upgrade():
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE image_consulting_results ADD COLUMN IF NOT EXISTS season_sub VARCHAR;"))
        conn.execute(text("ALTER TABLE image_consulting_results ADD COLUMN IF NOT EXISTS chroma VARCHAR;"))
        conn.execute(text("ALTER TABLE image_consulting_results ADD COLUMN IF NOT EXISTS season_confidence FLOAT;"))
        conn.execute(text("ALTER TABLE image_consulting_results ADD COLUMN IF NOT EXISTS body_shape_secondary VARCHAR;"))
        conn.execute(text("ALTER TABLE image_consulting_results ADD COLUMN IF NOT EXISTS body_shape_scores JSON;"))
        conn.execute(text("ALTER TABLE image_consulting_results ADD COLUMN IF NOT EXISTS waist_hip_ratio FLOAT;"))
        conn.commit()
    print("✅ Migration add_enhanced_body_analysis applied.")


def downgrade():
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE image_consulting_results DROP COLUMN IF EXISTS season_sub;"))
        conn.execute(text("ALTER TABLE image_consulting_results DROP COLUMN IF EXISTS chroma;"))
        conn.execute(text("ALTER TABLE image_consulting_results DROP COLUMN IF EXISTS season_confidence;"))
        conn.execute(text("ALTER TABLE image_consulting_results DROP COLUMN IF EXISTS body_shape_secondary;"))
        conn.execute(text("ALTER TABLE image_consulting_results DROP COLUMN IF EXISTS body_shape_scores;"))
        conn.execute(text("ALTER TABLE image_consulting_results DROP COLUMN IF EXISTS waist_hip_ratio;"))
        conn.commit()
    print("✅ Migration add_enhanced_body_analysis rolled back.")


if __name__ == "__main__":
    upgrade()
