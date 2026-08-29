"""Pytest fixtures and configuration."""
import pytest
from django.contrib.auth.models import User
from apps.accounts.models import UserProfile, UserRole


@pytest.fixture
def admin_user(db):
    """Fixture creating an Admin user."""
    user = User.objects.create_user(
        username="admin_user",
        email="admin@xaut.local",
        password="secure-password-123",
        is_staff=True,
    )
    user.profile.role = UserRole.ADMIN
    user.profile.save()
    return user


@pytest.fixture
def analyst_user(db):
    """Fixture creating an Analyst user."""
    user = User.objects.create_user(
        username="analyst_user",
        email="analyst@xaut.local",
        password="secure-password-123",
    )
    user.profile.role = UserRole.ANALYST
    user.profile.save()
    return user


@pytest.fixture
def viewer_user(db):
    """Fixture creating a Viewer user."""
    user = User.objects.create_user(
        username="viewer_user",
        email="viewer@xaut.local",
        password="secure-password-123",
    )
    user.profile.role = UserRole.VIEWER
    user.profile.save()
    return user
