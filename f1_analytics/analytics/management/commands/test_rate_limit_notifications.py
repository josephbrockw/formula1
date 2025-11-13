"""
Test rate limit Slack notifications.

This command tests the Slack notification functions that are triggered
when a rate limit pause occurs, without actually waiting for the pause.

Usage:
    python manage.py test_rate_limit_notifications
    python manage.py test_rate_limit_notifications --pause  # Test pause notification only
    python manage.py test_rate_limit_notifications --resume # Test resume notification only
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta


class Command(BaseCommand):
    help = 'Test rate limit Slack notifications without actually pausing'

    def add_arguments(self, parser):
        parser.add_argument(
            '--pause',
            action='store_true',
            help='Test only the pause notification',
        )
        parser.add_argument(
            '--resume',
            action='store_true',
            help='Test only the resume notification',
        )

    def handle(self, *args, **options):
        from analytics.processing.rate_limiter import (
            _send_rate_limit_pause_notification,
            _send_rate_limit_resume_notification
        )
        
        self.stdout.write(self.style.WARNING('\n' + '='*60))
        self.stdout.write(self.style.WARNING('Testing Rate Limit Slack Notifications'))
        self.stdout.write(self.style.WARNING('='*60 + '\n'))
        
        test_pause = options['pause'] or (not options['pause'] and not options['resume'])
        test_resume = options['resume'] or (not options['pause'] and not options['resume'])
        
        # Test pause notification
        if test_pause:
            self.stdout.write(self.style.NOTICE('📤 Sending PAUSE notification...'))
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
        
        # Wait a moment between notifications if sending both
        if test_pause and test_resume:
            self.stdout.write('   Waiting 2 seconds before resume notification...\n')
            import time
            time.sleep(2)
        
        # Test resume notification
        if test_resume:
            self.stdout.write(self.style.NOTICE('📤 Sending RESUME notification...'))
            self.stdout.write('   This simulates the pause completing\n')
            
            try:
                _send_rate_limit_resume_notification()
                
                self.stdout.write(self.style.SUCCESS('Resume notification sent!'))
                self.stdout.write(f'   Resume time shown: {timezone.now().strftime("%I:%M:%S %p")}\n')
                
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'Failed to send resume notification: {e}\n'))
                import traceback
                traceback.print_exc()
        
        # Summary
        self.stdout.write('\n' + '='*60)
        self.stdout.write(self.style.SUCCESS('Notification test complete!'))
        self.stdout.write('='*60)
        
        self.stdout.write('\n📱 Check your Slack channel to verify notifications arrived.')
        self.stdout.write('\n💡 If no notifications appeared, check:')
        self.stdout.write('   1. SLACK_WEBHOOK_URL is set in settings')
        self.stdout.write('   2. The webhook URL is valid')
        self.stdout.write('   3. Your Slack app has proper permissions\n')
