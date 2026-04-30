"""
Utility script to reset a user's password.
Run this script to update a user's password:
    python reset_password.py
"""
import sys
from pathlib import Path
from sqlalchemy.orm import Session
# Ensure project root (the folder containing 'app/') is on sys.path so imports work
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.sqlite.database import SessionLocal  # type: ignore
from app.sqlite.models import User  # type: ignore
from app.core.security import get_password_hash, verify_password  # type: ignore


def reset_user_password(email: str, new_password: str):
    """Reset a user's password."""
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            print(f"User with email {email} not found.")
            return False
        
        # Hash the new password
        hashed_password = get_password_hash(new_password)
        user.password = hashed_password
        db.commit()
        
        print(f"✓ Password reset successfully for {email}")
        return True
    except Exception as e:
        db.rollback()
        print(f"Error resetting password: {e}")
        return False
    finally:
        db.close()


if __name__ == "__main__":
    print("=" * 50)
    print("Reset User Password")
    print("=" * 50)
    
    email = input("Enter user email: ").strip()
    if not email:
        print("Email is required!")
        sys.exit(1)
    
    # Use getpass to avoid echoing passwords in terminal
    try:
        import getpass
        new_password = getpass.getpass("Enter new password: ").strip()
    except Exception:
        new_password = input("Enter new password: ").strip()
    if not new_password:
        print("Password is required!")
        sys.exit(1)
    
    try:
        import getpass
        confirm_password = getpass.getpass("Confirm new password: ").strip()
    except Exception:
        confirm_password = input("Confirm new password: ").strip()
    if new_password != confirm_password:
        print("Passwords do not match!")
        sys.exit(1)
    
    if reset_user_password(email, new_password):
        print("\nYou can now login with the new password")
    else:
        sys.exit(1)

