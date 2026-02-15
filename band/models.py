from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class Church(models.Model):
    """A church/organization that owns its own isolated data"""
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = "Churches"

    def __str__(self):
        return self.name


class UserProfile(models.Model):
    """Extends Django User with church membership and role"""
    ROLE_CHOICES = [
        ('superadmin', 'Super Admin'),
        ('admin', 'Admin (Worship Leader)'),
        ('user', 'User (Band Member)'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name='members', null=True, blank=True)
    app_role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='user')
    must_change_password = models.BooleanField(default=False)

    def __str__(self):
        church_name = self.church.name if self.church else 'No Church'
        return f"{self.user.username} - {self.get_app_role_display()} ({church_name})"

    @property
    def is_superadmin(self):
        return self.app_role == 'superadmin'

    @property
    def is_admin(self):
        return self.app_role in ('admin', 'superadmin')

    @property
    def is_user(self):
        return self.app_role == 'user'


class Person(models.Model):
    """Band member with their roles, instruments, and vocal abilities"""

    ROLE_CHOICES = [
        ('vocalist', 'Vocalist'),
        ('instrumentalist', 'Instrumentalist'),
        ('both', 'Both'),
    ]

    FREQUENCY_CHOICES = [
        ('core', 'Core'),
        ('regular', 'Regular'),
        ('occasional', 'Occasional'),
    ]

    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name='people')
    person_id = models.CharField(max_length=10, help_text="e.g., P001")
    name = models.CharField(max_length=100)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)

    # Instruments
    primary_instrument = models.CharField(max_length=100, blank=True, null=True)
    secondary_instrument = models.CharField(max_length=100, blank=True, null=True)

    # Vocal abilities
    lead_vocal = models.BooleanField(default=False)
    harmony_vocal = models.BooleanField(default=False)
    preferred_keys = models.CharField(max_length=100, blank=True, help_text="e.g., E, F, G, Bb")

    # Additional info
    style_strengths = models.TextField(blank=True, help_text="Musical style strengths")
    frequency = models.CharField(max_length=20, choices=FREQUENCY_CHOICES, blank=True)
    availability = models.TextField(blank=True, help_text="Availability notes")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = "People"
        unique_together = [('church', 'person_id')]

    def __str__(self):
        return f"{self.name} ({self.person_id})"


class Song(models.Model):
    """Song library with keys, tempo, and usage tracking"""

    TEMPO_CHOICES = [
        ('slow', 'Slow'),
        ('med_slow', 'Med Slow'),
        ('medium', 'Medium'),
        ('med_fast', 'Med Fast'),
        ('fast', 'Fast'),
    ]

    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name='songs')
    song_id = models.CharField(max_length=10, help_text="e.g., S001")
    title = models.CharField(max_length=200)
    artist = models.CharField(max_length=200, blank=True)
    default_key = models.CharField(max_length=10, help_text="e.g., D, G, A")
    tempo = models.CharField(max_length=20, choices=TEMPO_CHOICES, blank=True)
    bpm = models.IntegerField(blank=True, null=True, help_text="Beats per minute")
    style = models.CharField(max_length=100, blank=True)

    # Tracking
    arrangement_notes = models.TextField(blank=True)
    last_used = models.DateField(blank=True, null=True)
    times_used = models.IntegerField(default=0)
    comfort_level = models.CharField(max_length=50, blank=True, help_text="Band's comfort level")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['title']
        unique_together = [('church', 'song_id')]

    def __str__(self):
        return f"{self.title} - {self.artist} ({self.song_id})"


class PersonSongPreference(models.Model):
    """Junction table linking people to songs they can perform"""

    CONFIDENCE_CHOICES = [
        ('high', 'High'),
        ('medium', 'Medium'),
        ('low', 'Low'),
    ]

    entry_id = models.CharField(max_length=10, unique=True, help_text="e.g., E001")
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='song_preferences')
    song = models.ForeignKey(Song, on_delete=models.CASCADE, related_name='person_preferences')

    preferred_key = models.CharField(max_length=10, blank=True, help_text="Preferred key for this person")
    can_lead = models.BooleanField(default=False, verbose_name="Can Lead")
    confidence = models.CharField(max_length=20, choices=CONFIDENCE_CHOICES, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['person__name', 'song__title']
        verbose_name = "Person-Song Preference"
        unique_together = ['person', 'song']

    def __str__(self):
        return f"{self.person.name} - {self.song.title} (Key: {self.preferred_key})"


class Service(models.Model):
    """Worship service (completed or planned)"""

    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name='services')
    plan_id = models.CharField(max_length=10, help_text="e.g., SV001")
    service_date = models.DateField()
    service_name = models.CharField(max_length=200, help_text="e.g., Sunday Morning Worship")

    band_notes = models.TextField(blank=True, help_text="Notes for the band")
    service_notes = models.TextField(blank=True, help_text="General service notes")

    class Meta:
        ordering = ['-service_date']
        unique_together = [('church', 'plan_id')]

    def __str__(self):
        return f"{self.service_name} - {self.service_date}"


class ServiceSong(models.Model):
    """Songs used in a specific service"""

    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='songs')
    song = models.ForeignKey(Song, on_delete=models.CASCADE)

    song_order = models.IntegerField(help_text="Order in the service (1, 2, 3...)")
    key_used = models.CharField(max_length=10, help_text="Key used in this service")
    length = models.IntegerField(blank=True, null=True, help_text="Length in minutes")
    lead_person = models.ForeignKey(Person, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['service', 'song_order']
        unique_together = ['service', 'song_order']

    def __str__(self):
        return f"{self.service.service_name} - {self.song_order}. {self.song.title}"
