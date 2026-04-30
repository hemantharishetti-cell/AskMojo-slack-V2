"""
Utility script to create the first admin user.
Run this script to create an admin account:
    python create_admin.py
"""
import sys
from sqlalchemy.orm import Session
from app.sqlite.database import SessionLocal, Base, engine
from app.sqlite.models import User
from app.sqlite.migrations import run_migrations
from app.core.security import get_password_hash


def create_admin_user(name: str, email: str, password: str):
    """Create an admin user."""
    db: Session = SessionLocal()
    try:
        # Check if admin already exists
        existing_user = db.query(User).filter(User.email == email).first()
        if existing_user:
            print(f"User with email {email} already exists.")
            # Always update password if user exists (in case it's the default "changeme")
            existing_user.password = get_password_hash(password)
            existing_user.is_active = True
            if existing_user.role != "admin":
                existing_user.role = "admin"
                print(f"✓ Updated user {email} to admin role and reset password")
            else:
                print(f"✓ Updated password for existing admin user {email}")
            db.commit()
            return
        
        # Create new admin user
        hashed_password = get_password_hash(password)
        admin_user = User(
            name=name,
            email=email,
            password=hashed_password,
            role="admin",
            is_active=True
        )
        db.add(admin_user)
        db.commit()
        db.refresh(admin_user)
        print(f"✓ Admin user created successfully!")
        print(f"  Name: {name}")
        print(f"  Email: {email}")
        print(f"  Role: admin")
    except Exception as e:
        db.rollback()
        print(f"Error creating admin user: {e}")
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    print("=" * 50)
    print("Create Admin User")
    print("=" * 50)
    
    # Run migrations first to ensure database schema is up to date
    print("\nRunning database migrations...")
    run_migrations()
    # Also ensure all tables are created
    Base.metadata.create_all(bind=engine)
    print()
    
    # Get user input
    name = input("Enter admin name: ").strip()
    if not name:
        print("Name is required!")
        sys.exit(1)
    
    email = input("Enter admin email: ").strip()
    if not email:
        print("Email is required!")
        sys.exit(1)
    
    password = input("Enter admin password: ").strip()
    if not password:
        print("Password is required!")
        sys.exit(1)
    
    confirm_password = input("Confirm admin password: ").strip()
    if password != confirm_password:
        print("Passwords do not match!")
        sys.exit(1)
    
    create_admin_user(name, email, password)
    print("\nYou can now login at /api/v1/auth/login")

