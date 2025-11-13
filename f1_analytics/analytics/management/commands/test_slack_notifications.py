"""
Test Slack notifications for FastF1 import system.

This command tests all Slack notification functions without running actual imports.

Usage:
    python manage.py test_slack_notifications                    # Test all notifications
    python manage.py test_slack_notifications --pause             # Test rate limit pause only
    python manage.py test_slack_notifications --resume            # Test rate limit resume only
    python manage.py test_slack_notifications --completion       # Test import completion only
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta


class Command(BaseCommand):
    help = 'Test Slack notifications for FastF1 import system'

    def add_arguments(self, parser):
        parser.add_argument(
            '--pause',
            action='store_true',
            help='Test only the rate limit pause notification',
        )
        parser.add_argument(
            '--resume',
            action='store_true',
            help='Test only the rate limit resume notification',
        )
        parser.add_argument(
            '--completion',
            action='store_true',
            help='Test only the import completion notification',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING('\n' + '='*60))
        self.stdout.write(self.style.WARNING('Testing FastF1 Slack Notifications'))
        self.stdout.write(self.style.WARNING('='*60 + '\n'))
        
        # Determine which tests to run
        test_pause = options['pause'] or not any([options['pause'], options['resume'], options['completion']])
        test_resume = options['resume'] or not any([options['pause'], options['resume'], options['completion']])
        test_completion = options['completion'] or not any([options['pause'], options['resume'], options['completion']])
        
        # Test pause notification
        if test_pause:
            self._test_pause_notification()
        
        # Wait between notifications if sending multiple
        if test_pause and (test_resume or test_completion):
            self.stdout.write('   Waiting 2 seconds before next notification...\n')
            import time
            time.sleep(2)
        
        # Test resume notification
        if test_resume:
            self._test_resume_notification()
        
        # Wait between notifications if sending multiple
        if test_resume and test_completion:
            self.stdout.write('   Waiting 2 seconds before next notification...\n')
            import time
            time.sleep(2)
        
        # Test completion notification
        if test_completion:
            self._test_completion_notification()
        
        # Summary
        self.stdout.write('\n' + '='*60)
        self.stdout.write(self.style.SUCCESS('Notification tests complete!'))
        self.stdout.write('='*60)
        
        self.stdout.write('\n📱 Check your Slack channel to verify notifications arrived.')
        self.stdout.write('\n💡 If no notifications appeared, check:')
        self.stdout.write('   1. SLACK_WEBHOOK_URL is set in settings')
        self.stdout.write('   2. The webhook URL is valid')
        self.stdout.write('   3. Your Slack app has proper permissions\n')
    
    def _test_pause_notification(self):
        """Test rate limit pause notification."""
        from analytics.processing.rate_limiter import _send_rate_limit_pause_notification
        
        self.stdout.write(self.style.NOTICE('📤 Sending RATE LIMIT PAUSE notification...'))
        self.stdout.write('   This simulates a rate limit being hit\n')
        
        try:
            # Calculate a fake resume time (1 hour from now)
            resume_time = timezone.now() + timedelta(hours=1)
            
            _send_rate_limit_pause_notification(resume_time)
            
            self.stdout.write(self.style.SUCCESS('Pause notification sent!'))
            self.stdout.write(f'   Resume time shown: {resume_time.strftime("%I:%M:%S %p")}\n')
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Failed to send pause notification: {e}\n'))
            import traceback
            traceback.print_exc()
    
    def _test_resume_notification(self):
        """Test rate limit resume notification."""
        from analytics.processing.rate_limiter import _send_rate_limit_resume_notification
        
        self.stdout.write(self.style.NOTICE('📤 Sending RATE LIMIT RESUME notification...'))
        self.stdout.write('   This simulates the pause completing\n')
        
        try:
            _send_rate_limit_resume_notification()
            
            self.stdout.write(self.style.SUCCESS('Resume notification sent!'))
            self.stdout.write(f'   Resume time shown: {timezone.now().strftime("%I:%M:%S %p")}\n')
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Failed to send resume notification: {e}\n'))
            import traceback
            traceback.print_exc()
    
    def _test_completion_notification(self):
        """Test import completion notification."""
        from config.notifications import send_import_completion_notification
        
        self.stdout.write(self.style.NOTICE('📤 Sending IMPORT COMPLETION notification...'))
        self.stdout.write('   This simulates a successful import finishing\n')
        
        try:
            # Create a fake successful summary
            summary = {
                'status': 'complete',
                'year': 2025,
                'gaps_detected': 92,
                'sessions_processed': 92,
                'sessions_succeeded': 92,
                'sessions_failed': 0,
                'data_extracted': {
                    'weather': 92,
                    'circuit': 25,
                    'telemetry': 92
                },
                'duration_seconds': 8547.3  # ~2.4 hours
            }
            
            send_import_completion_notification(summary, year=2025, round_number=None)
            
            self.stdout.write(self.style.SUCCESS('Completion notification sent!'))
            self.stdout.write('   Showing: 2025 Season - Full Import (92 sessions, 2.4 hours)\n')
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Failed to send completion notification: {e}\n'))
            import traceback
            traceback.print_exc()
