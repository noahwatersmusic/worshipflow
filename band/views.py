from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Count, Max
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.utils.text import slugify
from django.utils.crypto import get_random_string
from django.core.mail import send_mail
from django.conf import settings
from .models import Church, UserProfile, Person, Song, PersonSongPreference, Service, ServiceSong
from .decorators import admin_required, superadmin_required
import csv
import io
from datetime import datetime
import requests
import re
import json
from bs4 import BeautifulSoup
import pdfplumber


def parse_song_length_to_seconds(song_length_str, service_song_length_int=None):
    """Parse song length to total seconds.
    Prefers Song.length (M:SS string), falls back to ServiceSong.length (int minutes).
    """
    if song_length_str:
        try:
            parts = song_length_str.strip().split(':')
            return int(parts[0]) * 60 + int(parts[1])
        except (ValueError, IndexError):
            pass
    if service_song_length_int is not None:
        return service_song_length_int * 60
    return 0


def format_service_length(total_seconds):
    """Format total seconds into a human-readable service length string."""
    if total_seconds == 0:
        return None
    total_min = total_seconds // 60
    if total_min >= 60:
        return f"{total_min // 60}h {total_min % 60}m"
    return f"{total_min} min"


def get_active_church(request):
    """Get the active church for the current user.
    SuperAdmin: reads from session (church switcher).
    Admin/User: returns their profile's church.
    """
    try:
        profile = request.user.profile
    except Exception:
        return None

    if profile.is_superadmin:
        church_id = request.session.get('active_church_id')
        if church_id:
            try:
                return Church.objects.get(id=church_id, is_active=True)
            except Church.DoesNotExist:
                return None
        return None
    else:
        return profile.church


def fetch_song_info_from_internet(title, artist):
    """
    Search PraiseCharts for original key, tempo, and BPM of a worship song.
    Returns a dict with 'key', 'tempo', and 'bpm' (any may be None).
    """
    result = {'key': None, 'tempo': None, 'bpm': None, 'length': None}
    try:
        if not title:
            return result

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }

        # PraiseCharts tempo labels → model tempo choices
        TEMPO_MAP = {
            'slow': 'slow',
            'med slow': 'med_slow',
            'medium': 'medium',
            'med fast': 'med_fast',
            'fast': 'fast',
        }

        # Search PraiseCharts
        search_url = f'https://www.praisecharts.com/search?q={requests.utils.quote(title)}'
        r = requests.get(search_url, headers=headers, timeout=10)
        if r.status_code != 200:
            return result

        soup = BeautifulSoup(r.text, 'html.parser')

        # Find song detail links from search results
        song_links = []
        for a in soup.find_all('a', href=True):
            if '/songs/details/' in a['href'] and 'chords' in a['href']:
                href = a['href']
                text = a.get_text().strip()
                if href not in [l[1] for l in song_links]:
                    song_links.append((text, href))

        if not song_links:
            return result

        # Pick best match — prefer one that matches artist name
        best_link = song_links[0][1]
        if artist:
            artist_lower = artist.lower()
            for text, href in song_links:
                if artist_lower.split()[0] in text.lower():
                    best_link = href
                    break

        # Fetch the song detail page
        detail_url = f'https://www.praisecharts.com{best_link}' if best_link.startswith('/') else best_link
        r2 = requests.get(detail_url, headers=headers, timeout=10)
        if r2.status_code != 200:
            return result

        page_text = r2.text

        # Try to extract from embedded JSON first (most reliable)
        key_json = re.search(r'"original_key":"([^"]+)"', page_text)
        if key_json:
            key = key_json.group(1).replace('♭', 'b').replace('♯', '#')
            if re.match(r'^[A-G][#b]?m?$', key):
                result['key'] = key

        tempo_json = re.search(r'"tempo":\{[^}]*"tempo":"([^"]+)"', page_text)
        if tempo_json:
            tempo_label = tempo_json.group(1).strip().lower()
            result['tempo'] = TEMPO_MAP.get(tempo_label)

        bpm_json = re.search(r'"bpm":"(\d+)"', page_text)
        if bpm_json:
            result['bpm'] = int(bpm_json.group(1))

        # Try to extract song length
        # First: look for formatted time string (M:SS or MM:SS) in JSON or HTML
        for length_pattern in [
            r'"duration":"(\d{1,2}:\d{2})"',
            r'"length":"(\d{1,2}:\d{2})"',
            r'"time":"(\d{1,2}:\d{2})"',
            r'"songLength":"(\d{1,2}:\d{2})"',
        ]:
            length_match = re.search(length_pattern, page_text, re.IGNORECASE)
            if length_match:
                result['length'] = length_match.group(1)
                break

        # Second: look for duration stored as integer seconds (e.g. "duration":288)
        if not result['length']:
            for sec_pattern in [
                r'"duration":(\d{2,3})[,}\]]',
                r'"length":(\d{2,3})[,}\]]',
                r'"songLength":(\d{2,3})[,}\]]',
                r'"lengthInSeconds":(\d{2,3})[,}\]]',
            ]:
                sec_match = re.search(sec_pattern, page_text)
                if sec_match:
                    total_seconds = int(sec_match.group(1))
                    if 60 <= total_seconds <= 900:  # sanity check: 1–15 minutes
                        minutes = total_seconds // 60
                        seconds = total_seconds % 60
                        result['length'] = f"{minutes}:{seconds:02d}"
                        break

        # Third: plain text fallback — look for time patterns near duration/length keywords
        if not result['length']:
            plain_text = BeautifulSoup(page_text, 'html.parser').get_text()
            for text_pattern in [
                r'(?:Duration|Length|Time)[^\d]{0,20}(\d{1,2}:\d{2})',
                r'(\d{1,2}:\d{2})(?:[^\d]{0,20}(?:min|duration|length))',
            ]:
                text_match = re.search(text_pattern, plain_text, re.IGNORECASE)
                if text_match:
                    result['length'] = text_match.group(1)
                    break

        # Fallback for key: parse HTML text
        if not result['key']:
            match = re.search(r'Original Key\s*</?\w*>?\s*([A-G][#b♭♯]?m?)', page_text)
            if not match:
                plain = BeautifulSoup(page_text, 'html.parser').get_text()
                match = re.search(r'Original Key\s+([A-G][#b♭♯]?m?)', plain)
            if match:
                key = match.group(1).replace('♭', 'b').replace('♯', '#')
                if re.match(r'^[A-G][#b]?m?$', key):
                    result['key'] = key

        return result

    except Exception:
        return result


def fetch_song_key_from_internet(title, artist):
    """Convenience wrapper that returns just the key string."""
    return fetch_song_info_from_internet(title, artist)['key']


@login_required
def home(request):
    """Home page with overview statistics"""
    church = get_active_church(request)
    if not church:
        messages.info(request, 'Please select a church to get started.')
        return render(request, 'band/home.html', {'no_church': True})

    context = {
        'total_people': Person.objects.filter(church=church).count(),
        'total_songs': Song.objects.filter(church=church).count(),
        'vocalists': Person.objects.filter(church=church).filter(Q(lead_vocal=True) | Q(harmony_vocal=True)).count(),
        'recent_services': Service.objects.filter(church=church)[:5],
    }
    return render(request, 'band/home.html', context)


@login_required
def people_list(request):
    """List all band members with filtering options"""
    church = get_active_church(request)
    if not church:
        return redirect('band:home')
    people = Person.objects.filter(church=church)

    # Filter by role
    role = request.GET.get('role')
    if role:
        people = people.filter(role=role)

    # Filter by vocal ability
    vocal = request.GET.get('vocal')
    if vocal == 'lead':
        people = people.filter(lead_vocal=True)
    elif vocal == 'harmony':
        people = people.filter(harmony_vocal=True)

    # Filter by frequency
    frequency = request.GET.get('frequency')
    if frequency:
        people = people.filter(frequency=frequency)

    # Search by name
    search = request.GET.get('search')
    if search:
        people = people.filter(Q(name__icontains=search) | Q(person_id__icontains=search))

    context = {
        'people': people,
        'role_choices': Person.ROLE_CHOICES,
        'frequency_choices': Person.FREQUENCY_CHOICES,
    }
    return render(request, 'band/people_list.html', context)


@login_required
def person_detail(request, person_id):
    """Detail view for a specific band member"""
    church = get_active_church(request)
    if not church:
        return redirect('band:home')
    person = get_object_or_404(Person, person_id=person_id, church=church)
    song_preferences = PersonSongPreference.objects.filter(person=person).select_related('song')

    context = {
        'person': person,
        'song_preferences': song_preferences,
        'role_choices': Person.ROLE_CHOICES,
        'frequency_choices': Person.FREQUENCY_CHOICES,
    }
    return render(request, 'band/person_detail.html', context)


