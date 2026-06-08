"""
DEPRECATED — this module is no longer used.

All data is persisted in PostgreSQL via SQLAlchemy (see models/database.py).
Importing this module will raise an error to surface accidental references.
"""
raise ImportError(
    "services.store is deprecated and has been removed. "
    "Use SQLAlchemy models in models/database.py instead."
)
