from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.sqlite.database import get_db
from app.sqlite.models import User
from app.core.security import (
    verify_password,
    get_password_hash,
    create_access_token,
    get_current_user,
)
from app.core.config import settings
from app.auth.schemas import Token, UserLogin, UserRegister, UserAuthResponse

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/register", response_model=UserAuthResponse, status_code=status.HTTP_201_CREATED)
def register(user_data: UserRegister, db: Session = Depends(get_db)):
    """
    Register a new user.
    """
    # Check if email already exists
    db_user = db.query(User).filter(User.email == user_data.email).first()
    if db_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Only allow admin role creation if explicitly set (or restrict to admins only)
    if user_data.role == "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot register as admin. Contact administrator."
        )
    
    # Create new user with hashed password
    hashed_password = get_password_hash(user_data.password)
    new_user = User(
        name=user_data.name,
        email=user_data.email,
        password=hashed_password,
        role=user_data.role,
        is_active=True
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


@router.post("/login", response_model=Token)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    """
    Login and get access token.
    Use email as username in the form.
    """
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user"
        )
    
    access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
    access_token = create_access_token(
        data={"sub": user.id},
        expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/login-json", response_model=Token)
def login_json(user_data: UserLogin, db: Session = Depends(get_db)):
    """
    Login using JSON body (alternative to OAuth2 form).
    """
    user = db.query(User).filter(User.email == user_data.email).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Check if password is the default "changeme" (plain text) and needs to be updated
    if user.password == "changeme":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Please reset your password. Default password cannot be used.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not verify_password(user_data.password, user.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user"
        )
    
    access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
    access_token = create_access_token(
        data={"sub": user.id},
        expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/me", response_model=UserAuthResponse)
def get_current_user_info(current_user: User = Depends(get_current_user)):
    """
    Get current authenticated user information.
    """
    return current_user

