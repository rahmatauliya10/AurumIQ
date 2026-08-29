"""Unit tests for user account profiles and RBAC role properties."""
import pytest
from django.contrib.auth.models import User
from apps.accounts.models import UserProfile, UserRole


@pytest.mark.unit
@pytest.mark.django_db
def test_user_profile_auto_created():
    """Verify UserProfile is automatically created on User creation signal."""
    user = User.objects.create_user(username="testuser", password="password")
    assert hasattr(user, "profile")
    assert user.profile.role == UserRole.VIEWER
    assert user.profile.is_viewer_role is True
    assert user.profile.is_analyst_role is False
    assert user.profile.is_admin_role is False


@pytest.mark.unit
@pytest.mark.django_db
def test_admin_role_permissions(admin_user):
    """Verify Admin role permissions."""
    assert admin_user.profile.role == UserRole.ADMIN
    assert admin_user.profile.is_admin_role is True
    assert admin_user.profile.is_analyst_role is True
    assert admin_user.profile.is_viewer_role is True


@pytest.mark.unit
@pytest.mark.django_db
def test_analyst_role_permissions(analyst_user):
    """Verify Analyst role permissions."""
    assert analyst_user.profile.role == UserRole.ANALYST
    assert analyst_user.profile.is_admin_role is False
    assert analyst_user.profile.is_analyst_role is True
    assert analyst_user.profile.is_viewer_role is True


@pytest.mark.unit
@pytest.mark.django_db
def test_superuser_inherits_all_roles():
    """Verify Django superuser inherits admin and analyst role permissions."""
    super_user = User.objects.create_superuser(username="root", password="password")
    assert super_user.profile.is_admin_role is True
    assert super_user.profile.is_analyst_role is True
