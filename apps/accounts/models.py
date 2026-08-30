"""User accounts and role-based access control (RBAC) models."""
from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver


class UserRole(models.TextChoices):
    ADMIN = "ADMIN", "Administrator"
    ANALYST = "ANALYST", "Quantitative Analyst"
    VIEWER = "VIEWER", "Read-Only Viewer"


class UserProfile(models.Model):
    """Extends standard Django User with application role permissions."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(
        max_length=16,
        choices=UserRole.choices,
        default=UserRole.VIEWER,
        db_index=True,
    )
    department = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"

    @property
    def is_admin_role(self) -> bool:
        return self.role == UserRole.ADMIN or self.user.is_superuser

    @property
    def is_analyst_role(self) -> bool:
        return self.role in (UserRole.ADMIN, UserRole.ANALYST) or self.user.is_superuser

    @property
    def is_viewer_role(self) -> bool:
        return True


@receiver(post_save, sender=User)
def create_or_update_user_profile(sender, instance, created, **kwargs):
    """Automatically create or synchronize UserProfile upon User save."""
    if created:
        UserProfile.objects.create(user=instance)
    else:
        if hasattr(instance, "profile"):
            instance.profile.save()
        else:
            UserProfile.objects.create(user=instance)


class AuditAction(models.TextChoices):
    USER_CREATED = "USER_CREATED", "User Created"
    USER_DISABLED = "USER_DISABLED", "User Disabled"
    USER_ENABLED = "USER_ENABLED", "User Enabled"
    ROLE_CHANGED = "ROLE_CHANGED", "Role Changed"
    DEPARTMENT_CHANGED = "DEPARTMENT_CHANGED", "Department Changed"
    PASSWORD_CHANGED = "PASSWORD_CHANGED", "Password Changed"


class UserManagementAuditLog(models.Model):
    """Immutable audit trail for all user lifecycle and RBAC modifications."""
    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_actions_performed",
    )
    target_user = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="audit_logs_received",
    )
    action = models.CharField(
        max_length=32,
        choices=AuditAction.choices,
        db_index=True,
    )
    before_state = models.JSONField(default=dict, blank=True)
    after_state = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "User Management Audit Log"
        verbose_name_plural = "User Management Audit Logs"

    def __str__(self):
        actor_name = self.actor.username if self.actor else "System"
        return f"[{self.created_at.strftime('%Y-%m-%d %H:%M:%S')}] {actor_name} -> {self.action} on {self.target_user.username}"

