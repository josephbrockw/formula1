"""
Base test class for processing tests with fast Prefect task configuration.

Provides FastPrefectTasksMixin that automatically replaces Prefect tasks
with fast-retry versions for testing.
"""

from unittest import mock
from django.test import TestCase
from analytics.processing import loaders


class FastPrefectTasksMixin:
    """
    Mixin to replace Prefect tasks with fast-retry versions for tests.
    
    Production: 3 retries with 60 second delays
    Tests: 1 retry with 1 second delay
    
    Usage:
        class MyTests(FastPrefectTasksMixin, TestCase):
            def test_something(self):
                # load_fastf1_session now has fast retries
                ...
    """
    
    def setUp(self):
        super().setUp()
        
        # Create fast version of load_fastf1_session task
        fast_load_task = loaders.load_fastf1_session.with_options(
            retries=1,
            retry_delay_seconds=1
        )
        
        # Patch it for this test
        self.fast_task_patcher = mock.patch.object(
            loaders, 
            'load_fastf1_session',
            fast_load_task
        )
        self.fast_task_patcher.start()
    
    def tearDown(self):
        super().tearDown()
        self.fast_task_patcher.stop()
