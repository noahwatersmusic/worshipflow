from django.contrib import admin
from .models import Church, UserProfile, Person, Song, PersonSongPreference, Service, ServiceSong


@admin.register(Church)
class ChurchAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'is_active', 'created_at']
    list_filter = ['is_active']
    search_fields = ['name', 'slug']
    prepopulated_fields = {'slug': ('name',)}


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'church', 'app_role']
    list_filter = ['app_role', 'church']
    search_fields = ['user__username', 'user__email']
    autocomplete_fields = ['user', 'church']


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ['name', 'person_id', 'church', 'role', 'primary_instrument', 'lead_vocal', 'harmony_vocal', 'frequency']
    list_filter = ['church', 'role', 'lead_vocal', 'harmony_vocal', 'frequency']
    search_fields = ['name', 'person_id', 'primary_instrument', 'secondary_instrument']
    fieldsets = (
        ('Basic Info', {
            'fields': ('person_id', 'name', 'role', 'frequency')
        }),
        ('Instruments', {
            'fields': ('primary_instrument', 'secondary_instrument')
        }),
        ('Vocal Abilities', {
            'fields': ('lead_vocal', 'harmony_vocal', 'preferred_keys')
        }),
        ('Additional Info', {
            'fields': ('style_strengths', 'availability', 'notes'),
            'classes': ('collapse',)
        }),
    )


@admin.register(Song)
class SongAdmin(admin.ModelAdmin):
    list_display = ['title', 'song_id', 'church', 'artist', 'default_key', 'tempo', 'last_used', 'times_used']
    list_filter = ['church', 'tempo', 'default_key', 'last_used']
    search_fields = ['title', 'artist', 'song_id']
    date_hierarchy = 'last_used'
    fieldsets = (
        ('Basic Info', {
            'fields': ('song_id', 'title', 'artist')
        }),
        ('Musical Details', {
            'fields': ('default_key', 'tempo', 'style')
        }),
        ('Usage Tracking', {
            'fields': ('last_used', 'times_used', 'comfort_level')
        }),
        ('Notes', {
            'fields': ('arrangement_notes', 'notes'),
            'classes': ('collapse',)
        }),
    )


class PersonSongPreferenceInline(admin.TabularInline):
    model = PersonSongPreference
    extra = 1
    fields = ['song', 'preferred_key', 'can_lead', 'confidence', 'notes']
    autocomplete_fields = ['song']


@admin.register(PersonSongPreference)
class PersonSongPreferenceAdmin(admin.ModelAdmin):
    list_display = ['person', 'song', 'preferred_key', 'can_lead', 'confidence']
    list_filter = ['can_lead', 'confidence', 'person__role']
    search_fields = ['person__name', 'song__title', 'entry_id']
    autocomplete_fields = ['person', 'song']
    fieldsets = (
        ('Link', {
            'fields': ('entry_id', 'person', 'song')
        }),
        ('Preferences', {
            'fields': ('preferred_key', 'can_lead', 'confidence', 'notes')
        }),
    )


class ServiceSongInline(admin.TabularInline):
    model = ServiceSong
    extra = 1
    fields = ['song_order', 'song', 'key_used', 'lead_person', 'length']
    autocomplete_fields = ['song', 'lead_person']


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ['service_name', 'service_date', 'plan_id', 'church']
    list_filter = ['church', 'service_date']
    search_fields = ['service_name', 'plan_id']
    date_hierarchy = 'service_date'
    inlines = [ServiceSongInline]
    fieldsets = (
        ('Service Info', {
            'fields': ('plan_id', 'service_date', 'service_name')
        }),
        ('Notes', {
            'fields': ('band_notes', 'service_notes')
        }),
    )


@admin.register(ServiceSong)
class ServiceSongAdmin(admin.ModelAdmin):
    list_display = ['service', 'song_order', 'song', 'key_used', 'lead_person']
    list_filter = ['service__service_date', 'key_used']
    search_fields = ['service__service_name', 'song__title', 'lead_person__name']
    autocomplete_fields = ['service', 'song', 'lead_person']