@login_required
@admin_required
def person_edit_review(request, person_id):
    """Show review page for person edits before saving"""
    church = get_active_church(request)
    if not church:
        return redirect('band:home')
    person = get_object_or_404(Person, person_id=person_id, church=church)

    if request.method != 'POST':
        return redirect('band:person_detail', person_id=person_id)

    # Get form data
    new_data = {
        'name': request.POST.get('name', '').strip(),
        'role': request.POST.get('role', ''),
        'frequency': request.POST.get('frequency', ''),
        'primary_instrument': request.POST.get('primary_instrument', '').strip(),
        'secondary_instrument': request.POST.get('secondary_instrument', '').strip(),
        'lead_vocal': 'lead_vocal' in request.POST,
        'harmony_vocal': 'harmony_vocal' in request.POST,
        'preferred_keys': request.POST.get('preferred_keys', '').strip(),
        'style_strengths': request.POST.get('style_strengths', '').strip(),
        'availability': request.POST.get('availability', '').strip(),
        'notes': request.POST.get('notes', '').strip(),
    }

    # Get display values for role and frequency
    role_display = dict(Person.ROLE_CHOICES).get(new_data['role'], new_data['role'])
    frequency_display = dict(Person.FREQUENCY_CHOICES).get(new_data['frequency'], new_data['frequency'])
    new_data['role_display'] = role_display
    new_data['frequency_display'] = frequency_display

    # Compare with current values and track changes
    changes = []
    changed_fields = []

    field_mappings = [
        ('name', 'Name', person.name),
        ('role', 'Role', person.role),
        ('frequency', 'Frequency', person.frequency or ''),
        ('primary_instrument', 'Primary Instrument', person.primary_instrument or ''),
        ('secondary_instrument', 'Secondary Instrument', person.secondary_instrument or ''),
        ('lead_vocal', 'Lead Vocal', person.lead_vocal),
        ('harmony_vocal', 'Harmony Vocal', person.harmony_vocal),
        ('preferred_keys', 'Preferred Keys', person.preferred_keys or ''),
        ('style_strengths', 'Style Strengths', person.style_strengths or ''),
        ('availability', 'Availability', person.availability or ''),
        ('notes', 'Notes', person.notes or ''),
    ]

    for field_name, display_name, old_value in field_mappings:
        new_value = new_data[field_name]

        # Handle boolean display
        if isinstance(old_value, bool):
            old_display = 'Yes' if old_value else 'No'
            new_display = 'Yes' if new_value else 'No'
        elif field_name == 'role':
            old_display = dict(Person.ROLE_CHOICES).get(old_value, old_value)
            new_display = role_display
        elif field_name == 'frequency':
            old_display = dict(Person.FREQUENCY_CHOICES).get(old_value, old_value) if old_value else ''
            new_display = frequency_display if new_value else ''
        else:
            old_display = old_value
            new_display = new_value

        if str(new_value) != str(old_value):
            changes.append({
                'field': display_name,
                'old_value': old_display,
                'new_value': new_display,
            })
            changed_fields.append(display_name)

    context = {
        'person': person,
        'new_data': new_data,
        'changes': changes,
        'changed_fields': changed_fields,
    }
    return render(request, 'band/person_edit_review.html', context)


@login_required
@admin_required
def person_edit_confirm(request, person_id):
    """Confirm and save person edits"""
    church = get_active_church(request)
    if not church:
        return redirect('band:home')
    person = get_object_or_404(Person, person_id=person_id, church=church)

    if request.method != 'POST':
        return redirect('band:person_detail', person_id=person_id)

    try:
        # Update person fields
        person.name = request.POST.get('name', '').strip()
        person.role = request.POST.get('role', '')
        person.frequency = request.POST.get('frequency', '') or None
        person.primary_instrument = request.POST.get('primary_instrument', '').strip() or None
        person.secondary_instrument = request.POST.get('secondary_instrument', '').strip() or None
        person.lead_vocal = request.POST.get('lead_vocal', '') == 'True'
        person.harmony_vocal = request.POST.get('harmony_vocal', '') == 'True'
        person.preferred_keys = request.POST.get('preferred_keys', '').strip()
        person.style_strengths = request.POST.get('style_strengths', '').strip()
        person.availability = request.POST.get('availability', '').strip()
        person.notes = request.POST.get('notes', '').strip()

        person.save()
        messages.success(request, f'Successfully updated {person.name}.')

    except Exception as e:
        messages.error(request, f'Error saving changes: {str(e)}')

    return redirect('band:person_detail', person_id=person_id)


@login_required
def songs_list(request):
    """List all songs with filtering options"""
    church = get_active_church(request)
    if not church:
        return redirect('band:home')
    songs = Song.objects.filter(church=church).annotate(
        _service_last_used=Max('servicesong__service__service_date'),
        computed_times_used=Count('servicesong')
    ).annotate(
        computed_last_used=Coalesce('_service_last_used', 'last_used')
    )

    # Filter by key
    key = request.GET.get('key')
    if key:
        songs = songs.filter(default_key=key)

    # Filter by tempo
    tempo = request.GET.get('tempo')
    if tempo:
        songs = songs.filter(tempo=tempo)

    # Filter by artist
    artist = request.GET.get('artist')
    if artist:
        songs = songs.filter(artist__icontains=artist)

    # Search by title
    search = request.GET.get('search')
    if search:
        songs = songs.filter(Q(title__icontains=search) | Q(artist__icontains=search))

    # Get unique keys and artists for filter dropdowns
    all_keys = Song.objects.filter(church=church).values_list('default_key', flat=True).distinct().order_by('default_key')
    all_artists = Song.objects.filter(church=church).values_list('artist', flat=True).distinct().order_by('artist')

    context = {
        'songs': songs,
        'tempo_choices': Song.TEMPO_CHOICES,
        'all_keys': all_keys,
        'all_artists': all_artists,
    }
    return render(request, 'band/songs_list.html', context)


@login_required
def song_detail(request, song_id):
    """Detail view for a specific song"""
    church = get_active_church(request)
    if not church:
        return redirect('band:home')
    song = get_object_or_404(Song.objects.filter(church=church).annotate(
        _service_last_used=Max('servicesong__service__service_date'),
        computed_times_used=Count('servicesong')
    ).annotate(
        computed_last_used=Coalesce('_service_last_used', 'last_used')
    ), song_id=song_id)
    person_preferences = PersonSongPreference.objects.filter(song=song).select_related('person')

    # Separate by who can lead vs just sing/play
    can_lead = person_preferences.filter(can_lead=True)
    cannot_lead = person_preferences.filter(can_lead=False)

    context = {
        'song': song,
        'can_lead': can_lead,
        'cannot_lead': cannot_lead,
        'tempo_choices': Song.TEMPO_CHOICES,
    }
    return render(request, 'band/song_detail.html', context)


@login_required
def song_finder(request):
    """Advanced search to find songs by singer and key"""
    church = get_active_church(request)
    if not church:
        return redirect('band:home')

    results = None
    selected_person = None

    person_id = request.GET.get('person')
    key = request.GET.get('key')
    can_lead = request.GET.get('can_lead')

    if person_id:
        selected_person = get_object_or_404(Person, person_id=person_id, church=church)
        results = PersonSongPreference.objects.filter(person=selected_person).select_related('song')

        if key:
            results = results.filter(Q(preferred_key=key) | Q(song__default_key=key))

        if can_lead == 'yes':
            results = results.filter(can_lead=True)

    people = Person.objects.filter(church=church).filter(Q(lead_vocal=True) | Q(harmony_vocal=True)).order_by('name')
    all_keys = Song.objects.filter(church=church).values_list('default_key', flat=True).distinct().order_by('default_key')

    context = {
        'people': people,
        'all_keys': all_keys,
        'results': results,
        'selected_person': selected_person,
    }
    return render(request, 'band/song_finder.html', context)


@login_required
@admin_required
def import_services(request):
    """Unified import page for CSV and PDF files"""
    if request.method == 'POST':
        import_type = request.POST.get('import_type', '')

        if import_type == 'csv' and request.FILES.getlist('csv_file'):
            return handle_csv_import(request)
        elif import_type == 'pdf' and request.FILES.getlist('pdf_file'):
            return handle_pdf_import(request)

    return render(request, 'band/import.html')


