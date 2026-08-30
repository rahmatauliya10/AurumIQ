"""Centralized user lifecycle management, RBAC enforcement, and last-admin safety invariants."""
from typing import Optional, Tuple
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models, transaction
from apps.accounts.models import AuditAction, UserManagementAuditLog, UserProfile, UserRole


def is_effective_active_admin(user: Optional[User]) -> bool:
    """
    Authoritatively check if a user is an active administrator.
    
    A user is an Effective Active Admin if and only if:
        is_active == True AND (is_superuser == True OR profile.role == UserRole.ADMIN)
    """
    if not user or not user.is_active:
        return False
    if user.is_superuser:
        return True
    if hasattr(user, "profile") and user.profile:
        return user.profile.role == UserRole.ADMIN.value or user.profile.role == UserRole.ADMIN
    return False


def get_effective_active_admin_ids() -> list[int]:
    """Retrieve primary keys of all currently effective active administrators."""
    return list(
        User.objects.filter(is_active=True)
        .filter(models.Q(is_superuser=True) | models.Q(profile__role=UserRole.ADMIN))
        .values_list("id", flat=True)
        .distinct()
    )


def assert_admin_invariant_not_violated(
    target_user_id: int,
    will_be_active: bool,
    will_be_admin_role: bool,
    is_superuser: bool,
) -> None:
    """
    Verify that applying a state modification to target_user will not leave zero effective active admins.
    
    MUST be called within transaction.atomic() while holding select_for_update() locks
    on all effective active admin candidate rows in deterministic order.
    """
    admin_ids = get_effective_active_admin_ids()
    target_was_effective_admin = target_user_id in admin_ids
    target_will_be_effective_admin = will_be_active and (is_superuser or will_be_admin_role)

    if target_was_effective_admin and not target_will_be_effective_admin:
        remaining_admins_count = len([uid for uid in admin_ids if uid != target_user_id])
        if remaining_admins_count < 1:
            raise ValidationError("Cannot deactivate or demote the last active administrator account.")


def disable_user_safely(
    target_user_id: int,
    actor: Optional[User] = None,
    ip_address: str = "",
) -> Tuple[bool, str]:
    """
    Safely toggle a user's is_active state with deterministic multi-row locking
    and audit log recording.
    """
    with transaction.atomic():
        # 1. Deterministic lock acquisition
        admin_ids = get_effective_active_admin_ids()
        lock_pks = sorted(list(set(admin_ids + [target_user_id])))
        locked_users = {u.id: u for u in User.objects.select_for_update().filter(id__in=lock_pks).order_by("id")}
        
        target_user = locked_users.get(target_user_id)
        if not target_user:
            return False, "Target user not found."

        if actor and target_user == actor and target_user.is_active:
            return False, "You cannot disable your own active account."

        old_status = target_user.is_active
        new_status = not old_status

        # If deactivating, verify last-admin invariant
        if not new_status:
            is_target_admin_role = hasattr(target_user, "profile") and target_user.profile.role == UserRole.ADMIN
            try:
                assert_admin_invariant_not_violated(
                    target_user_id=target_user.id,
                    will_be_active=False,
                    will_be_admin_role=is_target_admin_role,
                    is_superuser=target_user.is_superuser,
                )
            except ValidationError as e:
                return False, e.message

        target_user.is_active = new_status
        target_user.save(update_fields=["is_active"])

        action = AuditAction.USER_ENABLED if new_status else AuditAction.USER_DISABLED
        UserManagementAuditLog.objects.create(
            actor=actor,
            target_user=target_user,
            action=action,
            before_state={"is_active": old_status},
            after_state={"is_active": new_status},
            ip_address=ip_address,
        )

        status_label = "enabled" if new_status else "disabled"
        return True, f"User '{target_user.username}' has been {status_label}."


def update_user_role_safely(
    target_user_id: int,
    new_role: str,
    new_department: str,
    first_name: str,
    last_name: str,
    actor: Optional[User] = None,
    ip_address: str = "",
) -> Tuple[bool, str]:
    """
    Safely update a user's role and details with deterministic multi-row locking
    and audit log recording.
    """
    with transaction.atomic():
        admin_ids = get_effective_active_admin_ids()
        lock_pks = sorted(list(set(admin_ids + [target_user_id])))
        locked_users = {u.id: u for u in User.objects.select_for_update().filter(id__in=lock_pks).order_by("id")}

        target_user = locked_users.get(target_user_id)
        if not target_user:
            return False, "Target user not found."

        profile, _ = UserProfile.objects.select_for_update().get_or_create(user=target_user)
        old_role = profile.role
        old_dept = profile.department

        # If role is changing away from ADMIN, verify invariant
        if new_role in UserRole.values and new_role != old_role:
            try:
                assert_admin_invariant_not_violated(
                    target_user_id=target_user.id,
                    will_be_active=target_user.is_active,
                    will_be_admin_role=(new_role == UserRole.ADMIN.value or new_role == UserRole.ADMIN),
                    is_superuser=target_user.is_superuser,
                )
            except ValidationError as e:
                return False, e.message

            profile.role = new_role
            UserManagementAuditLog.objects.create(
                actor=actor,
                target_user=target_user,
                action=AuditAction.ROLE_CHANGED,
                before_state={"role": old_role},
                after_state={"role": new_role},
                ip_address=ip_address,
            )

        if new_department != old_dept:
            profile.department = new_department
            UserManagementAuditLog.objects.create(
                actor=actor,
                target_user=target_user,
                action=AuditAction.DEPARTMENT_CHANGED,
                before_state={"department": old_dept},
                after_state={"department": new_department},
                ip_address=ip_address,
            )

        target_user.first_name = first_name
        target_user.last_name = last_name
        target_user.save(update_fields=["first_name", "last_name"])
        profile.save(update_fields=["role", "department"])

        return True, f"User '{target_user.username}' updated successfully."
