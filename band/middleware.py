from django.shortcuts import redirect
from django.urls import reverse


class ForcePasswordChangeMiddleware:
    """Redirect users with must_change_password=True to the change password page."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            # Allow access to change-password, logout, and static files
            allowed_paths = [
                reverse('band:change_password'),
                reverse('logout'),
            ]
            if request.path not in allowed_paths:
                try:
                    if request.user.profile.must_change_password:
                        return redirect('band:change_password')
                except Exception:
                    pass

        return self.get_response(request)
