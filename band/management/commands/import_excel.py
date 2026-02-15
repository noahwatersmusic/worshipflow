from django.core.management.base import BaseCommand
from band.models import Person, Song, PersonSongPreference, Service, ServiceSong
import pandas as pd
from datetime import datetime


class Command(BaseCommand):
    help = 'Import data from Excel file into the database'

    def add_arguments(self, parser):
        parser.add_argument('excel_file', type=str, help='Path to the Excel file')

    def handle(self, *args, **options):
        excel_file = options['excel_file']
        self.stdout.write(f'Importing data from {excel_file}...')

        try:
            # Read Excel file
            xl = pd.ExcelFile(excel_file)

            # Import People
            self.stdout.write('\nImporting People...')
            people_df = pd.read_excel(xl, 'People')
            for _, row in people_df.iterrows():
                # Map role from Excel to model choices
                role_mapping = {
                    'Vocalist': 'vocalist',
                    'Instrumentalist': 'instrumentalist',
                    'Both': 'both',
                }
                role = role_mapping.get(row['Role'], 'both')

                # Map frequency
                frequency_mapping = {
                    'Core': 'core',
                    'Regular': 'regular',
                    'Occasional': 'occasional',
                }
                frequency = frequency_mapping.get(row.get('Frequency', ''), '')

                person, created = Person.objects.update_or_create(
                    person_id=row['PersonID'],
                    defaults={
                        'name': row['Name'],
                        'role': role,
                        'primary_instrument': row.get('Primary Instrument', ''),
                        'secondary_instrument': row.get('Secondary Instrument', ''),
                        'lead_vocal': row.get('Lead Vocal', False) == 'Yes',
                        'harmony_vocal': row.get('Harmony Vocal', False) == 'Yes',
                        'preferred_keys': row.get('Preferred Keys', ''),
                        'style_strengths': row.get('Style Strengths', ''),
                        'frequency': frequency,
                        'availability': row.get('Availability', ''),
                        'notes': row.get('Notes', ''),
                    }
                )
                if created:
                    self.stdout.write(f'  Created: {person.name}')
                else:
                    self.stdout.write(f'  Updated: {person.name}')

            # Import Songs
            self.stdout.write('\nImporting Songs...')
            songs_df = pd.read_excel(xl, 'Songs')
            for _, row in songs_df.iterrows():
                # Map tempo
                tempo_mapping = {
                    'Slow': 'slow',
                    'Medium': 'medium',
                    'Fast': 'fast',
                }
                tempo = tempo_mapping.get(row.get('Tempo', ''), '')

                # Handle date
                last_used = None
                if pd.notna(row.get('Last Used')):
                    try:
                        last_used = pd.to_datetime(row['Last Used']).date()
                    except:
                        pass

                song, created = Song.objects.update_or_create(
                    song_id=row['SongID'],
                    defaults={
                        'title': row['Title'],
                        'artist': row.get('Artist', ''),
                        'default_key': row.get('Default Key', ''),
                        'tempo': tempo,
                        'style': row.get('Style', ''),
                        'arrangement_notes': row.get('Arrangement Notes', ''),
                        'last_used': last_used,
                        'times_used': int(row.get('Times Used', 0)) if pd.notna(row.get('Times Used')) else 0,
                        'comfort_level': row.get('Comfort Level', ''),
                        'notes': row.get('Notes', ''),
                    }
                )
                if created:
                    self.stdout.write(f'  Created: {song.title}')
                else:
                    self.stdout.write(f'  Updated: {song.title}')

            # Import PersonSongMap
            self.stdout.write('\nImporting Person-Song Preferences...')
            psm_df = pd.read_excel(xl, 'PersonSongMap')
            for _, row in psm_df.iterrows():
                try:
                    person = Person.objects.get(person_id=row['PersonID'])
                    song = Song.objects.get(song_id=row['SongID'])

                    # Map confidence
                    confidence_mapping = {
                        'High': 'high',
                        'Medium': 'medium',
                        'Low': 'low',
                    }
                    confidence = confidence_mapping.get(row.get('Lead Confidence', ''), '')

                    pref, created = PersonSongPreference.objects.update_or_create(
                        entry_id=row['EntryID'],
                        defaults={
                            'person': person,
                            'song': song,
                            'preferred_key': row.get('Preferred Key', ''),
                            'can_lead': row.get('Lead', 'No') == 'Yes',
                            'confidence': confidence,
                            'notes': row.get('Notes', ''),
                        }
                    )
                    if created:
                        self.stdout.write(f'  Created: {person.name} - {song.title}')
                    else:
                        self.stdout.write(f'  Updated: {person.name} - {song.title}')
                except Person.DoesNotExist:
                    self.stdout.write(self.style.WARNING(f'  Skipped: Person {row["PersonID"]} not found'))
                except Song.DoesNotExist:
                    self.stdout.write(self.style.WARNING(f'  Skipped: Song {row["SongID"]} not found'))

            self.stdout.write(self.style.SUCCESS('\nData import completed successfully!'))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error importing data: {str(e)}'))
            raise e
