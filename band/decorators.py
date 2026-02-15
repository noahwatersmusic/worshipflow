from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages


def admin_required(view_func):
    """Allow only admin and superadmin roles"""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        try:
            profile = request.user.profile
        except Exception:
            messages.error(request, "Your account is not set up. Contact an administrator.")
            return redirect('band:home')
        if not profile.is_admin:
            messages.error(request, "You don't have permission to access this page.")
            return redirect('band:home')
        return view_func(request, *args, **kwargs)
    return wrapper


def superadmin_required(view_func):
    """Allow only superadmin role"""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        try:
            profile = request.user.profile
        except Exception:
            messages.error(request, "Your account is not set up. Contact an administrator.")
            return redirect('band:home')
        if not profile.is_superadmin:
            messages.error(request, "You don't have permission to access this page.")
            return redirect('band:home')
        return view_func(request, *args, **kwargs)
    return wrapper