def handle_csv_import(request):
    """Handle CSV file import (supports multiple files)"""
    church = get_active_church(request)
    if not church:
        messages.error(request, 'Please select a church first.')
        return redirect('band:home')

    csv_files = request.FILES.getlist('csv_file')
    all_errors = []
    total_imported = 0
    multi = len(csv_files) > 1

    for csv_file in csv_files:
        file_prefix = f'{csv_file.name}: ' if multi else ''

        if not csv_file.name.endswith('.csv'):
            all_errors.append(f'{csv_file.name}: Not a CSV file.')
            continue

        try:
            # Read and decode CSV file
            file_data = csv_file.read().decode('utf-8')
            csv_reader = csv.DictReader(io.StringIO(file_data))

            # Group rows by service (Service Date + Service Name)
            services_data = {}
            errors = []
            row_num = 2

            for row in csv_reader:
                try:
                    # Parse service date
                    service_date = datetime.strptime(row['Service Date'], '%Y-%m-%d').date()
                    service_name = row['Service Name']

                    # Create unique key for this service
                    service_key = (service_date, service_name)

                    # Initialize service data if first time seeing this service
                    if service_key not in services_data:
                        services_data[service_key] = {
                            'service_date': service_date,
                            'service_name': service_name,
                            'band_notes': row.get('Band Notes', ''),
                            'service_notes': row.get('Service Notes', ''),
                            'songs': []
                        }

                    # Add song to this service if specified
                    if row.get('Song ID') and row.get('Song Order'):
                        song_data = {
                            'song_id': row['Song ID'],
                            'song_title': row.get('Song Title', ''),
                            'song_artist': row.get('Song Artist', ''),
                            'song_default_key': row.get('Song Default Key', ''),
                            'song_order': row['Song Order'],
                            'key_used': row.get('Key Used', ''),
                            'length': row.get('Length', ''),
                            'lead_person_id': row.get('Lead Person ID', ''),
                            'row_num': row_num
                        }
                        services_data[service_key]['songs'].append(song_data)

                except KeyError as e:
                    errors.append(f"{file_prefix}Row {row_num}: Missing required field - {str(e)}")
                except ValueError as e:
                    errors.append(f"{file_prefix}Row {row_num}: Invalid date format - {str(e)}")
                except Exception as e:
                    errors.append(f"{file_prefix}Row {row_num}: Error - {str(e)}")

                row_num += 1

            # Generate Plan IDs and create services
            imported_count = 0

            # Get the next available Plan ID number (scoped to church)
            last_service = Service.objects.filter(church=church).order_by('-plan_id').first()
            if last_service and last_service.plan_id.startswith('SV'):
                try:
                    next_num = int(last_service.plan_id[2:]) + 1
                except:
                    next_num = 1
            else:
                next_num = 1

            # Create each service
            for service_key, service_info in services_data.items():
                # Generate Plan ID
                plan_id = f"SV{next_num:03d}"
                next_num += 1

                # Create service
                service = Service.objects.create(
                    plan_id=plan_id,
                    service_date=service_info['service_date'],
                    service_name=service_info['service_name'],
                    band_notes=service_info['band_notes'],
                    service_notes=service_info['service_notes'],
                    church=church,
                )

                # Add songs to service
                for song_data in service_info['songs']:
                    try:
                        # Try to get existing song
                        try:
                            song = Song.objects.get(song_id=song_data['song_id'], church=church)
                        except Song.DoesNotExist:
                            # Create new song if it doesn't exist
                            if song_data['song_title'] and song_data['song_default_key']:
                                # Try to fetch song info from PraiseCharts
                                song_info = fetch_song_info_from_internet(
                                    song_data['song_title'],
                                    song_data['song_artist']
                                )

                                # Use fetched key if found, otherwise use CSV-provided key
                                final_key = song_info['key'] if song_info['key'] else song_data['song_default_key']

                                song = Song.objects.create(
                                    song_id=song_data['song_id'],
                                    title=song_data['song_title'],
                                    artist=song_data['song_artist'],
                                    default_key=final_key,
                                    tempo=song_info['tempo'] or '',
                                    bpm=song_info['bpm'],
                                    church=church,
                                )

                                # Log which key was used
                                if song_info['key']:
                                    messages.info(request, f"Created '{song_data['song_title']}' with original key {final_key} (found online)")
                                else:
                                    messages.info(request, f"Created '{song_data['song_title']}' with key {final_key} (from CSV)")
                            else:
                                errors.append(f"{file_prefix}Row {song_data['row_num']}: Song {song_data['song_id']} not found. To create it, provide Song Title and Song Default Key.")
                                continue

                        lead_person = None
                        if song_data['lead_person_id']:
                            try:
                                lead_person = Person.objects.get(person_id=song_data['lead_person_id'], church=church)
                            except Person.DoesNotExist:
                                # Silently skip if lead person not found - just populate songs and services
                                pass

                        ServiceSong.objects.create(
                            service=service,
                            song=song,
                            song_order=int(song_data['song_order']),
                            key_used=song_data['key_used'],
                            length=int(song_data['length']) if song_data['length'] else None,
                            lead_person=lead_person
                        )

                        # Update song's last_used and times_used
                        if song.last_used is None or service_info['service_date'] > song.last_used:
                            song.last_used = service_info['service_date']
                        song.times_used += 1
                        song.save()

                        # If there's a lead person, add/update their song preference
                        if lead_person:
                            pref, created = PersonSongPreference.objects.get_or_create(
                                person=lead_person,
                                song=song,
                                defaults={
                                    'entry_id': f"E{PersonSongPreference.objects.count() + 1:03d}",
                                    'preferred_key': song_data['key_used'] if song_data['key_used'] else song.default_key,
                                    'can_lead': True,
                                    'confidence': 'high',
                            }
                        )
                        if not created and not pref.can_lead:
                            # Update existing preference to mark they can lead
                            pref.can_lead = True
                            pref.save()

                    except ValueError as e:
                        errors.append(f"{file_prefix}Row {song_data['row_num']}: Invalid number format - {str(e)}")

                imported_count += 1

            total_imported += imported_count
            all_errors.extend(errors)

            if multi and imported_count > 0:
                messages.success(request, f'{csv_file.name}: Imported {imported_count} service(s).')

        except Exception as e:
            all_errors.append(f'{csv_file.name}: Error processing file: {str(e)}')

    # Display aggregate results
    if total_imported > 0 and not multi:
        messages.success(request, f'Successfully imported {total_imported} service(s).')
    elif total_imported > 0:
        messages.success(request, f'Total: {total_imported} service(s) imported from {len(csv_files)} file(s).')
    for error in all_errors[:10]:
        messages.warning(request, error)
    if len(all_errors) > 10:
        messages.warning(request, f'...and {len(all_errors) - 10} more errors')

    return redirect('band:import_services')


@login_required
@admin_required
def download_csv_template(request):
    """Download CSV template file"""
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="service_import_template.csv"'

    writer = csv.writer(response)

    # Write header
    writer.writerow([
        'Service Date',
        'Service Name',
        'Song Order',
        'Song ID',
        'Song Title',
        'Song Artist',
        'Song Default Key',
        'Key Used',
        'Length',
        'Lead Person ID',
        'Band Notes',
        'Service Notes'
    ])

    # Write example rows
    writer.writerow([
        '2025-02-02',
        'Sunday Morning Worship',
        '1',
        'S001',
        'Trust in God',
        'Elevation Worship',
        'D',
        'D',
        '5',
        'P004',
        'Start with slow intro',
        'Communion service'
    ])

    writer.writerow([
        '2025-02-02',
        'Sunday Morning Worship',
        '2',
        'S002',
        'Faithful Now',
        'Vertical Worship',
        'G',
        'G',
        '4',
        'P002',
        '',
        ''
    ])

    writer.writerow([
        '2025-02-09',
        'Sunday Evening Service',
        '1',
        'S006',
        'Oceans',
        'Hillsong United',
        'A',
        'A',
        '6',
        'P007',
        '',
        'Youth focus'
    ])

    return response


def parse_multiple_leaders(lead_text):
    """
    Parse a lead text that may contain multiple leaders.
    Handles formats like:
    - "Bill leads"
    - "Bill and Sarah lead"
    - "Bill, Sarah lead"
    - "Bill, Sarah, and Mike lead"
    - "Bill & Sarah"
    Returns a list of leader names.
    """
    if not lead_text:
        return []

    # Clean up the text
    lead_text = lead_text.strip()

    # Remove common suffixes
    lead_text = re.sub(r'\s*(leads?|vocals?|singing)\s*$', '', lead_text, flags=re.IGNORECASE)

    # Split on common separators: "and", "&", ","
    # First normalize "and" and "&" to commas
    lead_text = re.sub(r'\s+and\s+', ', ', lead_text, flags=re.IGNORECASE)
    lead_text = re.sub(r'\s*&\s*', ', ', lead_text)

    # Split on commas and clean up
    leaders = [name.strip() for name in lead_text.split(',') if name.strip()]

    return leaders


def match_leaders_to_people(leaders, people):
    """
    Match a list of leader names to Person objects.
    Returns a list of matched person IDs.
    """
    matched_ids = []

    for leader_name in leaders:
        leader_lower = leader_name.lower()
        for person in people:
            person_name_lower = person.name.lower()
            # Match if leader name is contained in person name, or first name matches
            first_name = person_name_lower.split()[0] if person_name_lower else ''
            if leader_lower == first_name or leader_lower in person_name_lower:
                if person.person_id not in matched_ids:
                    matched_ids.append(person.person_id)
                break

    return matched_ids


