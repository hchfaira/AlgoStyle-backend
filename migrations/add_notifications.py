"""
Migration: create notifications table for in-app notifications.

Schema
------
  id            VARCHAR  PK
  user_id       VARCHAR  → users.id  (CASCADE DELETE)   -- recipient
  type          VARCHAR  NOT NULL                        -- new_follower, follow_request, outfit_liked
  actor_id      VARCHAR  → users.id  (SET NULL)          -- user who triggered it
  outfit_id     VARCHAR  nullable                        -- related outfit (for likes)
  content       TEXT     NOT NULL
  is_read       BOOLEAN  DEFAULT FALSE
  created_at    TIMESTAMP

Run once:
    cd backend && python migrations/add_notifications.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from db import engine
from sqlalchemy import text


def run():
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS notifications (
                id            VARCHAR PRIMARY KEY,
                user_id       VARCHAR NOT NULL
                              REFERENCES users(id) ON DELETE CASCADE,
                type          VARCHAR NOT NULL,
                actor_id      VARCHAR
                              REFERENCES users(id) ON DELETE SET NULL,
                outfit_id     VARCHAR,
                content       TEXT NOT NULL,
                is_read       BOOLEAN NOT NULL DEFAULT FALSE,
                created_at    TIMESTAMP NOT NULL DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS ix_notifications_user_id
                ON notifications (user_id);
            CREATE INDEX IF NOT EXISTS ix_notifications_user_unread
                ON notifications (user_id, is_read)
                WHERE is_read = FALSE;
            CREATE INDEX IF NOT EXISTS ix_notifications_created_at
                ON notifications (created_at DESC);
        """))
    print("Migration complete: notifications table created.")


if __name__ == "__main__":
    run()
