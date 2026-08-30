"""Role-Based Access Control (RBAC) permissions, mixins, and decorators."""
from functools import wraps
from typing import Sequence, Union

from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect

from apps.accounts.models import UserRole


def get_user_role(user) -> str:
    """Resolve authoritative role for user."""
    if not user or not user.is_authenticated:
        return ""
    if user.is_superuser:
        return UserRole.ADMIN.value
    if hasattr(user, "profile") and user.profile:
        return user.profile.role
    return UserRole.VIEWER.value


def user_has_role(user, allowed_roles: Sequence[Union[str, UserRole]]) -> bool:
    """Check if user has any of the permitted roles (with superuser bypass)."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    
    current_role = get_user_role(user)
    role_values = [r.value if isinstance(r, UserRole) else str(r) for r in allowed_roles]
    
    # Hierarchy check
    if current_role == UserRole.ADMIN.value:
        return True
    if current_role == UserRole.ANALYST.value and (
        UserRole.ANALYST.value in role_values or UserRole.VIEWER.value in role_values
    ):
        return True
    if current_role == UserRole.VIEWER.value and UserRole.VIEWER.value in role_values:
        return True
    
    return current_role in role_values


def role_required(*allowed_roles: Union[str, UserRole]):
    """
    View decorator enforcing RBAC.
    
    Redirects unauthenticated users to login;
    Raises PermissionDenied (403) or returns JSON error if authenticated but unauthorized.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request: HttpRequest, *args, **kwargs) -> HttpResponse:
            if not request.user.is_authenticated:
                if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.path.startswith("/api/"):
                    return JsonResponse({"error": "Authentication required"}, status=401)
                return redirect(f"/accounts/login/?next={request.path}")
            
            if not user_has_role(request.user, allowed_roles):
                if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.path.startswith("/api/"):
                    return JsonResponse({"error": "Permission denied. Insufficient role privileges."}, status=403)
                raise PermissionDenied("You do not have permission to access this resource.")
            
            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator


class RoleRequiredMixin(AccessMixin):
    """
    CBV mixin enforcing RBAC role gating.
    """
    allowed_roles: Sequence[Union[str, UserRole]] = (UserRole.VIEWER,)

    def dispatch(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        
        if not user_has_role(request.user, self.allowed_roles):
            if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.path.startswith("/api/"):
                return JsonResponse({"error": "Permission denied. Insufficient role privileges."}, status=403)
            raise PermissionDenied("You do not have permission to access this resource.")
        
        return super().dispatch(request, *args, **kwargs)
