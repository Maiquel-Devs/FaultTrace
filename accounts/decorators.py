from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from .models import User


def organization_admin_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if request.user.is_superuser or request.user.role == User.Role.ADMIN:
            return view(request, *args, **kwargs)
        raise PermissionDenied

    return login_required(wrapped)

