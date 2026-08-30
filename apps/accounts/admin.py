"""Admin registration and security hardening for User, UserProfile, and UserManagementAuditLog."""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import UserManagementAuditLog, UserProfile, UserRole
from .services import assert_admin_invariant_not_violated, get_effective_active_admin_ids, is_effective_active_admin


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = "User Role Profile"


class UserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline,)
    list_display = ("username", "email", "first_name", "last_name", "get_role", "is_active", "is_staff")

    def get_role(self, instance):
        return instance.profile.get_role_display() if hasattr(instance, "profile") else "-"
    get_role.short_description = "Role"

    def has_delete_permission(self, request, obj=None):
        """Disable user deletion in Django Admin (enforces lifecycle ACTIVE -> DISABLED)."""
        return False

    def get_actions(self, request):
        """Remove bulk delete action from Django Admin."""
        actions = super().get_actions(request)
        if "delete_selected" in actions:
            del actions["delete_selected"]
        return actions

    def save_model(self, request, obj, form, change):
        """Enforce authoritative last-active-admin invariant upon User model save."""
        if change and "is_active" in form.changed_data and not obj.is_active:
            with transaction.atomic():
                admin_ids = get_effective_active_admin_ids()
                lock_pks = sorted(list(set(admin_ids + [obj.id])))
                list(User.objects.select_for_update().filter(id__in=lock_pks).order_by("id"))
                
                is_admin_role = hasattr(obj, "profile") and obj.profile.role == UserRole.ADMIN
                assert_admin_invariant_not_violated(
                    target_user_id=obj.id,
                    will_be_active=False,
                    will_be_admin_role=is_admin_role,
                    is_superuser=obj.is_superuser,
                )
        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        """Enforce authoritative last-active-admin invariant upon UserProfileInline save."""
        instances = formset.save(commit=False)
        for instance in instances:
            if isinstance(instance, UserProfile) and instance.pk:
                user = instance.user
                if instance.role != UserRole.ADMIN and not user.is_superuser and user.is_active:
                    with transaction.atomic():
                        admin_ids = get_effective_active_admin_ids()
                        lock_pks = sorted(list(set(admin_ids + [user.id])))
                        list(User.objects.select_for_update().filter(id__in=lock_pks).order_by("id"))
                        
                        assert_admin_invariant_not_violated(
                            target_user_id=user.id,
                            will_be_active=user.is_active,
                            will_be_admin_role=False,
                            is_superuser=user.is_superuser,
                        )
            instance.save()
        formset.save_m2m()


# Re-register UserAdmin with profile inline
admin.site.unregister(User)
admin.site.register(User, UserAdmin)


@admin.register(UserManagementAuditLog)
class UserManagementAuditLogAdmin(admin.ModelAdmin):
    """Read-only admin view for immutable user management audit trail."""
    list_display = ("created_at", "actor", "action", "target_user", "ip_address")
    list_filter = ("action", "created_at")
    search_fields = ("target_user__username", "actor__username", "action")
    readonly_fields = ("actor", "target_user", "action", "before_state", "after_state", "ip_address", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