def parse_pdf_table_data(tables):
    """
    Parse table data extracted from PDF by pdfplumber
    Returns a dictionary with extracted data
    """
    extracted = {
        'service_date': None,
        'service_name': None,
        'songs': [],
        'raw_text': ''
    }

    for table in tables:
        for row in table:
            if not row or all(cell is None or cell == '' for cell in row):
                continue

            # Clean up cells - join multi-line text and strip whitespace
            cleaned_row = []
            for cell in row:
                if cell:
                    # Replace newlines with spaces and clean up
                    cleaned = ' '.join(str(cell).split())
                    cleaned_row.append(cleaned)
                else:
                    cleaned_row.append('')

            # Try to parse as a song row
            # Expected format: Date | Service Name | Order | Title | Artist | Key | Length | Lead
            if len(cleaned_row) >= 6:
                # Look for date in first column
                date_cell = cleaned_row[0] if cleaned_row[0] else ''

                # Try to parse date
                if not extracted['service_date'] and date_cell:
                    date_patterns = [
                        (r'(\w+)\s+(\d{1,2}),?\s+(\d{4})', '%B %d %Y'),  # February 1, 2026
                        (r'(\d{1,2})/(\d{1,2})/(\d{4})', '%m/%d/%Y'),  # 2/1/2026
                        (r'(\d{4})-(\d{2})-(\d{2})', '%Y-%m-%d'),  # 2026-02-01
                    ]
                    for pattern, fmt in date_patterns:
                        match = re.search(pattern, date_cell)
                        if match:
                            try:
                                date_str = ' '.join(match.groups())
                                parsed_date = datetime.strptime(date_str, fmt)
                                extracted['service_date'] = parsed_date.strftime('%Y-%m-%d')
                                break
                            except ValueError:
                                continue

                # Service name from second column
                if not extracted['service_name'] and len(cleaned_row) > 1 and cleaned_row[1]:
                    service_name = cleaned_row[1].replace(' -', '').strip()
                    if service_name and service_name != '-':
                        extracted['service_name'] = service_name

                # Try to extract song data
                # Look for order number (column index 2 typically)
                order_idx = None
                for i, cell in enumerate(cleaned_row):
                    if cell and cell.isdigit() and int(cell) <= 20:
                        order_idx = i
                        break

                if order_idx is not None:
                    song_entry = {
                        'order': int(cleaned_row[order_idx]),
                        'title': '',
                        'artist': '',
                        'key': '',
                        'length': '',
                        'lead': ''
                    }

                    # Title is typically after order
                    if order_idx + 1 < len(cleaned_row):
                        song_entry['title'] = cleaned_row[order_idx + 1]

                    # Artist is typically after title
                    if order_idx + 2 < len(cleaned_row):
                        song_entry['artist'] = cleaned_row[order_idx + 2]

                    # Look for key (single letter or letter + b/# like Bb, A, D, F#)
                    # and time format (M:SS or MM:SS)
                    for i in range(order_idx + 3, len(cleaned_row)):
                        cell = cleaned_row[i]
                        if not cell:
                            continue

                        # Check for key pattern (A, Bb, C#, Dm, etc.)
                        if re.match(r'^[A-G][b#]?m?$', cell) and not song_entry['key']:
                            song_entry['key'] = cell
                        # Check for time pattern (M:SS or MM:SS)
                        elif re.match(r'^\d{1,2}:\d{2}$', cell) and not song_entry['length']:
                            song_entry['length'] = cell
                        # Check for lead info (contains "leads" or is a name)
                        elif 'leads' in cell.lower():
                            song_entry['lead'] = cell.replace('leads', '').strip()
                        elif not song_entry['lead'] and i == len(cleaned_row) - 1:
                            # Last column might be lead
                            song_entry['lead'] = cell

                    # Only add if we have a title
                    if song_entry['title']:
                        extracted['songs'].append(song_entry)

    # Set default service name if not found
    if not extracted['service_name']:
        if extracted['service_date']:
            extracted['service_name'] = f"Service - {extracted['service_date']}"
        else:
            extracted['service_name'] = "Imported Service"

    return extracted


def parse_pdf_for_service_data(pdf_text):
    """
    Parse PDF text to extract service and song information (fallback for non-table PDFs)
    Returns a dictionary with extracted data
    """
    extracted = {
        'service_date': None,
        'service_name': None,
        'songs': [],
        'raw_text': pdf_text
    }

    # First, try to parse as Planning Center tabular format
    # The text often comes as repeated blocks like:
    # "February 1, 2026 WordServe - 1 Blessed Be Your Name Beth Redman... Bb 4:48 Bill leads"

    # Join all text and normalize whitespace
    full_text = ' '.join(pdf_text.split())

    # Pattern to match song entries in Planning Center format
    # Date | Service | Order | Title | Artist | Key | Time | Lead
    song_pattern = re.compile(
        r'(\w+\s+\d{1,2},?\s+\d{4})\s+'  # Date (February 1, 2026)
        r'([A-Za-z]+(?:\s*-)?)\s+'  # Service name (WordServe -)
        r'(\d+)\s+'  # Order number
        r'(.+?)\s+'  # Title (non-greedy)
        r'([A-Za-z][A-Za-z\s,\.]+?)\s+'  # Artist
        r'([A-G][b#]?m?)\s+'  # Key
        r'(\d{1,2}:\d{2})\s+'  # Time
        r'(\w+)\s*leads'  # Lead person
    )

    matches = song_pattern.findall(full_text)

    if matches:
        for match in matches:
            date_str, service_name, order, title, artist, key, length, lead = match

            # Set service date and name from first match
            if not extracted['service_date']:
                try:
                    for fmt in ['%B %d %Y', '%B %d, %Y', '%b %d %Y', '%b %d, %Y']:
                        try:
                            parsed_date = datetime.strptime(date_str.replace(',', ''), fmt)
                            extracted['service_date'] = parsed_date.strftime('%Y-%m-%d')
                            break
                        except ValueError:
                            continue
                except:
                    pass

            if not extracted['service_name']:
                extracted['service_name'] = service_name.replace('-', '').strip()

            song_entry = {
                'order': int(order),
                'title': title.strip(),
                'artist': artist.strip().rstrip(',').rstrip(' and'),
                'key': key,
                'length': length,
                'lead': lead.strip()
            }
            extracted['songs'].append(song_entry)
    else:
        # Fallback: Try simpler line-by-line parsing
        lines = pdf_text.split('\n')
        lines = [line.strip() for line in lines if line.strip()]

        # Date patterns to look for
        date_patterns = [
            r'(\d{1,2}/\d{1,2}/\d{4})',  # MM/DD/YYYY
            r'(\d{4}-\d{2}-\d{2})',  # YYYY-MM-DD
            r'((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4})',
            r'((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4})',
        ]

        for line in lines:
            # Try to find service date
            if not extracted['service_date']:
                for pattern in date_patterns:
                    match = re.search(pattern, line, re.IGNORECASE)
                    if match:
                        date_str = match.group(1)
                        try:
                            for fmt in ['%m/%d/%Y', '%Y-%m-%d', '%B %d, %Y', '%B %d %Y', '%b %d, %Y', '%b %d %Y']:
                                try:
                                    parsed_date = datetime.strptime(date_str.replace(',', ''), fmt)
                                    extracted['service_date'] = parsed_date.strftime('%Y-%m-%d')
                                    break
                                except ValueError:
                                    continue
                        except:
                            pass
                        break

            # Try to find service name
            if not extracted['service_name']:
                service_indicators = ['sunday', 'service', 'worship', 'evening', 'morning', 'wednesday', 'saturday', 'wordserve']
                if any(indicator in line.lower() for indicator in service_indicators):
                    extracted['service_name'] = line[:100]

    # Set default service name if not found
    if not extracted['service_name']:
        if extracted['service_date']:
            extracted['service_name'] = f"Service - {extracted['service_date']}"
        else:
            extracted['service_name'] = "Imported Service"

    return extracted


