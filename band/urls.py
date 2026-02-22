from django.urls import path
from . import views

app_name = 'band'

urlpatterns = [
    path('', views.home, name='home'),
    path('people/', views.people_list, name='people_list'),
    path('people/add/', views.person_add, name='person_add'),
    path('people/<str:person_id>/', views.person_detail, name='person_detail'),
    path('people/<str:person_id>/edit/review/', views.person_edit_review, name='person_edit_review'),
    path('people/<str:person_id>/edit/confirm/', views.person_edit_confirm, name='person_edit_confirm'),
    path('people/<str:person_id>/delete/', views.person_delete, name='person_delete'),
    path('people/<str:person_id>/delete/confirm/', views.person_delete_confirm, name='person_delete_confirm'),
    path('songs/', views.songs_list, name='songs_list'),
    path('songs/add/', views.song_add, name='song_add'),
    path('songs/refresh-keys/', views.refresh_song_keys, name='refresh_song_keys'),
    path('songs/<str:song_id>/', views.song_detail, name='song_detail'),
    path('songs/<str:song_id>/edit/review/', views.song_edit_review, name='song_edit_review'),
    path('songs/<str:song_id>/edit/confirm/', views.song_edit_confirm, name='song_edit_confirm'),
    path('songs/<str:song_id>/delete/', views.song_delete, name='song_delete'),
    path('songs/<str:song_id>/delete/confirm/', views.song_delete_confirm, name='song_delete_confirm'),
    path('services/', views.services_list, name='services_list'),
    path('services/add/', views.service_add, name='service_add'),
    path('services/<str:plan_id>/', views.service_detail, name='service_detail'),
    path('services/<str:plan_id>/edit/', views.service_edit, name='service_edit'),
    path('services/<str:plan_id>/delete/', views.service_delete, name='service_delete'),
    path('services/<str:plan_id>/delete/confirm/', views.service_delete_confirm, name='service_delete_confirm'),
    path('preferences/<str:entry_id>/edit/', views.preference_edit, name='preference_edit'),
    path('song-finder/', views.song_finder, name='song_finder'),
    path('import/', views.import_services, name='import_services'),
    path('import/confirm-pdf/', views.confirm_pdf_import, name='confirm_pdf_import'),
    path('download-template/', views.download_csv_template, name='download_csv_template'),
    # SuperAdmin: Church management
    path('churches/', views.church_list, name='church_list'),
    path('churches/add/', views.church_add, name='church_add'),
    path('churches/<int:church_id>/edit/', views.church_edit, name='church_edit'),
    # SuperAdmin: User management
    path('users/', views.user_list, name='user_list'),
    path('users/add/', views.user_add, name='user_add'),
    path('users/<int:user_id>/edit/', views.user_edit, name='user_edit'),
    # SuperAdmin: Church switcher
    path('switch-church/', views.switch_church, name='switch_church'),
    # Password change (first login)
    path('change-password/', views.change_password, name='change_password'),
    # Backward compatibility aliases
    path('import-csv/', views.import_services, name='import_csv'),
    path('import-pdf/', views.import_services, name='import_pdf'),
]
