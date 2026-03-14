"""
Database setup and connection management for AlgoStyle.
Uses SQLAlchemy ORM with PostgreSQL.
"""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from sqlalchemy.pool import NullPool
from contextlib import contextmanager
from config import settings

# Create base class for all models
Base = declarative_base()

# Database connection URL - will be set from config
DATABASE_URL = getattr(settings, 'database_url', 
                       'postgresql+psycopg2://postgres:postgres@localhost:5432/algostyle')

# Create engine with connection pooling
# NullPool for async environments or lightweight scenarios
engine = create_engine(
    DATABASE_URL,
    echo=False,  # Set to True for SQL query logging
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,  # Test connection before using
    future=True,
)

# Session factory
SessionLocal = sessionmaker(
    bind=engine,
    class_=Session,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


def get_db() -> Session:
    """Get a database session. Use in dependency injection."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_db_context():
    """Get a database session as a context manager."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Initialize database - create all tables."""
    Base.metadata.create_all(bind=engine)
    print("✅ Database tables initialized")


def test_connection():
    """Test database connection."""
    try:
        with get_db_context() as db:
            db.execute(text("SELECT 1"))
            print("✅ Database connection successful")
            return True
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return False

