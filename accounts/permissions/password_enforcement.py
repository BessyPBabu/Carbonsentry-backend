from rest_framework.permissions import BasePermission


class EnforcePasswordChange(BasePermission):
    def has_permission(self, request, view):
        user = request.user

        if not user or not user.is_authenticated:
            return True

        if not user.must_change_password:
            return True

        allowed_paths = (
            "/api/auth/logout/",
            "/api/auth/password/change/",
            "/api/auth/token/refresh/",
        )

        return request.path.startswith(allowed_paths)
