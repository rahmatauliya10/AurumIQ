"""Account management, RBAC enforcement, and user lifecycle views."""
import json
from django.contrib import messages
from django.contrib.auth import logout as auth_logout, update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.contrib.auth.views import PasswordChangeView
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import ListView, TemplateView

from apps.accounts.models import AuditAction, UserManagementAuditLog, UserProfile, UserRole
from apps.accounts.permissions import RoleRequiredMixin, get_user_role


def _get_client_ip(request: HttpRequest) -> str:
    """Extract client IP address safely from request headers."""
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


class UserProfileView(LoginRequiredMixin, TemplateView):
    """User profile overview showing role, department, and active sessions."""
    template_name = "accounts/profile.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        role = get_user_role(user)
        context["user_obj"] = user
        context["user_role"] = role
        context["user_department"] = user.profile.department if hasattr(user, "profile") else ""
        context["is_admin"] = role == UserRole.ADMIN.value
        context["is_analyst"] = role in (UserRole.ADMIN.value, UserRole.ANALYST.value)
        return context


class CustomPasswordChangeView(PasswordChangeView):
    """Password change view enforcing Django session auth hash security."""
    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("accounts:profile")

    def form_valid(self, form):
        response = super().form_valid(form)
        # Log audit action
        UserManagementAuditLog.objects.create(
            actor=self.request.user,
            target_user=self.request.user,
            action=AuditAction.PASSWORD_CHANGED,
            before_state={},
            after_state={"status": "password_reset_success"},
            ip_address=_get_client_ip(self.request),
        )
        messages.success(self.request, "Your password has been successfully updated.")
        return response


class CustomLogoutView(View):
    """Strict POST-only logout view with CSRF protection and session invalidation."""

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        auth_logout(request)
        messages.info(request, "You have been successfully signed out.")
        return redirect("login")

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        # GET is rejected to prevent pre-fetch / CSRF logout attacks
        return HttpResponseNotAllowed(["POST"], "Logout must be performed via POST.")


class UserManagementListView(RoleRequiredMixin, ListView):
    """Admin-only view to list, filter, and manage user accounts."""
    allowed_roles = (UserRole.ADMIN,)
    model = User
    template_name = "accounts/user_management.html"
    context_object_name = "users"
    paginate_by = 25

    def get_queryset(self):
        qs = User.objects.select_related("profile").order_by("-date_joined")
        search_query = self.request.GET.get("q", "").strip()
        role_filter = self.request.GET.get("role", "").strip()
        status_filter = self.request.GET.get("status", "").strip()

        if search_query:
            qs = qs.filter(username__icontains=search_query) | qs.filter(email__icontains=search_query)
        if role_filter in UserRole.values:
            qs = qs.filter(profile__role=role_filter)
        if status_filter == "active":
            qs = qs.filter(is_active=True)
        elif status_filter == "disabled":
            qs = qs.filter(is_active=False)

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["roles"] = UserRole.choices
        context["current_q"] = self.request.GET.get("q", "")
        context["current_role"] = self.request.GET.get("role", "")
        context["current_status"] = self.request.GET.get("status", "")
        context["total_users"] = User.objects.count()
        context["active_users"] = User.objects.filter(is_active=True).count()
        context["disabled_users"] = User.objects.filter(is_active=False).count()
        return context


class UserCreateView(RoleRequiredMixin, View):
    """Admin-only action to create a new user with assigned role & department."""
    allowed_roles = (UserRole.ADMIN,)

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        username = request.POST.get("username", "").strip()
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "").strip()
        role = request.POST.get("role", UserRole.VIEWER.value).strip()
        department = request.POST.get("department", "").strip()
        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()

        if not username or not password:
            messages.error(request, "Username and password are required.")
            return redirect("accounts:user_management")

        if User.objects.filter(username=username).exists():
            messages.error(request, f"User with username '{username}' already exists.")
            return redirect("accounts:user_management")

        if role not in UserRole.values:
            role = UserRole.VIEWER.value

        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
        )

        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.role = role
        profile.department = department
        profile.save()

        # Audit Trail
        UserManagementAuditLog.objects.create(
            actor=request.user,
            target_user=user,
            action=AuditAction.USER_CREATED,
            before_state={},
            after_state={
                "username": username,
                "email": email,
                "role": role,
                "department": department,
                "is_active": True,
            },
            ip_address=_get_client_ip(request),
        )

        messages.success(request, f"User '{username}' ({role}) created successfully.")
        return redirect("accounts:user_management")


class UserEditView(RoleRequiredMixin, View):
    """Admin-only action to edit a user's role, department, and profile details."""
    allowed_roles = (UserRole.ADMIN,)

    def post(self, request: HttpRequest, user_id: int, *args, **kwargs) -> HttpResponse:
        target_user = get_object_or_404(User.objects.select_related("profile"), id=user_id)
        
        new_role = request.POST.get("role", "").strip()
        new_department = request.POST.get("department", "").strip()
        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()

        profile, _ = UserProfile.objects.get_or_create(user=target_user)
        old_role = profile.role
        old_dept = profile.department

        # Track state changes
        before_state = {
            "role": old_role,
            "department": old_dept,
            "first_name": target_user.first_name,
            "last_name": target_user.last_name,
        }

        if new_role in UserRole.values and new_role != old_role:
            profile.role = new_role
            UserManagementAuditLog.objects.create(
                actor=request.user,
                target_user=target_user,
                action=AuditAction.ROLE_CHANGED,
                before_state={"role": old_role},
                after_state={"role": new_role},
                ip_address=_get_client_ip(request),
            )

        if new_department != old_dept:
            profile.department = new_department
            UserManagementAuditLog.objects.create(
                actor=request.user,
                target_user=target_user,
                action=AuditAction.DEPARTMENT_CHANGED,
                before_state={"department": old_dept},
                after_state={"department": new_department},
                ip_address=_get_client_ip(request),
            )

        target_user.first_name = first_name
        target_user.last_name = last_name
        target_user.save()
        profile.save()

        messages.success(request, f"User '{target_user.username}' updated successfully.")
        return redirect("accounts:user_management")


class UserToggleStatusView(RoleRequiredMixin, View):
    """Admin-only action to enable or disable (soft-delete) a user."""
    allowed_roles = (UserRole.ADMIN,)

    def post(self, request: HttpRequest, user_id: int, *args, **kwargs) -> HttpResponse:
        target_user = get_object_or_404(User, id=user_id)
        
        if target_user == request.user:
            messages.error(request, "You cannot disable your own active account.")
            return redirect("accounts:user_management")

        old_status = target_user.is_active
        new_status = not old_status
        target_user.is_active = new_status
        target_user.save()

        action = AuditAction.USER_ENABLED if new_status else AuditAction.USER_DISABLED
        UserManagementAuditLog.objects.create(
            actor=request.user,
            target_user=target_user,
            action=action,
            before_state={"is_active": old_status},
            after_state={"is_active": new_status},
            ip_address=_get_client_ip(request),
        )

        status_label = "enabled" if new_status else "disabled"
        messages.success(request, f"User '{target_user.username}' has been {status_label}.")
        return redirect("accounts:user_management")


class UserAuditLogListView(RoleRequiredMixin, ListView):
    """Admin-only view displaying immutable user lifecycle audit logs."""
    allowed_roles = (UserRole.ADMIN,)
    model = UserManagementAuditLog
    template_name = "accounts/user_audit_log.html"
    context_object_name = "audit_logs"
    paginate_by = 50

    def get_queryset(self):
        return UserManagementAuditLog.objects.select_related("actor", "target_user").order_by("-created_at")