def handle_pdf_import(request):
    """Handle PDF file upload and parsing (supports multiple files)"""
    church = get_active_church(request)
    if not church:
        messages.error(request, 'Please select a church first.')
        return redirect('band:home')

    pdf_files = request.FILES.getlist('pdf_file')
    people = Person.objects.filter(church=church).filter(
        Q(lead_vocal=True) | Q(harmony_vocal=True)
    ).order_by('name')

    all_extracted = []

    for pdf_file in pdf_files:
        if not pdf_file.name.lower().endswith('.pdf'):
            messages.error(request, f'{pdf_file.name}: Not a PDF file.')
            continue

        try:
            # Extract data from PDF - try tables first, then text
            pdf_text = ""
            all_tables = []

            with pdfplumber.open(pdf_file) as pdf:
                for page in pdf.pages:
                    tables = page.extract_tables()
                    if tables:
                        all_tables.extend(tables)
                    page_text = page.extract_text()
                    if page_text:
                        pdf_text += page_text + "\n"

            extracted_data = None

            # Try table extraction first if we found tables
            if all_tables:
                extracted_data = parse_pdf_table_data(all_tables)
                extracted_data['raw_text'] = pdf_text
                # Only use table data if songs were found
                if not extracted_data['songs']:
                    extracted_data = None

            # Fall back to text parsing
            if extracted_data is None:
                if not pdf_text.strip():
                    messages.error(request, f'{pdf_file.name}: Could not extract text. The PDF may be image-based or empty.')
                    continue
                extracted_data = parse_pdf_for_service_data(pdf_text)

            # Match lead names to people in database
            for song in extracted_data['songs']:
                if song.get('lead'):
                    leaders = parse_multiple_leaders(song['lead'])
                    matched_ids = match_leaders_to_people(leaders, people)
                    song['matched_person_ids'] = matched_ids
                    song['matched_person_id'] = matched_ids[0] if matched_ids else None
                    song['parsed_leaders'] = leaders

            extracted_data['filename'] = pdf_file.name
            all_extracted.append(extracted_data)

        except Exception as e:
            messages.error(request, f'{pdf_file.name}: Error processing PDF: {str(e)}')

    if not all_extracted:
        return redirect('band:import_services')

    # Store in session for confirmation step
    request.session['pdf_extracted_data_list'] = all_extracted

    return render(request, 'band/import_pdf_review.html', {
        'extracted_list': all_extracted,
        'people': people,
    })


@login_required
@admin_required
def confirm_pdf_import(request):
    """Handle PDF import confirmation (supports multiple PDFs)"""
    if request.method == 'POST' and 'confirm_import' in request.POST:
        try:
            church = get_active_church(request)
            if not church:
                messages.error(request, 'Please select a church first.')
                return redirect('band:home')

            pdf_count = int(request.POST.get('pdf_count', 1))
            total_services = 0
            total_songs_added = 0
            total_songs_created = 0

            for p in range(pdf_count):
                fp = f'pdf_{p}_'  # field prefix for this PDF

                service_date = request.POST.get(f'{fp}service_date', '').strip()
                service_name = request.POST.get(f'{fp}service_name', '').strip()
                if not service_date or not service_name:
                    continue

                try:
                    parsed_date = datetime.strptime(service_date, '%Y-%m-%d').date()
                except ValueError:
                    messages.error(request, f'PDF {p + 1}: Invalid date format.')
                    continue

                # Generate Plan ID (fresh query each iteration to avoid duplicates)
                last_service = Service.objects.filter(church=church).order_by('-plan_id').first()
                if last_service and last_service.plan_id.startswith('SV'):
                    try:
                        next_num = int(last_service.plan_id[2:]) + 1
                    except:
                        next_num = 1
                else:
                    next_num = 1
                plan_id = f"SV{next_num:03d}"

                service = Service.objects.create(
                    plan_id=plan_id,
                    service_date=parsed_date,
                    service_name=service_name,
                    band_notes=request.POST.get(f'{fp}band_notes', ''),
                    service_notes=request.POST.get(f'{fp}service_notes', ''),
                    church=church,
                )
                total_services += 1

                song_count = int(request.POST.get(f'{fp}song_count', 0))
                songs_added = 0
                songs_created = 0

                for i in range(song_count):
                    song_title = request.POST.get(f'{fp}song_title_{i}', '').strip()
                    if not song_title:
                        continue

                    song_artist = request.POST.get(f'{fp}song_artist_{i}', '').strip()
                    song_key = request.POST.get(f'{fp}song_key_{i}', '').strip()
                    song_length = request.POST.get(f'{fp}song_length_{i}', '').strip()
                    song_order = int(request.POST.get(f'{fp}song_order_{i}', i + 1))

                    # Collect all lead persons for this song (supports multiple leaders)
                    lead_person_ids = []
                    for j in range(20):
                        lead_id = request.POST.get(f'{fp}lead_person_{i}_{j}', '').strip()
                        if lead_id and lead_id not in lead_person_ids:
                            lead_person_ids.append(lead_id)

                    # Generate Song ID (scoped to church)
                    last_song = Song.objects.filter(church=church).order_by('-song_id').first()
                    if last_song and last_song.song_id.startswith('S'):
                        try:
                            next_song_num = int(last_song.song_id[1:]) + 1
                        except:
                            next_song_num = 1
                    else:
                        next_song_num = 1
                    new_song_id = f"S{next_song_num:03d}"

                    # Check if song exists by title and artist (scoped to church)
                    existing_song = Song.objects.filter(
                        church=church,
                        title__iexact=song_title,
                        artist__iexact=song_artist
                    ).first()

                    if existing_song:
                        song = existing_song
                        if song_length and not song.length:
                            song.length = song_length
                            song.save()
                    else:
                        song_info = fetch_song_info_from_internet(song_title, song_artist)
                        final_key = song_info['key'] if song_info['key'] else (song_key if song_key else 'C')
                        final_length = song_length or song_info.get('length') or ''

                        song = Song.objects.create(
                            song_id=new_song_id,
                            title=song_title,
                            artist=song_artist,
                            default_key=final_key,
                            tempo=song_info['tempo'] or '',
                            bpm=song_info['bpm'],
                            length=final_length,
                            church=church,
                        )
                        songs_created += 1

                        if song_info['key']:
                            messages.info(request, f"Created '{song_title}' with key {final_key} (found online)")

                    # Get primary lead person (first one) for ServiceSong
                    primary_lead_person = None
                    if lead_person_ids:
                        try:
                            primary_lead_person = Person.objects.get(person_id=lead_person_ids[0], church=church)
                        except Person.DoesNotExist:
                            pass

                    ServiceSong.objects.create(
                        service=service,
                        song=song,
                        song_order=song_order,
                        key_used=song_key if song_key else song.default_key,
                        lead_person=primary_lead_person
                    )
                    songs_added += 1

                    if song.last_used is None or parsed_date > song.last_used:
                        song.last_used = parsed_date
                    song.times_used += 1
                    song.save()

                    # Create/update song preferences for ALL leaders
                    for lead_person_id in lead_person_ids:
                        try:
                            lead_person = Person.objects.get(person_id=lead_person_id, church=church)
                            pref, created = PersonSongPreference.objects.get_or_create(
                                person=lead_person,
                                song=song,
                                defaults={
                                    'entry_id': f"E{PersonSongPreference.objects.count() + 1:03d}",
                                    'preferred_key': song_key if song_key else song.default_key,
                                    'can_lead': True,
                                    'confidence': 'high',
                                }
                            )
                            if not created and not pref.can_lead:
                                pref.can_lead = True
                                pref.save()
                        except Person.DoesNotExist:
                            pass

                total_songs_added += songs_added
                total_songs_created += songs_created

            # Clear session data
            for key in ['pdf_extracted_data', 'pdf_extracted_data_list']:
                if key in request.session:
                    del request.session[key]

            messages.success(request, f'Successfully imported {total_services} service(s) with {total_songs_added} song(s). {total_songs_created} new song(s) added to library.')
            return redirect('band:home')

        except Exception as e:
            messages.error(request, f'Error importing data: {str(e)}')
            return redirect('band:import_services')

    return redirect('band:import_services')


@login_required
@admin_required
def person_delete(request, person_id):
    """Show delete confirmation for a band member"""
    church = get_active_church(request)
    if not church:
        return redirect('band:home')
    person = get_object_or_404(Person, person_id=person_id, church=church)

    # Get related data to show impact of deletion
    song_preferences = PersonSongPreference.objects.filter(person=person).count()
    service_songs = ServiceSong.objects.filter(lead_person=person).count()

    context = {
        'person': person,
        'song_preferences_count': song_preferences,
        'service_songs_count': service_songs,
    }
    return render(request, 'band/person_delete.html', context)


@login_required
@admin_required
def person_delete_confirm(request, person_id):
    """Confirm and delete a band member"""
    church = get_active_church(request)
    if not church:
        return redirect('band:home')
    person = get_object_or_404(Person, person_id=person_id, church=church)

    if request.method != 'POST':
        return redirect('band:person_delete', person_id=person_id)

    try:
        person_name = person.name
        person.delete()
        messages.success(request, f'Successfully deleted {person_name}.')
    except Exception as e:
        messages.error(request, f'Error deleting band member: {str(e)}')
        return redirect('band:person_detail', person_id=person_id)

    return redirect('band:people_list')


