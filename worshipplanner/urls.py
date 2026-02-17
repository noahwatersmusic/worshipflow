"""
URL configuration for worshipplanner project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, include
from django.http import JsonResponse
from django.contrib.auth.models import User
from django.views.decorators.csrf import csrf_exempt
from decouple import config


@csrf_exempt
def initial_setup(request):
    """One-time setup endpoint to create superadmin. Protected by SETUP_TOKEN env var."""
    setup_token = config('SETUP_TOKEN', default='')
    if not setup_token:
        return JsonResponse({'error': 'SETUP_TOKEN not configured'}, status=403)

    token = request.GET.get('token', '')
    if token != setup_token:
        return JsonResponse({'error': 'Invalid token'}, status=403)

    # Run migrations first (can't run at build time on Vercel)
    from django.core.management import call_command
    call_command('migrate', '--no-input')

    if User.objects.filter(profile__app_role='superadmin').exists():
        return JsonResponse({'message': 'Superadmin already exists. Setup not needed.'})

    from band.models import Church, UserProfile

    church = Church.objects.create(name='Default Church', slug='default-church')

    email = request.GET.get('email', 'admin@worshipflow.app')
    password = request.GET.get('password', 'changeme123')

    user = User.objects.create_user(
        username=email.lower(),
        email=email.lower(),
        password=password,
        first_name='Admin',
        last_name='User',
    )

    profile = user.profile
    profile.app_role = 'superadmin'
    profile.church = church
    profile.must_change_password = True
    profile.save()

    return JsonResponse({
        'message': 'Setup complete!',
        'email': email,
        'church': church.name,
        'note': 'You will be prompted to change your password on first login. Delete the SETUP_TOKEN env var now.'
    })


urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/', auth_views.LoginView.as_view(template_name='band/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('setup/', initial_setup, name='initial_setup'),
    path('', include('band.urls')),
]
