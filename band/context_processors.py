from .models import Church


def user_context(request):
    """Inject user role and church info into all templates"""
    context = {
        'user_role': None,
        'is_admin': False,
        'is_superadmin': False,
        'active_church': None,
        'all_churches': [],
    }

    if not request.user.is_authenticated:
        return context

    try:
        profile = request.user.profile
    except Exception:
        return context

    context['user_role'] = profile.app_role
    context['is_admin'] = profile.is_admin
    context['is_superadmin'] = profile.is_superadmin

    if profile.is_superadmin:
        # SuperAdmin can switch churches via session
        active_church_id = request.session.get('active_church_id')
        if active_church_id:
            try:
                context['active_church'] = Church.objects.get(id=active_church_id, is_active=True)
            except Church.DoesNotExist:
                pass
        context['all_churches'] = list(Church.objects.filter(is_active=True))
    else:
        context['active_church'] = profile.church

    return context
