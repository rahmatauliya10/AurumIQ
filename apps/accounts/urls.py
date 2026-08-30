"""URL routing for accounts, RBAC, profile, and user management."""
from django.urls import path
from apps.accounts.views import (
    CustomLogoutView,
    CustomPasswordChangeView,
    UserAuditLogListView,
    UserCreateView,
    UserEditView,
    UserManagementListView,
    UserProfileView,
    UserToggleStatusView,
)

app_name = "accounts"

urlpatterns = [
    path("profile/", UserProfileView.as_view(), name="profile"),
    path("password_change/", CustomPasswordChangeView.as_view(), name="password_change"),
    path("logout/", CustomLogoutView.as_view(), name="logout"),
    path("users/", UserManagementListView.as_view(), name="user_management"),
    path("users/create/", UserCreateView.as_view(), name="user_create"),
    path("users/<int:user_id>/edit/", UserEditView.as_view(), name="user_edit"),
    path("users/<int:user_id>/toggle-status/", UserToggleStatusView.as_view(), name="user_toggle_status"),
    path("users/audit/", UserAuditLogListView.as_view(), name="user_audit_log"),
]
