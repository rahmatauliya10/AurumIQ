"""Unit and concurrency tests for user account profiles, RBAC role gating, and user management audit trails."""
import threading
import pytest
from django.contrib import admin
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connections, models, transaction
from django.test import Client, RequestFactory, TransactionTestCase
from django.urls import reverse

from apps.accounts.admin import UserAdmin, UserManagementAuditLogAdmin
from apps.accounts.models import AuditAction, UserManagementAuditLog, UserProfile, UserRole
from apps.accounts.permissions import get_user_role, user_has_role
from apps.accounts.services import (
    assert_admin_invariant_not_violated,
    disable_user_safely,
    get_effective_active_admin_ids,
    is_effective_active_admin,
    update_user_role_safely,
)


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
def test_audit_durability_protect_on_delete():
    """Verify User deletion is protected when referenced by UserManagementAuditLog."""
    user = User.objects.create_user(username="durability_test", password="password123")
    audit_log = UserManagementAuditLog.objects.create(
        actor=user,
        target_user=user,
        action=AuditAction.USER_CREATED,
        before_state={},
        after_state={"username": "durability_test"},
    )
    assert audit_log.id is not None

    with pytest.raises(models.ProtectedError):
        user.delete()

    assert User.objects.filter(username="durability_test").exists()
    assert UserManagementAuditLog.objects.filter(id=audit_log.id).exists()


@pytest.mark.unit
def test_django_admin_user_has_no_delete_permission():
    """Verify UserAdmin disables deletion and removes bulk delete action."""
    rf = RequestFactory()
    request = rf.get("/admin/")
    user_admin = UserAdmin(User, admin.site)
    assert user_admin.has_delete_permission(request) is False
    actions = user_admin.get_actions(request)
    assert "delete_selected" not in actions

    audit_admin = UserManagementAuditLogAdmin(UserManagementAuditLog, admin.site)
    assert audit_admin.has_add_permission(request) is False
    assert audit_admin.has_change_permission(request) is False
    assert audit_admin.has_delete_permission(request) is False


@pytest.mark.unit
@pytest.mark.django_db
def test_effective_active_admin_semantics():
    """Verify effective active admin helper handles superuser and profile role accurately."""
    u1 = User.objects.create_user(username="admin_u1", password="password", is_active=True)
    u1.profile.role = UserRole.ADMIN
    u1.profile.save()
    assert is_effective_active_admin(u1) is True

    # Inactive admin is not effective active admin
    u1.is_active = False
    u1.save()
    assert is_effective_active_admin(u1) is False

    # Superuser with VIEWER profile is effective active admin
    su = User.objects.create_superuser(username="su_viewer", password="password", is_active=True)
    su.profile.role = UserRole.VIEWER
    su.profile.save()
    assert is_effective_active_admin(su) is True

    # Inactive superuser is not effective active admin
    su.is_active = False
    su.save()
    assert is_effective_active_admin(su) is False


@pytest.mark.unit
@pytest.mark.django_db
def test_last_active_admin_disable_protection(client: Client):
    """Verify sole active admin cannot be disabled via view or service."""
    User.objects.all().delete()
    sole_admin = User.objects.create_user(username="sole_admin", password="password123", is_active=True)
    sole_admin.profile.role = UserRole.ADMIN
    sole_admin.profile.save()

    # 1. View level: cannot disable self
    client.force_login(sole_admin)
    url = reverse("accounts:user_toggle_status", kwargs={"user_id": sole_admin.id})
    resp = client.post(url)
    assert resp.status_code == 302
    sole_admin.refresh_from_db()
    assert sole_admin.is_active is True

    # 2. Service level: directly attempting to disable the last active admin is rejected
    success, msg = disable_user_safely(target_user_id=sole_admin.id, actor=None)
    assert success is False
    assert "last active administrator" in msg
    sole_admin.refresh_from_db()
    assert sole_admin.is_active is True