@login_required
@admin_required
def song_edit_review(request, song_id):
    """Show review page for song edits before saving"""
    church = get_active_church(request)
    if not church:
        return redirect('band:home')
    song = get_object_or_404(Song, song_id=song_id, church=church)

    if request.method != 'POST':
        return redirect('band:song_detail', song_id=song_id)

    # Get form data
    new_data = {
        'title': request.POST.get('title', '').strip(),
        'artist': request.POST.get('artist', '').strip(),
        'default_key': request.POST.get('default_key', '').strip(),
        'tempo': request.POST.get('tempo', ''),
        'bpm': request.POST.get('bpm', '').strip(),
        'length': request.POST.get('length', '').strip(),
        'style': request.POST.get('style', '').strip(),
        'arrangement_notes': request.POST.get('arrangement_notes', '').strip(),
        'comfort_level': request.POST.get('comfort_level', '').strip(),
        'notes': request.POST.get('notes', '').strip(),
    }

    # Get display value for tempo
    tempo_display = dict(Song.TEMPO_CHOICES).get(new_data['tempo'], new_data['tempo'])
    new_data['tempo_display'] = tempo_display

    # Compare with current values and track changes
    changes = []
    changed_fields = []

    field_mappings = [
        ('title', 'Title', song.title),
        ('artist', 'Artist', song.artist or ''),
        ('default_key', 'Default Key', song.default_key),
        ('tempo', 'Tempo', song.tempo or ''),
        ('bpm', 'BPM', str(song.bpm) if song.bpm else ''),
        ('length', 'Length', song.length or ''),
        ('style', 'Style', song.style or ''),
        ('arrangement_notes', 'Arrangement Notes', song.arrangement_notes or ''),
        ('comfort_level', 'Comfort Level', song.comfort_level or ''),
        ('notes', 'Notes', song.notes or ''),
    ]

    for field_name, display_name, old_value in field_mappings:
        new_value = new_data[field_name]

        if field_name == 'tempo':
            old_display = dict(Song.TEMPO_CHOICES).get(old_value, old_value) if old_value else ''
            new_display = tempo_display if new_value else ''
        else:
            old_display = old_value
            new_display = new_value

        if str(new_value) != str(old_value):
            changes.append({
                'field': display_name,
                'old_value': old_display,
                'new_value': new_display,
            })
            changed_fields.append(display_name)

    context = {
        'song': song,
        'new_data': new_data,
        'changes': changes,
        'changed_fields': changed_fields,
        'tempo_choices': Song.TEMPO_CHOICES,
    }
    return render(request, 'band/song_edit_review.html', context)


@login_required
@admin_required
def song_edit_confirm(request, song_id):
    """Confirm and save song edits"""
    church = get_active_church(request)
    if not church:
        return redirect('band:home')
    song = get_object_or_404(Song, song_id=song_id, church=church)

    if request.method != 'POST':
        return redirect('band:song_detail', song_id=song_id)

    try:
        # Update song fields
        song.title = request.POST.get('title', '').strip()
        song.artist = request.POST.get('artist', '').strip() or None
        song.default_key = request.POST.get('default_key', '').strip()
        song.tempo = request.POST.get('tempo', '') or None
        bpm_str = request.POST.get('bpm', '').strip()
        song.bpm = int(bpm_str) if bpm_str else None
        song.length = request.POST.get('length', '').strip()
        song.style = request.POST.get('style', '').strip() or None
        song.arrangement_notes = request.POST.get('arrangement_notes', '').strip()
        song.comfort_level = request.POST.get('comfort_level', '').strip()
        song.notes = request.POST.get('notes', '').strip()

        song.save()
        messages.success(request, f'Successfully updated {song.title}.')

    except Exception as e:
        messages.error(request, f'Error saving changes: {str(e)}')

    return redirect('band:song_detail', song_id=song_id)


@login_required
@admin_required
def song_delete(request, song_id):
    """Show delete confirmation for a song"""
    church = get_active_church(request)
    if not church:
        return redirect('band:home')
    song = get_object_or_404(Song, song_id=song_id, church=church)

    # Get related data to show impact of deletion
    person_preferences = PersonSongPreference.objects.filter(song=song).count()
    service_songs = ServiceSong.objects.filter(song=song).count()

    context = {
        'song': song,
        'person_preferences_count': person_preferences,
        'service_songs_count': service_songs,
    }
    return render(request, 'band/song_delete.html', context)


@login_required
@admin_required
def song_delete_confirm(request, song_id):
    """Confirm and delete a song"""
    church = get_active_church(request)
    if not church:
        return redirect('band:home')
    song = get_object_or_404(Song, song_id=song_id, church=church)

    if request.method != 'POST':
        return redirect('band:song_delete', song_id=song_id)

    try:
        song_title = song.title
        song.delete()
        messages.success(request, f'Successfully deleted "{song_title}".')
    except Exception as e:
        messages.error(request, f'Error deleting song: {str(e)}')
        return redirect('band:song_detail', song_id=song_id)

    return redirect('band:songs_list')


@login_required
@admin_required
def service_edit(request, plan_id):
    """Save edited service information"""
    church = get_active_church(request)
    if not church:
        return redirect('band:home')
    service = get_object_or_404(Service, plan_id=plan_id, church=church)

    if request.method != 'POST':
        return redirect('band:service_detail', plan_id=plan_id)

    service_name = request.POST.get('service_name', '').strip()
    service_date = request.POST.get('service_date', '').strip()

    if not service_name or not service_date:
        messages.error(request, 'Service Name and Date are required.')
        return redirect('band:service_detail', plan_id=plan_id)

    try:
        service.service_name = service_name
        service.service_date = datetime.strptime(service_date, '%Y-%m-%d').date()
        service.band_notes = request.POST.get('band_notes', '').strip()
        service.service_notes = request.POST.get('service_notes', '').strip()
        service.save()
        messages.success(request, 'Service updated successfully.')
    except ValueError:
        messages.error(request, 'Invalid date format.')
    except Exception as e:
        messages.error(request, f'Error updating service: {str(e)}')

    return redirect('band:service_detail', plan_id=plan_id)


@login_required
@admin_required
def preference_edit(request, entry_id):
    """Save edited PersonSongPreference (confidence, can_lead, preferred_key, notes)"""
    church = get_active_church(request)
    if not church:
        return redirect('band:home')
    pref = get_object_or_404(PersonSongPreference, entry_id=entry_id, person__church=church)

    if request.method != 'POST':
        return redirect('band:home')

    pref.confidence    = request.POST.get('confidence', '').strip()
    pref.can_lead      = 'can_lead' in request.POST
    pref.preferred_key = request.POST.get('preferred_key', '').strip()
    pref.notes         = request.POST.get('notes', '').strip()
    pref.save()

    next_url = request.POST.get('next', '')
    messages.success(request, f'Preference updated for {pref.person.name} — {pref.song.title}.')
    return redirect(next_url) if next_url else redirect('band:home')


@login_required
@admin_required
def service_delete(request, plan_id):
    """Show delete confirmation for a service"""
    church = get_active_church(request)
    if not church:
        return redirect('band:home')
    service = get_object_or_404(Service, plan_id=plan_id, church=church)
    song_count = ServiceSong.objects.filter(service=service).count()

    return render(request, 'band/service_delete.html', {
        'service': service,
        'song_count': song_count,
    })


@login_required
@admin_required
def service_delete_confirm(request, plan_id):
    """Confirm and delete a service"""
    church = get_active_church(request)
    if not church:
        return redirect('band:home')
    service = get_object_or_404(Service, plan_id=plan_id, church=church)

    if request.method != 'POST':
        return redirect('band:service_delete', plan_id=plan_id)

    try:
        service_name = service.service_name
        service.delete()
        messages.success(request, f'Successfully deleted "{service_name}".')
    except Exception as e:
        messages.error(request, f'Error deleting service: {str(e)}')
        return redirect('band:service_detail', plan_id=plan_id)

    return redirect('band:services_list')


