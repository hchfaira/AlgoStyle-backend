"""Tests for auth service."""
import pytest
from fastapi import HTTPException
from services.auth_service import (
    register_user, login_user, create_guest,
    get_user_by_token, hash_password, verify_password,
)
from models.schemas import UserRole


class TestHashPassword:
    def test_hash_verifies(self):
        # bcrypt is salted — two hashes of the same password will differ,
        # but verify_password must return True for a correct password.
        hashed = hash_password("test123")
        assert verify_password("test123", hashed) is True

    def test_wrong_password_does_not_verify(self):
        hashed = hash_password("test123")
        assert verify_password("wrong", hashed) is False

    def test_different_passwords_different_hashes(self):
        assert hash_password("pass1") != hash_password("pass2")


class TestRegister:
    def test_register_success(self):
        result = register_user("test@test.com", "password", "Test User")
        assert result.user_id.startswith("user_")
        assert result.email == "test@test.com"
        assert result.name == "Test User"
        assert result.token
        assert result.is_onboarded is False

    def test_register_duplicate_email(self):
        register_user("dup@test.com", "pass", "User 1")
        with pytest.raises(HTTPException) as exc_info:
            register_user("dup@test.com", "pass", "User 2")
        assert exc_info.value.status_code == 400

    def test_register_with_role(self):
        result = register_user("stylist@test.com", "pass", "Stylist", UserRole.STYLIST)
        assert result.role == UserRole.STYLIST


class TestLogin:
    def test_login_success(self):
        register_user("login@test.com", "mypass", "Login User")
        result = login_user("login@test.com", "mypass")
        assert result.email == "login@test.com"
        assert result.token

    def test_login_wrong_email(self):
        with pytest.raises(HTTPException) as exc_info:
            login_user("wrong@test.com", "pass")
        assert exc_info.value.status_code == 401

    def test_login_wrong_password(self):
        register_user("user@test.com", "correct", "User")
        with pytest.raises(HTTPException) as exc_info:
            login_user("user@test.com", "wrong")
        assert exc_info.value.status_code == 401


class TestGuest:
    def test_guest_login(self):
        result = create_guest()
        assert result.user_id.startswith("user_")
        assert result.name == "Guest"
        assert result.email == ""
        assert result.role == UserRole.USER


class TestTokenLookup:
    def test_valid_token(self):
        auth = register_user("tok@test.com", "pass", "Token User")
        user = get_user_by_token(auth.token)
        assert user["email"] == "tok@test.com"

    def test_invalid_token(self):
        with pytest.raises(HTTPException) as exc_info:
            get_user_by_token("invalid_token")
        assert exc_info.value.status_code == 401