@pytest.mark.unit
@pytest.mark.django_db
def test_django_admin_cannot_bypass_last_admin_invariant():
    """Verify Django Admin save_model and save_formset prevent deactivating/demoting last active admin."""
    User.objects.all().delete()
    sole_admin = User.objects.create_user(username="sole_admin", password="password123", is_active=True)
    sole_admin.profile.role = UserRole.ADMIN
    sole_admin.profile.save()

    rf = RequestFactory()
    request = rf.post("/admin/auth/user/")
    user_admin = UserAdmin(User, admin.site)

    # 1. Attempting to deactivate via UserAdmin save_model
    class DummyForm:
        changed_data = ["is_active"]
        cleaned_data = {"is_active": False}

    sole_admin.is_active = False
    with pytest.raises(ValidationError) as excinfo:
        user_admin.save_model(request, sole_admin, DummyForm(), change=True)
    assert "last active administrator" in str(excinfo.value)

    # 2. Invariant helper directly raises ValidationError
    with pytest.raises(ValidationError):
        assert_admin_invariant_not_violated(
            target_user_id=sole_admin.id,
            will_be_active=False,
            will_be_admin_role=True,
            is_superuser=False,
        )


@pytest.mark.unit
@pytest.mark.django_db
def test_last_active_admin_demotion_protection(client: Client):
    """Verify sole active admin cannot be demoted via UserEditView."""
    User.objects.all().delete()
    sole_admin = User.objects.create_user(username="sole_admin", password="password123", is_active=True)
    sole_admin.profile.role = UserRole.ADMIN
    sole_admin.profile.save()

    client.force_login(sole_admin)
    url = reverse("accounts:user_edit", kwargs={"user_id": sole_admin.id})

    resp = client.post(url, {
        "role": UserRole.VIEWER.value,
        "department": "Engineering",
        "first_name": "Sole",
        "last_name": "Admin",
    })
    assert resp.status_code == 302
    sole_admin.profile.refresh_from_db()
    assert sole_admin.profile.role == UserRole.ADMIN


@pytest.mark.unit
@pytest.mark.django_db
def test_superuser_counts_as_effective_admin_for_mutations(client: Client):
    """Verify presence of active superuser allows demoting/disabling regular profile admin."""
    User.objects.all().delete()
    su = User.objects.create_superuser(username="root_admin", password="password", is_active=True)
    su.profile.role = UserRole.VIEWER
    su.profile.save()

    reg_admin = User.objects.create_user(username="reg_admin", password="password", is_active=True)
    reg_admin.profile.role = UserRole.ADMIN
    reg_admin.profile.save()

    client.force_login(su)
    
    # Demote regular admin -> succeeds because su remains an effective active admin
    edit_url = reverse("accounts:user_edit", kwargs={"user_id": reg_admin.id})
    resp = client.post(edit_url, {
        "role": UserRole.ANALYST.value,
        "department": "Quant",
        "first_name": "Reg",
        "last_name": "Admin",
    })
    assert resp.status_code == 302
    reg_admin.profile.refresh_from_db()
    assert reg_admin.profile.role == UserRole.ANALYST

    # Disable regular user -> succeeds
    toggle_url = reverse("accounts:user_toggle_status", kwargs={"user_id": reg_admin.id})
    resp_toggle = client.post(toggle_url)
    assert resp_toggle.status_code == 302
    reg_admin.refresh_from_db()
    assert reg_admin.is_active is False