@login_required
def services_list(request):
    """List all services with column-based filtering"""
    church = get_active_church(request)
    if not church:
        return redirect('band:home')

    # All unique service names for the name filter dropdown
    all_service_names = list(
        Service.objects.filter(church=church)
        .values_list('service_name', flat=True)
        .distinct()
        .order_by('service_name')
    )

    services = Service.objects.filter(church=church).prefetch_related('songs__song', 'songs__lead_person')

    # Sort (default: newest first)
    sort = request.GET.get('sort', 'date_desc')
    if sort == 'date_asc':
        services = services.order_by('service_date', 'service_name')
    else:
        services = services.order_by('-service_date', 'service_name')

    # Date filter: specific date takes priority over range
    date_exact = request.GET.get('date', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()

    if date_exact:
        try:
            services = services.filter(service_date=datetime.strptime(date_exact, '%Y-%m-%d').date())
        except ValueError:
            date_exact = ''
    else:
        if date_from:
            try:
                services = services.filter(service_date__gte=datetime.strptime(date_from, '%Y-%m-%d').date())
            except ValueError:
                date_from = ''
        if date_to:
            try:
                services = services.filter(service_date__lte=datetime.strptime(date_to, '%Y-%m-%d').date())
            except ValueError:
                date_to = ''

    # Name filter: multi-select checkboxes
    selected_names = request.GET.getlist('names')
    if selected_names:
        services = services.filter(service_name__in=selected_names)

    # Annotate and compute lengths
    services = services.annotate(song_count=Count('songs'))
    services = list(services)
    for service in services:
        total_sec = sum(
            parse_song_length_to_seconds(ss.song.length, ss.length)
            for ss in service.songs.all()
        )
        service.total_length = format_service_length(total_sec)

    context = {
        'services': services,
        'all_service_names': all_service_names,
        'sort': sort,
        'selected_names': selected_names,
        'date_exact': date_exact,
        'date_from': date_from,
        'date_to': date_to,
        'date_active': bool(date_exact or date_from or date_to),
    }
    return render(request, 'band/services_list.html', context)


@login_required
def service_detail(request, plan_id):
    """Detail view for a specific service showing songs in order"""
    church = get_active_church(request)
    if not church:
        return redirect('band:home')
    service = get_object_or_404(Service, plan_id=plan_id, church=church)
    service_songs = ServiceSong.objects.filter(service=service).select_related('song', 'lead_person').order_by('song_order')

    total_sec = sum(
        parse_song_length_to_seconds(ss.song.length, ss.length)
        for ss in service_songs
    )

    context = {
        'service': service,
        'service_songs': service_songs,
        'total_length': format_service_length(total_sec),
    }
    return render(request, 'band/service_detail.html', context)


@login_required
@admin_required
def person_add(request):
    """Add a new band member"""
    church = get_active_church(request)
    if not church:
        return redirect('band:home')

    if request.method == 'POST':
        try:
            # Generate Person ID (scoped to church)
            last_person = Person.objects.filter(church=church).order_by('-person_id').first()
            if last_person and last_person.person_id.startswith('P'):
                try:
                    next_num = int(last_person.person_id[1:]) + 1
                except:
                    next_num = 1
            else:
                next_num = 1
            person_id = f"P{next_num:03d}"

            # Create the person
            person = Person.objects.create(
                person_id=person_id,
                name=request.POST.get('name', '').strip(),
                role=request.POST.get('role', ''),
                frequency=request.POST.get('frequency', '') or None,
                primary_instrument=request.POST.get('primary_instrument', '').strip() or None,
                secondary_instrument=request.POST.get('secondary_instrument', '').strip() or None,
                lead_vocal='lead_vocal' in request.POST,
                harmony_vocal='harmony_vocal' in request.POST,
                preferred_keys=request.POST.get('preferred_keys', '').strip(),
                style_strengths=request.POST.get('style_strengths', '').strip(),
                availability=request.POST.get('availability', '').strip(),
                notes=request.POST.get('notes', '').strip(),
                church=church,
            )
            messages.success(request, f'Successfully added {person.name}.')
            return redirect('band:person_detail', person_id=person.person_id)

        except Exception as e:
            messages.error(request, f'Error adding band member: {str(e)}')

    context = {
        'role_choices': Person.ROLE_CHOICES,
        'frequency_choices': Person.FREQUENCY_CHOICES,
    }
    return render(request, 'band/person_add.html', context)


@login_required
@admin_required
def song_add(request):
    """Add a new song"""
    church = get_active_church(request)
    if not church:
        return redirect('band:home')

    if request.method == 'POST':
        try:
            # Generate Song ID (scoped to church)
            last_song = Song.objects.filter(church=church).order_by('-song_id').first()
            if last_song and last_song.song_id.startswith('S'):
                try:
                    next_num = int(last_song.song_id[1:]) + 1
                except:
                    next_num = 1
            else:
                next_num = 1
            song_id = f"S{next_num:03d}"

            title = request.POST.get('title', '').strip()
            artist = request.POST.get('artist', '').strip()
            default_key = request.POST.get('default_key', '').strip()
            tempo = request.POST.get('tempo', '')
            bpm_str = request.POST.get('bpm', '').strip()
            length = request.POST.get('length', '').strip()

            # Fetch song info from PraiseCharts if key, tempo, BPM, or length is missing
            if not default_key or not tempo or not bpm_str or not length:
                song_info = fetch_song_info_from_internet(title, artist)
                if not default_key and song_info['key']:
                    default_key = song_info['key']
                    messages.info(request, f'Original key "{default_key}" found online.')
                if not tempo and song_info['tempo']:
                    tempo = song_info['tempo']
                if not bpm_str and song_info['bpm']:
                    bpm_str = str(song_info['bpm'])
                if not length and song_info.get('length'):
                    length = song_info['length']

            # Create the song
            song = Song.objects.create(
                song_id=song_id,
                title=title,
                artist=artist or None,
                default_key=default_key or 'C',
                tempo=tempo or '',
                bpm=int(bpm_str) if bpm_str else None,
                length=length,
                style=request.POST.get('style', '').strip() or None,
                arrangement_notes=request.POST.get('arrangement_notes', '').strip(),
                comfort_level=request.POST.get('comfort_level', '').strip(),
                notes=request.POST.get('notes', '').strip(),
                church=church,
            )
            messages.success(request, f'Successfully added "{song.title}".')
            return redirect('band:song_detail', song_id=song.song_id)

        except Exception as e:
            messages.error(request, f'Error adding song: {str(e)}')

    context = {
        'tempo_choices': Song.TEMPO_CHOICES,
    }
    return render(request, 'band/song_add.html', context)


@login_required
@admin_required
def service_add(request):
    """Add a new service"""
    church = get_active_church(request)
    if not church:
        return redirect('band:home')

    if request.method == 'POST':
        try:
            # Generate Plan ID (scoped to church)
            last_service = Service.objects.filter(church=church).order_by('-plan_id').first()
            if last_service and last_service.plan_id.startswith('SV'):
                try:
                    next_num = int(last_service.plan_id[2:]) + 1
                except:
                    next_num = 1
            else:
                next_num = 1
            plan_id = f"SV{next_num:03d}"

            # Parse service date
            service_date = datetime.strptime(request.POST.get('service_date', ''), '%Y-%m-%d').date()

            # Create the service
            service = Service.objects.create(
                plan_id=plan_id,
                service_date=service_date,
                service_name=request.POST.get('service_name', '').strip(),
                band_notes=request.POST.get('band_notes', '').strip(),
                service_notes=request.POST.get('service_notes', '').strip(),
                church=church,
            )
            messages.success(request, f'Successfully added service "{service.service_name}".')
            return redirect('band:service_detail', plan_id=service.plan_id)

        except ValueError:
            messages.error(request, 'Invalid date format. Please use YYYY-MM-DD.')
        except Exception as e:
            messages.error(request, f'Error adding service: {str(e)}')

    return render(request, 'band/service_add.html')


@login_required
@admin_required
def refresh_song_keys(request):
    """Re-fetch original keys, tempo, and BPM for all songs from PraiseCharts"""
    if request.method == 'POST':
        church = get_active_church(request)
        if not church:
            return redirect('band:home')
        songs = Song.objects.filter(church=church)
        updated = 0
        failed = []

        for song in songs:
            try:
                song_info = fetch_song_info_from_internet(song.title, song.artist)
                if song_info['key'] or song_info['tempo'] or song_info['bpm'] or song_info.get('length'):
                    changed = False
                    changes = []

                    if song_info['key'] and song_info['key'] != song.default_key:
                        changes.append(f'key: {song.default_key} → {song_info["key"]}')
                        song.default_key = song_info['key']
                        changed = True

                    if song_info['tempo'] and song_info['tempo'] != song.tempo:
                        song.tempo = song_info['tempo']
                        changed = True

                    if song_info['bpm'] and song_info['bpm'] != song.bpm:
                        song.bpm = song_info['bpm']
                        changed = True

                    if song_info.get('length') and not song.length:
                        song.length = song_info['length']
                        changed = True

                    if changed:
                        song.save()
                        updated += 1
                        if changes:
                            messages.info(request, f'"{song.title}": {", ".join(changes)}')
                else:
                    failed.append(song.title)
            except Exception:
                failed.append(song.title)

        messages.success(request, f'Updated {updated} song(s) from PraiseCharts.')
        if failed:
            messages.warning(request, f'Could not find info for: {", ".join(failed)}')

        return redirect('band:songs_list')

    return redirect('band:songs_list')


# ── Phase 5: SuperAdmin Features ──────────────────────────────────────

@login_required
@superadmin_required
def switch_church(request):
    """Switch the active church (SuperAdmin only)"""
    if request.method == 'POST':
        church_id = request.POST.get('church_id')
        if church_id:
            try:
                church = Church.objects.get(id=church_id, is_active=True)
                request.session['active_church_id'] = church.id
                messages.success(request, f'Switched to {church.name}.')
            except Church.DoesNotExist:
                messages.error(request, 'Church not found.')
        else:
            request.session.pop('active_church_id', None)
            messages.info(request, 'No church selected.')
    return redirect('band:home')


@login_required
@superadmin_required
def church_list(request):
    """List all churches"""
    churches = Church.objects.all()
    for church in churches:
        church.user_count = UserProfile.objects.filter(church=church).count()
    context = {'churches': churches}
    return render(request, 'band/church_list.html', context)


@login_required
@superadmin_required
def church_add(request):
    """Add a new church"""
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Church name is required.')
            return render(request, 'band/church_add.html')

        slug = slugify(name)
        # Ensure unique slug
        base_slug = slug
        counter = 1
        while Church.objects.filter(slug=slug).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1

        church = Church.objects.create(name=name, slug=slug, is_active=True)
        messages.success(request, f'Church "{church.name}" created successfully.')
        return redirect('band:church_list')

    return render(request, 'band/church_add.html')


@login_required
@superadmin_required
def church_edit(request, church_id):
    """Edit a church"""
    church = get_object_or_404(Church, id=church_id)

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        is_active = 'is_active' in request.POST

        if not name:
            messages.error(request, 'Church name is required.')
        else:
            church.name = name
            church.is_active = is_active
            church.save()
            messages.success(request, f'Church "{church.name}" updated.')
            return redirect('band:church_list')

    context = {'church': church}
    return render(request, 'band/church_edit.html', context)


@login_required
@admin_required
def user_list(request):
    """List all users. Admins see their church's users; SuperAdmins see all."""
    from django.contrib.auth.models import User
    profiles = UserProfile.objects.select_related('user', 'church').order_by('church__name', 'user__username')

    try:
        caller_profile = request.user.profile
    except Exception:
        return redirect('band:home')

    if not caller_profile.is_superadmin:
        # Admins only see users in their own church
        profiles = profiles.filter(church=caller_profile.church)

    context = {'profiles': profiles, 'is_superadmin': caller_profile.is_superadmin}
    return render(request, 'band/user_list.html', context)


@login_required
@admin_required
def user_add(request):
    """Add a new user. Email is used as the username. A temp password is generated and emailed."""
    from django.contrib.auth.models import User

    try:
        caller_profile = request.user.profile
    except Exception:
        return redirect('band:home')

    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        church_id = request.POST.get('church', '')
        app_role = request.POST.get('app_role', 'user')

        if not email:
            messages.error(request, 'Email address is required.')
        elif User.objects.filter(username=email).exists():
            messages.error(request, f'A user with email "{email}" already exists.')
        else:
            # Auto-generate a temporary password
            temp_password = get_random_string(length=10)

            user = User.objects.create_user(
                username=email,
                email=email,
                password=temp_password,
                first_name=first_name,
                last_name=last_name,
            )

            # Set up profile
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.must_change_password = True

            if caller_profile.is_superadmin:
                # SuperAdmin can assign any church and role
                profile.app_role = app_role
                if church_id:
                    try:
                        profile.church = Church.objects.get(id=church_id)
                    except Church.DoesNotExist:
                        pass
            else:
                # Admin can only add users to their own church as 'user' role
                profile.church = caller_profile.church
                profile.app_role = 'user'

            profile.save()

            # Send welcome email with temp password
            church_name = profile.church.name if profile.church else 'WorshipFlow'
            email_sent = False
            try:
                send_mail(
                    subject=f'Welcome to WorshipFlow - {church_name}',
                    message=(
                        f'Hi {first_name or email},\n\n'
                        f'An account has been created for you on WorshipFlow.\n\n'
                        f'Email (login): {email}\n'
                        f'Temporary password: {temp_password}\n\n'
                        f'You will be asked to set a new password when you first log in.\n\n'
                        f'- {church_name}'
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[email],
                    fail_silently=False,
                )
                email_sent = True
            except Exception:
                pass

            messages.success(request, f'User "{email}" created successfully.')
            if email_sent:
                messages.info(request, f'Login details emailed to {email}.')
            messages.warning(request, f'Temporary password: {temp_password} — share this with the user.')

            return redirect('band:user_list')

    # Build context based on role
    if caller_profile.is_superadmin:
        churches = Church.objects.filter(is_active=True)
        role_choices = UserProfile.ROLE_CHOICES
    else:
        churches = None
        role_choices = None

    context = {
        'churches': churches,
        'role_choices': role_choices,
        'is_superadmin': caller_profile.is_superadmin,
    }
    return render(request, 'band/user_add.html', context)


@login_required
@admin_required
def user_edit(request, user_id):
    """Edit a user"""
    from django.contrib.auth.models import User

    try:
        caller_profile = request.user.profile
    except Exception:
        return redirect('band:home')

    target_user = get_object_or_404(User, id=user_id)
    profile, _ = UserProfile.objects.get_or_create(user=target_user)

    # Admins can only edit users in their own church
    if not caller_profile.is_superadmin and profile.church != caller_profile.church:
        messages.error(request, "You don't have permission to edit this user.")
        return redirect('band:user_list')

    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        new_password = request.POST.get('new_password', '').strip()

        if email and email != target_user.email:
            # Update username to match new email
            if User.objects.filter(username=email).exclude(id=target_user.id).exists():
                messages.error(request, f'A user with email "{email}" already exists.')
                return render(request, 'band/user_edit.html', {
                    'target_user': target_user,
                    'profile': profile,
                    'churches': Church.objects.filter(is_active=True) if caller_profile.is_superadmin else None,
                    'role_choices': UserProfile.ROLE_CHOICES if caller_profile.is_superadmin else None,
                    'is_superadmin': caller_profile.is_superadmin,
                })
            target_user.username = email
            target_user.email = email

        target_user.first_name = first_name
        target_user.last_name = last_name
        if new_password:
            target_user.set_password(new_password)
            profile.must_change_password = True
        target_user.save()

        if caller_profile.is_superadmin:
            church_id = request.POST.get('church', '')
            app_role = request.POST.get('app_role', 'user')
            profile.app_role = app_role
            if church_id:
                try:
                    profile.church = Church.objects.get(id=church_id)
                except Church.DoesNotExist:
                    profile.church = None
            else:
                profile.church = None

        profile.save()

        messages.success(request, f'User "{target_user.email}" updated.')
        return redirect('band:user_list')

    context = {
        'target_user': target_user,
        'profile': profile,
        'churches': Church.objects.filter(is_active=True) if caller_profile.is_superadmin else None,
        'role_choices': UserProfile.ROLE_CHOICES if caller_profile.is_superadmin else None,
        'is_superadmin': caller_profile.is_superadmin,
    }
    return render(request, 'band/user_edit.html', context)


@login_required
@admin_required
def user_delete(request, user_id):
    """Show delete confirmation for a user."""
    from django.contrib.auth.models import User

    try:
        caller_profile = request.user.profile
    except Exception:
        return redirect('band:home')

    target_user = get_object_or_404(User, id=user_id)
    profile, _ = UserProfile.objects.get_or_create(user=target_user)

    # Can't delete yourself
    if target_user == request.user:
        messages.error(request, "You cannot delete your own account.")
        return redirect('band:user_list')

    # Admins can only delete 'user' role accounts in their church
    if not caller_profile.is_superadmin:
        if profile.church != caller_profile.church:
            messages.error(request, "You don't have permission to delete this user.")
            return redirect('band:user_list')
        if profile.app_role != 'user':
            messages.error(request, "Admins can only delete regular user accounts.")
            return redirect('band:user_list')

    return render(request, 'band/user_delete.html', {
        'target_user': target_user,
        'profile': profile,
    })


@login_required
@admin_required
def user_delete_confirm(request, user_id):
    """Perform the actual user deletion."""
    from django.contrib.auth.models import User

    try:
        caller_profile = request.user.profile
    except Exception:
        return redirect('band:home')

    target_user = get_object_or_404(User, id=user_id)
    profile, _ = UserProfile.objects.get_or_create(user=target_user)

    if request.method != 'POST':
        return redirect('band:user_delete', user_id=user_id)

    # Re-check permissions
    if target_user == request.user:
        messages.error(request, "You cannot delete your own account.")
        return redirect('band:user_list')

    if not caller_profile.is_superadmin:
        if profile.church != caller_profile.church or profile.app_role != 'user':
            messages.error(request, "You don't have permission to delete this user.")
            return redirect('band:user_list')

    email = target_user.email or target_user.username
    target_user.delete()
    messages.success(request, f'User "{email}" has been deleted.')
    return redirect('band:user_list')


@login_required
def change_password(request):
    """Force password change on first login"""
    if request.method == 'POST':
        new_password = request.POST.get('new_password', '')
        confirm_password = request.POST.get('confirm_password', '')

        if not new_password:
            messages.error(request, 'Password cannot be empty.')
        elif len(new_password) < 6:
            messages.error(request, 'Password must be at least 6 characters.')
        elif new_password != confirm_password:
            messages.error(request, 'Passwords do not match.')
        else:
            request.user.set_password(new_password)
            request.user.save()

            # Clear the flag
            try:
                profile = request.user.profile
                profile.must_change_password = False
                profile.save()
            except Exception:
                pass

            # Re-authenticate so the user stays logged in
            from django.contrib.auth import update_session_auth_hash
            update_session_auth_hash(request, request.user)

            messages.success(request, 'Password updated successfully.')
            return redirect('band:home')

    return render(request, 'band/change_password.html')
