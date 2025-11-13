"""
Management command to check driver data integrity across sources.

This command helps identify:
- Drivers missing FastF1 identifiers (driver_number, abbreviation)
- Potential duplicate drivers that should be merged
- Name variations between Formula1.com and FastF1

Usage:
    python manage.py check_driver_integrity
    python manage.py check_driver_integrity --verbose
"""

from django.core.management.base import BaseCommand
from analytics.models import Driver


def get_unmatched_drivers_report() -> dict:
    """Generate report of drivers needing manual review."""
    all_drivers = Driver.objects.all()
    report = {
        'drivers_without_numbers': [],
        'drivers_without_abbreviations': [],
        'potential_duplicates': []
    }
    
    # Missing identifiers
    for driver in all_drivers:
        if not driver.driver_number:
            report['drivers_without_numbers'].append({
                'id': driver.id,
                'full_name': driver.full_name,
                'abbreviation': driver.abbreviation or 'N/A'
            })
        if not driver.abbreviation:
            report['drivers_without_abbreviations'].append({
                'id': driver.id,
                'full_name': driver.full_name,
                'driver_number': driver.driver_number or 'N/A'
            })
    
    # Potential duplicates (same last name)
    last_names = {}
    for driver in all_drivers:
        last = driver.last_name.lower()
        last_names.setdefault(last, []).append(driver)
    
    for last_name, drivers in last_names.items():
        if len(drivers) > 1:
            report['potential_duplicates'].append({
                'last_name': last_name,
                'drivers': [
                    {
                        'id': d.id,
                        'full_name': d.full_name,
                        'driver_number': d.driver_number or 'N/A',
                        'abbreviation': d.abbreviation or 'N/A'
                    }
                    for d in drivers
                ]
            })
    
    return report


class Command(BaseCommand):
    help = 'Check driver data integrity across Formula1.com and FastF1 sources'

    def add_arguments(self, parser):
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed information for each issue'
        )

    def handle(self, *args, **options):
        verbose = options.get('verbose', False)
        
        self.stdout.write(self.style.SUCCESS('\n' + '='*70))
        self.stdout.write(self.style.SUCCESS('Driver Data Integrity Report'))
        self.stdout.write(self.style.SUCCESS('='*70 + '\n'))
        
        # Get total driver count
        total_drivers = Driver.objects.count()
        self.stdout.write(f"Total drivers in database: {total_drivers}\n")
        
        # Get report
        report = get_unmatched_drivers_report()
        
        # Section 1: Drivers without FastF1 identifiers
        self._report_missing_identifiers(report, verbose)
        
        # Section 2: Potential duplicates
        self._report_potential_duplicates(report, verbose)
        
        # Section 3: Summary and recommendations
        self._report_summary(report)

    def _report_missing_identifiers(self, report, verbose):
        """Report drivers missing FastF1 identifiers"""
        self.stdout.write(self.style.WARNING('1. Drivers Missing FastF1 Identifiers'))
        self.stdout.write('-' * 70)
        
        missing_numbers = report['drivers_without_numbers']
        missing_abbrevs = report['drivers_without_abbreviations']
        
        if not missing_numbers and not missing_abbrevs:
            self.stdout.write(self.style.SUCCESS('✓ All drivers have complete identifiers\n'))
            return
        
        if missing_numbers:
            self.stdout.write(f"\nDrivers without driver_number: {len(missing_numbers)}")
            if verbose:
                for driver in missing_numbers:
                    self.stdout.write(
                        f"  • {driver['full_name']} (ID: {driver['id']}, "
                        f"Abbrev: {driver['abbreviation']})"
                    )
        
        if missing_abbrevs:
            self.stdout.write(f"\nDrivers without abbreviation: {len(missing_abbrevs)}")
            if verbose:
                for driver in missing_abbrevs:
                    self.stdout.write(
                        f"  • {driver['full_name']} (ID: {driver['id']}, "
                        f"Number: {driver['driver_number']})"
                    )
        
        self.stdout.write(
            self.style.WARNING(
                "\nℹ  These drivers may have been imported from Formula1.com but not yet "
                "encountered in FastF1 data.\n   They will be automatically updated when "
                "telemetry is imported for sessions they participate in.\n"
            )
        )

    def _report_potential_duplicates(self, report, verbose):
        """Report potential duplicate drivers"""
        self.stdout.write(self.style.WARNING('2. Potential Duplicate Drivers'))
        self.stdout.write('-' * 70)
        
        duplicates = report['potential_duplicates']
        
        if not duplicates:
            self.stdout.write(self.style.SUCCESS('✓ No potential duplicates detected\n'))
            return
        
        self.stdout.write(f"\nFound {len(duplicates)} groups with same last name:")
        
        for group in duplicates:
            self.stdout.write(f"\n  Last name: {group['last_name'].title()}")
            for driver in group['drivers']:
                self.stdout.write(
                    f"    • {driver['full_name']} (ID: {driver['id']}, "
                    f"#{driver['driver_number']}, {driver['abbreviation']})"
                )
        
        self.stdout.write(
            self.style.WARNING(
                "\nℹ  Review these drivers to ensure they are not duplicates. "
                "If duplicates exist:\n"
                "   1. Merge data manually in Django admin or shell\n"
                "   2. Delete the duplicate driver record\n"
            )
        )

    def _report_summary(self, report):
        """Print summary and recommendations"""
        self.stdout.write(self.style.SUCCESS('\n3. Summary & Recommendations'))
        self.stdout.write('-' * 70)
        
        missing_numbers = len(report['drivers_without_numbers'])
        missing_abbrevs = len(report['drivers_without_abbreviations'])
        duplicates = len(report['potential_duplicates'])
        
        issues_found = missing_numbers + missing_abbrevs + duplicates
        
        if issues_found == 0:
            self.stdout.write(self.style.SUCCESS('\n✓ No data integrity issues detected!'))
            self.stdout.write(
                'Your driver data is consistent across Formula1.com and FastF1 sources.\n'
            )
        else:
            self.stdout.write(f"\nTotal potential issues: {issues_found}")
            
            self.stdout.write('\nRecommended actions:')
            
            if missing_numbers or missing_abbrevs:
                self.stdout.write(
                    '\n  1. Import telemetry data to automatically populate missing identifiers:'
                    '\n     python manage.py import_fastf1 --year 2024'
                )
            
            if duplicates:
                self.stdout.write(
                    '\n  2. Review potential duplicates in Django admin:'
                    '\n     - Check if drivers with same last name are truly different people'
                    '\n     - Merge any duplicate records manually'
                )
            
            self.stdout.write(
                '\n  3. Re-run this command after fixes to verify:'
                '\n     python manage.py check_driver_integrity\n'
            )
        
        self.stdout.write(self.style.SUCCESS('\n' + '='*70 + '\n'))