@pytest.mark.unit
@pytest.mark.django_db
def test_multiple_admins_allow_disabling_or_demoting_non_last_admin(client: Client):
    """Verify when >1 active admins exist, modifying one admin succeeds."""
    User.objects.all().delete()
    admin1 = User.objects.create_user(username="admin1", password="password", is_active=True)
    admin1.profile.role = UserRole.ADMIN
    admin1.profile.save()

    admin2 = User.objects.create_user(username="admin2", password="password", is_active=True)
    admin2.profile.role = UserRole.ADMIN
    admin2.profile.save()

    client.force_login(admin1)

    # 1. Admin1 disables Admin2
    toggle_url = reverse("accounts:user_toggle_status", kwargs={"user_id": admin2.id})
    resp = client.post(toggle_url)
    assert resp.status_code == 302
    admin2.refresh_from_db()
    assert admin2.is_active is False

    # 2. Admin1 re-enables Admin2
    resp = client.post(toggle_url)
    assert resp.status_code == 302
    admin2.refresh_from_db()
    assert admin2.is_active is True

    # 3. Admin1 demotes Admin2 to ANALYST
    edit_url = reverse("accounts:user_edit", kwargs={"user_id": admin2.id})
    resp = client.post(edit_url, {
        "role": UserRole.ANALYST.value,
        "department": "Research",
        "first_name": "Admin",
        "last_name": "Two",
    })
    assert resp.status_code == 302
    admin2.profile.refresh_from_db()
    assert admin2.profile.role == UserRole.ANALYST


@pytest.mark.unit
@pytest.mark.django_db
def test_wrong_role_direct_url_access_matrix(client: Client):
    """Verify comprehensive role access matrix across all account endpoints."""
    User.objects.all().delete()
    viewer = User.objects.create_user(username="v_user", password="password", is_active=True)
    viewer.profile.role = UserRole.VIEWER
    viewer.profile.save()

    analyst = User.objects.create_user(username="a_user", password="password", is_active=True)
    analyst.profile.role = UserRole.ANALYST
    analyst.profile.save()

    admin_u = User.objects.create_user(username="ad_user", password="password", is_active=True)
    admin_u.profile.role = UserRole.ADMIN
    admin_u.profile.save()

    admin_urls = [
        reverse("accounts:user_management"),
        reverse("accounts:user_audit_log"),
    ]

    for url in admin_urls:
        # Anonymous -> 302 Login
        client.logout()
        assert client.get(url).status_code == 302

        # Viewer -> 403 Forbidden
        client.force_login(viewer)
        assert client.get(url).status_code == 403

        # Analyst -> 403 Forbidden
        client.force_login(analyst)
        assert client.get(url).status_code == 403

        # Admin -> 200 OK
        client.force_login(admin_u)
        assert client.get(url).status_code == 200


class ConcurrencyAdminLockoutTests(TransactionTestCase):
    """Transactional concurrency tests ensuring two simultaneous admin updates cannot leave zero admins."""

    def tearDown(self):
        super().tearDown()
        connections.close_all()

    def test_concurrent_admin_mutations_cannot_leave_zero_effective_admins(self):
        """Simulate two concurrent threads attempting to disable the only two active admins."""
        User.objects.all().delete()
        admin_a = User.objects.create_user(username="admin_a", password="password", is_active=True)
        admin_a.profile.role = UserRole.ADMIN
        admin_a.profile.save()

        admin_b = User.objects.create_user(username="admin_b", password="password", is_active=True)
        admin_b.profile.role = UserRole.ADMIN
        admin_b.profile.save()

        results = []

        def worker_disable(target_id, actor_user):
            try:
                success, msg = disable_user_safely(target_user_id=target_id, actor=actor_user)
                results.append((target_id, success, msg))
            finally:
                connections.close_all()

        t1 = threading.Thread(target=worker_disable, args=(admin_a.id, admin_b))
        t2 = threading.Thread(target=worker_disable, args=(admin_b.id, admin_a))

        t1.start()
        t2.start()
        t1.join()
        t2.join()
        connections.close_all()

        # Reload state from database
        admin_a.refresh_from_db()
        admin_b.refresh_from_db()

        # At least one admin MUST remain active
        active_admins = User.objects.filter(is_active=True, profile__role=UserRole.ADMIN).count()
        assert active_admins >= 1, f"Zero active admins remained! Results: {results}"

