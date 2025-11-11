"""
Pipeline and infrastructure models.

These models track internal data import/processing state and are not
used for ML/RL training. They exist to manage the data pipeline itself.

Models:
- SessionLoadStatus: Tracks what FastF1 data has been loaded for each session
"""

from django.db import models
from .events import Session


class SessionLoadStatus(models.Model):
    """
    Tracks what FastF1 data has been loaded for each session.
    
    Used by the import pipeline to:
    - Avoid duplicate session.load() calls
    - Track which data types have been extracted
    - Manage rate limiting
    - Store Prefect flow metadata
    
    Simple fields (booleans, timestamps) for queryability.
    JSON field for complex Prefect metadata (flow IDs, cache keys, errors).
    """
    
    session = models.OneToOneField(
        Session,
        on_delete=models.CASCADE,
        related_name='load_status',
        help_text="The session this status tracks"
    )
    
    # Data type flags (actual model fields for easy queries)
    has_circuit = models.BooleanField(
        default=False,
        help_text="Whether circuit geometry data has been extracted"
    )
    
    has_weather = models.BooleanField(
        default=False,
        help_text="Whether weather data has been extracted"
    )
    
    has_lap_times = models.BooleanField(
        default=False,
        help_text="Whether lap time data has been extracted"
    )
    
    has_telemetry = models.BooleanField(
        default=False,
        help_text="Whether telemetry data has been extracted"
    )
    
    # Timestamps for each data type
    circuit_loaded_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When circuit data was loaded"
    )
    
    weather_loaded_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When weather data was loaded"
    )
    
    laps_loaded_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When lap time data was loaded"
    )
    
    telemetry_loaded_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When telemetry data was loaded"
    )
    
    # Rate limit tracking
    last_api_call = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of last FastF1 API call for this session"
    )
    
    api_calls_count = models.IntegerField(
        default=0,
        help_text="Number of times session.load() was called"
    )
    
    # Prefect metadata (JSONField for complex data)
    prefect_metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Prefect-specific metadata: flow IDs, cache keys, error history"
    )
    # Example structure:
    # {
    #     'last_flow_run_id': 'abc-123-def-456',
    #     'last_flow_run_name': 'import-weather-2025',
    #     'session_cache_key': 'fastf1_2025_1_R',
    #     'error_history': [
    #         {'timestamp': '2025-01-15T10:30:00Z', 'error': 'RateLimitExceeded', 'retries': 3}
    #     ],
    #     'last_successful_load': '2025-01-15T10:35:00Z'
    # }
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['session__race__season', 'session__race__round_number', 'session__session_number']
        verbose_name = 'Session Load Status'
        verbose_name_plural = 'Session Load Statuses'
        indexes = [
            models.Index(fields=['has_circuit']),
            models.Index(fields=['has_weather']),
            models.Index(fields=['has_lap_times']),
            models.Index(fields=['last_api_call']),
        ]
    
    def __str__(self):
        loaded_types = []
        if self.has_circuit:
            loaded_types.append('circuit')
        if self.has_weather:
            loaded_types.append('weather')
        if self.has_lap_times:
            loaded_types.append('laps')
        if self.has_telemetry:
            loaded_types.append('telemetry')
        
        status = ', '.join(loaded_types) if loaded_types else 'none'
        return f"{self.session} - Loaded: {status}"
    
    @property
    def loaded_data_types(self):
        """List of data types that have been loaded"""
        types = []
        if self.has_circuit:
            types.append('circuit')
        if self.has_weather:
            types.append('weather')
        if self.has_lap_times:
            types.append('laps')
        if self.has_telemetry:
            types.append('telemetry')
        return types
    
    @property
    def missing_data_types(self):
        """List of data types that haven't been loaded yet"""
        all_types = ['circuit', 'weather', 'laps', 'telemetry']
        return [t for t in all_types if t not in self.loaded_data_types]
    
    def mark_loaded(self, data_type, timestamp=None, flow_run_id=None):
        """Mark a specific data type as loaded"""
        from django.utils import timezone as django_timezone
        
        if timestamp is None:
            timestamp = django_timezone.now()
        
        # Update flag and timestamp
        if data_type == 'circuit':
            self.has_circuit = True
            self.circuit_loaded_at = timestamp
        elif data_type == 'weather':
            self.has_weather = True
            self.weather_loaded_at = timestamp
        elif data_type == 'laps':
            self.has_lap_times = True
            self.laps_loaded_at = timestamp
        elif data_type == 'telemetry':
            self.has_telemetry = True
            self.telemetry_loaded_at = timestamp
        
        # Update Prefect metadata
        if flow_run_id:
            if 'load_history' not in self.prefect_metadata:
                self.prefect_metadata['load_history'] = []
            self.prefect_metadata['load_history'].append({
                'data_type': data_type,
                'flow_run_id': flow_run_id,
                'timestamp': timestamp.isoformat()
            })
            self.prefect_metadata['last_flow_run_id'] = flow_run_id
        
        self.save()
