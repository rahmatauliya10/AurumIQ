"""Unit tests for user account profiles, RBAC role gating, and user management audit trails."""
import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.accounts.models import AuditAction, UserManagementAuditLog, UserProfile, UserRole
from apps.accounts.permissions import get_user_role, user_has_role


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
    assert user_has_role(super_user, [UserRole.ADMIN]) is True
    assert user_has_role(super_user, [UserRole.ANALYST]) is True
    assert user_has_role(super_user, [UserRole.VIEWER]) is True


@pytest.mark.unit
@pytest.mark.django_db
def test_user_profile_view_authenticated(client: Client, viewer_user):
    """Verify UserProfileView renders profile for authenticated user."""
    client.force_login(viewer_user)
    response = client.get(reverse("accounts:profile"))
    assert response.status_code == 200
    assert "User Profile" in response.content.decode()
    assert viewer_user.username in response.content.decode()


@pytest.mark.unit
@pytest.mark.django_db
def test_user_profile_view_unauthenticated(client: Client):
    """Verify unauthenticated user is redirected to login."""
    response = client.get(reverse("accounts:profile"))
    assert response.status_code == 302
    assert "/accounts/login/" in response.url


@pytest.mark.unit
@pytest.mark.django_db
def test_post_only_logout(client: Client, viewer_user):
    """Verify logout strictly requires POST and invalidates session."""
    client.force_login(viewer_user)
    
    # GET request is rejected with 405 Method Not Allowed
    get_resp = client.get(reverse("accounts:logout"))
    assert get_resp.status_code == 405

    # POST request logs out user and redirects to login
    post_resp = client.post(reverse("accounts:logout"))
    assert post_resp.status_code == 302
    assert "/accounts/login/" in post_resp.url


@pytest.mark.unit
@pytest.mark.django_db
def test_admin_user_management_access(client: Client, admin_user, analyst_user, viewer_user):
    """Verify only Admin can access User Management directory."""
    url = reverse("accounts:user_management")

    # Viewer -> 403 Forbidden
    client.force_login(viewer_user)
    assert client.get(url).status_code == 403

    # Analyst -> 403 Forbidden
    client.force_login(analyst_user)
    assert client.get(url).status_code == 403

    # Admin -> 200 OK
    client.force_login(admin_user)
    resp = client.get(url)
    assert resp.status_code == 200
    assert "User Accounts Directory" in resp.content.decode()


@pytest.mark.unit
@pytest.mark.django_db
def test_admin_create_user_and_audit_log(client: Client, admin_user):
    """Verify Admin can create new user and an immutable audit log is recorded."""
    client.force_login(admin_user)
    url = reverse("accounts:user_create")

    response = client.post(url, {
        "username": "newquant",
        "email": "newquant@aurumiq.com",
        "password": "ValidPassword123!",
        "role": UserRole.ANALYST.value,
        "department": "Quantitative Research",
        "first_name": "New",
        "last_name": "Quant",
    })
    assert response.status_code == 302

    created_user = User.objects.get(username="newquant")
    assert created_user.email == "newquant@aurumiq.com"
    assert created_user.profile.role == UserRole.ANALYST
    assert created_user.profile.department == "Quantitative Research"

    # Verify Audit Log
    audit = UserManagementAuditLog.objects.filter(target_user=created_user, action=AuditAction.USER_CREATED).first()
    assert audit is not None
    assert audit.actor == admin_user
    assert audit.after_state["role"] == UserRole.ANALYST.value


@pytest.mark.unit
@pytest.mark.django_db
def test_admin_edit_user_role_and_audit_log(client: Client, admin_user, viewer_user):
    """Verify Admin can edit user role and department with audit trail."""
    client.force_login(admin_user)
    url = reverse("accounts:user_edit", kwargs={"user_id": viewer_user.id})

    response = client.post(url, {
        "role": UserRole.ANALYST.value,
        "department": "Risk Analysis",
        "first_name": "Updated",
        "last_name": "Name",
    })
    assert response.status_code == 302

    viewer_user.profile.refresh_from_db()
    assert viewer_user.profile.role == UserRole.ANALYST
    assert viewer_user.profile.department == "Risk Analysis"

    # Verify Audit Logs
    role_audit = UserManagementAuditLog.objects.filter(target_user=viewer_user, action=AuditAction.ROLE_CHANGED).first()
    assert role_audit is not None
    assert role_audit.before_state["role"] == UserRole.VIEWER.value
    assert role_audit.after_state["role"] == UserRole.ANALYST.value


@pytest.mark.unit
@pytest.mark.django_db
def test_admin_toggle_user_status_soft_delete(client: Client, admin_user, viewer_user):
    """Verify Admin can disable user (soft delete via is_active=False) with audit log."""
    client.force_login(admin_user)
    url = reverse("accounts:user_toggle_status", kwargs={"user_id": viewer_user.id})

    assert viewer_user.is_active is True

    # 1. Disable user
    resp1 = client.post(url)
    assert resp1.status_code == 302
    viewer_user.refresh_from_db()
    assert viewer_user.is_active is False

    audit_disabled = UserManagementAuditLog.objects.filter(target_user=viewer_user, action=AuditAction.USER_DISABLED).first()
    assert audit_disabled is not None
    assert audit_disabled.after_state["is_active"] is False

    # 2. Re-enable user
    resp2 = client.post(url)
    assert resp2.status_code == 302
    viewer_user.refresh_from_db()
    assert viewer_user.is_active is True

    audit_enabled = UserManagementAuditLog.objects.filter(target_user=viewer_user, action=AuditAction.USER_ENABLED).first()
    assert audit_enabled is not None
    assert audit_enabled.after_state["is_active"] is True


@pytest.mark.unit
@pytest.mark.django_db
def test_admin_cannot_disable_self(client: Client, admin_user):
    """Verify Admin cannot disable their own active account."""
    client.force_login(admin_user)
    url = reverse("accounts:user_toggle_status", kwargs={"user_id": admin_user.id})

    response = client.post(url)
    assert response.status_code == 302
    admin_user.refresh_from_db()
    assert admin_user.is_active is True


@pytest.mark.unit
@pytest.mark.django_db
def test_user_audit_log_access(client: Client, admin_user, analyst_user):
    """Verify only Admin can access the immutable User Management audit log."""
    url = reverse("accounts:user_audit_log")

    # Analyst -> 403 Forbidden
    client.force_login(analyst_user)
    assert client.get(url).status_code == 403

    # Admin -> 200 OK
    client.force_login(admin_user)
    resp = client.get(url)
    assert resp.status_code == 200
    assert "User Management Audit Log" in resp.content.decode()
