"""
Test telemetry import for debugging.

Usage:
    python manage.py test_telemetry_import --year 2024 --round 1 --session "Race"
"""

from django.core.management.base import BaseCommand
import fastf1

from analytics.flows.import_drivers import extract_driver_info, save_driver_info_to_db
from analytics.flows.import_telemetry import extract_lap_data, save_telemetry_to_db
from analytics.models import Session


class Command(BaseCommand):
    help = 'Test telemetry import for a specific session'

    def add_arguments(self, parser):
        parser.add_argument('--year', type=int, required=True, help='Season year')
        parser.add_argument('--round', type=int, required=True, help='Round number')
        parser.add_argument('--session', type=str, default='Race', help='Session type (e.g., Race, Qualifying)')

    def handle(self, *args, **options):
        year = options['year']
        round_num = options['round']
        session_type = options['session']
        
        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"Testing telemetry import: {year} Round {round_num} - {session_type}")
        self.stdout.write(f"{'='*60}\n")
        
        # Step 1: Find session in database
        try:
            session = Session.objects.get(
                race__season__year=year,
                race__round_number=round_num,
                session_type=session_type
            )
            self.stdout.write(self.style.SUCCESS(f"✓ Found session: {session}"))
        except Session.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"✗ Session not found in database"))
            return
        
        # Step 2: Load FastF1 session
        try:
            self.stdout.write("\nLoading FastF1 session...")
            f1_session = fastf1.get_session(year, round_num, session_type)
            f1_session.load()
            self.stdout.write(self.style.SUCCESS(f"✓ FastF1 session loaded"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"✗ Failed to load FastF1 session: {e}"))
            return
        
        # Step 3: Check session.results
        try:
            self.stdout.write("\nChecking session.results...")
            if hasattr(f1_session, 'results') and f1_session.results is not None:
                results_count = len(f1_session.results)
                self.stdout.write(self.style.SUCCESS(f"✓ Found {results_count} drivers in session.results"))
                
                # Show first few drivers
                for idx, row in f1_session.results.head(3).iterrows():
                    self.stdout.write(
                        f"  - {row.get('FullName', 'N/A')} "
                        f"(#{row.get('DriverNumber', 'N/A')}, {row.get('Abbreviation', 'N/A')})"
                    )
            else:
                self.stdout.write(self.style.WARNING(f"⚠ session.results is None or missing"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"✗ Error checking session.results: {e}"))
        
        # Step 4: Check laps data
        try:
            self.stdout.write("\nChecking laps data...")
            if hasattr(f1_session, 'laps') and f1_session.laps is not None:
                laps_count = len(f1_session.laps)
                self.stdout.write(self.style.SUCCESS(f"✓ Found {laps_count} laps"))
                
                # Show unique drivers in laps
                unique_drivers = f1_session.laps['Driver'].unique()
                self.stdout.write(f"  Drivers: {', '.join(unique_drivers)}")
            else:
                self.stdout.write(self.style.WARNING(f"⚠ Laps data is None or missing"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"✗ Error checking laps: {e}"))
        
        # Step 5: Test driver info extraction (direct call, not via Prefect)
        try:
            self.stdout.write("\n" + "="*60)
            self.stdout.write("STEP 1: Testing driver info extraction...")
            self.stdout.write("="*60)
            
            # Import the actual extraction logic
            from analytics.flows.import_drivers import extract_driver_info as extract_fn
            from analytics.flows.import_drivers import save_driver_info_to_db as save_fn
            
            # Manually extract driver data without Prefect context
            driver_data = self._extract_driver_info_manual(f1_session)
            
            if driver_data:
                drivers_count = len(driver_data.get('drivers', []))
                self.stdout.write(self.style.SUCCESS(f"✓ Extracted {drivers_count} drivers"))
                
                # Show sample drivers
                for driver_dict in driver_data['drivers'][:3]:
                    self.stdout.write(
                        f"  - {driver_dict['full_name']} "
                        f"(#{driver_dict['driver_number']}, {driver_dict['abbreviation']})"
                    )
                
                # Test save
                save_result = self._save_driver_info_manual(session.id, driver_data)
                if save_result['status'] == 'success':
                    self.stdout.write(self.style.SUCCESS(
                        f"✓ Saved: {save_result['drivers_created']} created, "
                        f"{save_result['drivers_updated']} updated, "
                        f"{save_result['results_created']} session results"
                    ))
                else:
                    self.stdout.write(self.style.ERROR(f"✗ Driver save failed: {save_result.get('error')}"))
            else:
                self.stdout.write(self.style.ERROR(f"✗ Failed to extract driver info"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"✗ Driver extraction error: {e}"))
            import traceback
            traceback.print_exc()
        
        # Step 6: Test telemetry extraction (direct call, not via Prefect)
        try:
            self.stdout.write("\n" + "="*60)
            self.stdout.write("STEP 2: Testing telemetry extraction...")
            self.stdout.write("="*60)
            
            telemetry_data = self._extract_lap_data_manual(f1_session)
            
            if telemetry_data:
                laps = telemetry_data.get('laps', [])
                pit_stops = telemetry_data.get('pit_stops', [])
                self.stdout.write(self.style.SUCCESS(f"✓ Extracted {len(laps)} laps, {len(pit_stops)} pit stops"))
                
                # Show sample lap
                if laps:
                    sample_lap = laps[0]
                    self.stdout.write(f"\nSample lap:")
                    self.stdout.write(f"  Driver: {sample_lap.get('full_name', 'N/A')}")
                    self.stdout.write(f"  Number: #{sample_lap.get('driver_number', 'N/A')}")
                    self.stdout.write(f"  Lap: {sample_lap.get('lap_number', 'N/A')}")
                    self.stdout.write(f"  Time: {sample_lap.get('lap_time', 'N/A')}")
                
                # Test save
                self.stdout.write("\nAttempting to save to database...")
                save_result = self._save_telemetry_manual(session.id, telemetry_data)
                if save_result['status'] == 'success':
                    self.stdout.write(self.style.SUCCESS(
                        f"✓ Saved: {save_result['laps_created']} laps, "
                        f"{save_result['pit_stops_created']} pit stops"
                    ))
                else:
                    self.stdout.write(self.style.ERROR(f"✗ Telemetry save failed: {save_result.get('error')}"))
            else:
                self.stdout.write(self.style.ERROR(f"✗ Failed to extract telemetry data"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"✗ Telemetry extraction error: {e}"))
            import traceback
            traceback.print_exc()
        
        self.stdout.write(f"\n{'='*60}")
        self.stdout.write("Test complete")
        self.stdout.write(f"{'='*60}\n")
    
    def _extract_driver_info_manual(self, f1_session):
        """Extract driver info without Prefect context"""
        import pandas as pd
        
        try:
            if not hasattr(f1_session, 'results') or f1_session.results is None or f1_session.results.empty:
                return None
            
            results_df = f1_session.results
            drivers_data = []
            
            for _, driver_result in results_df.iterrows():
                driver_dict = {
                    'full_name': str(driver_result.get('FullName', '')),
                    'driver_number': str(driver_result.get('DriverNumber', '')),
                    'abbreviation': str(driver_result.get('Abbreviation', '')),
                    'team_name': str(driver_result.get('TeamName', '')),
                    'team_color': str(driver_result.get('TeamColor', '')),
                    'position': int(driver_result.get('Position', 0)) if pd.notna(driver_result.get('Position')) else None,
                    'grid_position': int(driver_result.get('GridPosition', 0)) if pd.notna(driver_result.get('GridPosition')) else None,
                    'status': str(driver_result.get('Status', '')),
                }
                
                if not driver_dict['full_name'] or not driver_dict['driver_number']:
                    continue
                
                drivers_data.append(driver_dict)
            
            return {'drivers': drivers_data}
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error in manual extraction: {e}"))
            return None
    
    def _save_driver_info_manual(self, session_id, driver_data):
        """Save driver info without Prefect context"""
        from analytics.models import Driver, Team, Session, SessionResult
        from analytics.processing.driver_matching import find_driver_by_fastf1_data
        
        drivers_created = 0
        drivers_updated = 0
        drivers_skipped = 0
        results_created = 0
        
        try:
            session = Session.objects.get(id=session_id)
        except Session.DoesNotExist:
            return {
                'status': 'failed',
                'error': f'Session {session_id} not found',
                'drivers_created': 0,
                'drivers_updated': 0,
                'drivers_skipped': 0,
                'results_created': 0,
            }
        
        try:
            for driver_dict in driver_data.get('drivers', []):
                full_name = driver_dict['full_name']
                driver_number = driver_dict['driver_number']
                abbreviation = driver_dict['abbreviation']
                team_name = driver_dict['team_name']
                
                try:
                    driver, match_method = find_driver_by_fastf1_data(
                        full_name=full_name,
                        driver_number=driver_number,
                        abbreviation=abbreviation,
                        create_if_missing=True
                    )
                    
                    if not driver:
                        drivers_skipped += 1
                        continue
                    
                    is_new = match_method == "created_new"
                    updated = False
                    
                    if driver.driver_number != driver_number:
                        driver.driver_number = driver_number
                        updated = True
                    
                    if driver.abbreviation != abbreviation:
                        driver.abbreviation = abbreviation
                        updated = True
                    
                    if team_name:
                        team, _ = Team.objects.get_or_create(
                            name=team_name,
                            defaults={'short_name': team_name[:3].upper()}
                        )
                        if driver.current_team != team:
                            driver.current_team = team
                            updated = True
                    
                    if updated:
                        driver.save()
                        if is_new:
                            drivers_created += 1
                        else:
                            drivers_updated += 1
                    elif is_new:
                        drivers_created += 1
                    
                    # Create SessionResult
                    team = driver.current_team if driver.current_team else None
                    session_result, result_created = SessionResult.objects.update_or_create(
                        session=session,
                        driver=driver,
                        defaults={
                            'team': team,
                            'position': driver_dict.get('position'),
                            'grid_position': driver_dict.get('grid_position'),
                            'status': driver_dict.get('status', ''),
                            'driver_number': driver_number,
                            'abbreviation': abbreviation,
                        }
                    )
                    
                    results_created += 1  # Count all (created or updated)
                        
                except Exception as e:
                    drivers_skipped += 1
                    continue
            
            return {
                'status': 'success',
                'drivers_created': drivers_created,
                'drivers_updated': drivers_updated,
                'drivers_skipped': drivers_skipped,
                'results_created': results_created,
            }
        except Exception as e:
            return {
                'status': 'failed',
                'error': str(e),
                'drivers_created': drivers_created,
                'drivers_updated': drivers_updated,
                'drivers_skipped': drivers_skipped,
                'results_created': results_created,
            }
    
    def _extract_lap_data_manual(self, f1_session):
        """Extract lap data without Prefect context"""
        import pandas as pd
        
        try:
            laps_df = f1_session.laps
            if laps_df is None or laps_df.empty:
                return None
            
            # Build driver info map
            driver_info_map = {}
            if hasattr(f1_session, 'results') and f1_session.results is not None:
                for _, driver_result in f1_session.results.iterrows():
                    abbr = str(driver_result.get('Abbreviation', ''))
                    driver_info_map[abbr] = {
                        'full_name': str(driver_result.get('FullName', '')),
                        'driver_number': str(driver_result.get('DriverNumber', ''))
                    }
            
            # Extract laps
            laps_data = []
            for _, lap_row in laps_df.iterrows():
                driver_abbr = str(lap_row.get('Driver', ''))
                driver_info = driver_info_map.get(driver_abbr, {})
                
                lap_dict = {
                    'driver_number': str(lap_row.get('DriverNumber', '')),
                    'full_name': driver_info.get('full_name', ''),
                    'lap_number': int(lap_row.get('LapNumber', 0)),
                    'lap_time': self._to_seconds(lap_row.get('LapTime')),
                    'abbreviation': driver_abbr,
                }
                laps_data.append(lap_dict)
            
            return {
                'laps': laps_data,
                'pit_stops': []  # Simplified for testing
            }
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error in lap extraction: {e}"))
            return None
    
    def _save_telemetry_manual(self, session_id, telemetry_data):
        """Save telemetry without Prefect context"""
        from analytics.models import Session, Lap
        from analytics.processing.driver_matching import find_driver_by_fastf1_data
        
        laps_created = 0
        laps_skipped = 0
        
        try:
            session = Session.objects.get(id=session_id)
        except Session.DoesNotExist:
            return {'status': 'failed', 'error': 'Session not found', 'laps_created': 0}
        
        try:
            # Sample first few laps for debugging
            sample_size = min(5, len(telemetry_data.get('laps', [])))
            
            for idx, lap_dict in enumerate(telemetry_data.get('laps', [])):
                full_name = lap_dict.get('full_name', '')
                driver_number = lap_dict.get('driver_number', '')
                abbreviation = lap_dict.get('abbreviation', '')
                
                if not full_name and not driver_number:
                    if idx < sample_size:
                        print(f"  [Lap {idx+1}] Skipped - no name or number")
                    laps_skipped += 1
                    continue
                
                driver, _ = find_driver_by_fastf1_data(
                    full_name=full_name,
                    driver_number=driver_number,
                    abbreviation=abbreviation,
                    create_if_missing=False
                )
                
                if not driver:
                    if idx < sample_size:
                        print(f"  [Lap {idx+1}] Skipped - driver not found: {full_name} (#{driver_number})")
                    laps_skipped += 1
                    continue
                
                # Create lap (simplified - no team lookup)
                lap, created = Lap.objects.update_or_create(
                    session=session,
                    driver=driver,
                    lap_number=lap_dict['lap_number'],
                    defaults={
                        'lap_time': lap_dict.get('lap_time'),
                        'driver_number': driver_number,
                    }
                )
                
                if idx < sample_size:
                    print(f"  [Lap {idx+1}] {driver.full_name} - Lap {lap_dict['lap_number']} - {'CREATED' if created else 'UPDATED'}")
                
                if created:
                    laps_created += 1
            
            return {
                'status': 'success',
                'laps_created': laps_created,
                'laps_skipped': laps_skipped,
                'pit_stops_created': 0
            }
        except Exception as e:
            return {
                'status': 'failed',
                'error': str(e),
                'laps_created': laps_created
            }
    
    def _to_seconds(self, timedelta_val):
        """Convert pandas Timedelta to seconds"""
        import pandas as pd
        if pd.isna(timedelta_val):
            return None
        try:
            return timedelta_val.total_seconds()
        except:
            return None
