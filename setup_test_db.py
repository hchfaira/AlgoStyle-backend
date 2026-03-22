#!/usr/bin/env python3
"""
Setup script for local development — Initialize database + test users
Usage: python setup_test_db.py
"""
import sys
import os
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))

from db import init_db, SessionLocal
from models.database import User, UserProfile
from services.auth_service import hash_password


def create_test_users():
    """Create test users for development"""
    db = SessionLocal()
    try:
        # Check if test users already exist
        existing = db.query(User).filter(User.email == "test@algostyle.com").first()
        if existing:
            print("✅ Test user already exists — skipping creation")
            return existing.id
        
        # Create test user
        test_user = User(
            email="test@algostyle.com",
            name="Test User",
            password_hash=hash_password("Test123!"),
        )
        db.add(test_user)
        db.flush()  # Get the ID without committing
        user_id = test_user.id
        
        # Create user profile with measurements
        test_profile = UserProfile(
            user_id=user_id,
            height_cm=170,
            weight_kg=65,
        )
        db.add(test_profile)
        db.commit()
        
        print(f"✅ Test user created successfully")
        print(f"   Email: test@algostyle.com")
        print(f"   Password: Test123!")
        print(f"   User ID: {user_id}")
        
        return user_id
    except Exception as e:
        db.rollback()
        print(f"❌ Error creating test user: {e}")
        raise
    finally:
        db.close()


def main():
    """Main setup function"""
    print("🔧 AlgoStyle Backend — Local Setup")
    print("=" * 60)
    
    # Step 1: Initialize database
    print("\n📦 Initializing database...")
    try:
        init_db()
        print("✅ Database initialized")
    except Exception as e:
        print(f"❌ Database initialization failed: {e}")
        sys.exit(1)
    
    # Step 2: Create test users
    print("\n👤 Creating test users...")
    try:
        user_id = create_test_users()
    except Exception as e:
        print(f"❌ Failed to create test users: {e}")
        sys.exit(1)
    
    # Step 3: Summary
    print("\n" + "=" * 60)
    print("✅ Setup complete! You can now test the API")
    print("\n📝 Quick test with curl:")
    print(f"""
# 1. Login
curl -X POST http://localhost:8000/api/v1/auth/login \\
  -H "Content-Type: application/json" \\
  -d {{'email': 'test@algostyle.com', 'password': 'Test123!'}}

# 2. Upload image for analysis
curl -X POST http://localhost:8000/api/v1/image-consulting/analyze/{user_id} \\
  -H "Authorization: Bearer <token>" \\
  -F "image=@/path/to/image.jpg" \\
  -F "height_cm=170" \\
  -F "weight_kg=65"
""")


if __name__ == "__main__":
    main()
