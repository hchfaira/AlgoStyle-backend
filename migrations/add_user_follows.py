"""
Migration: create user_follows table for follow/unfollow relationships.

Schema
------
  id            VARCHAR  PK
  follower_id   VARCHAR  → users.id  (CASCADE DELETE)
  following_id  VARCHAR  → users.id  (CASCADE DELETE)
  status        VARCHAR  DEFAULT 'accepted'   ('accepted' | 'pending')
  created_at    TIMESTAMP

Run once:
    cd backend && python migrations/add_user_follows.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from db import engine
from sqlalchemy import text


def run():
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS user_follows (
                id            VARCHAR PRIMARY KEY,
                follower_id   VARCHAR NOT NULL
                              REFERENCES users(id) ON DELETE CASCADE,
                following_id  VARCHAR NOT NULL
                              REFERENCES users(id) ON DELETE CASCADE,
                status        VARCHAR NOT NULL DEFAULT 'accepted',
                created_at    TIMESTAMP NOT NULL DEFAULT NOW(),

                CONSTRAINT uq_user_follow UNIQUE (follower_id, following_id),
                CONSTRAINT ck_no_self_follow CHECK (follower_id <> following_id)
            );

            CREATE INDEX IF NOT EXISTS ix_user_follows_follower_id
                ON user_follows (follower_id);
            CREATE INDEX IF NOT EXISTS ix_user_follows_following_id
                ON user_follows (following_id);
            CREATE INDEX IF NOT EXISTS ix_user_follows_status
                ON user_follows (status);
        """))
    print("Migration complete: user_follows table created.")


if __name__ == "__main__":
    run()
