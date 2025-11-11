"""
FastF1 data processing layer.

This module contains Prefect tasks and utility functions for loading,
extracting, and persisting FastF1 data.

Structure:
- loaders.py: Prefect tasks for loading FastF1 sessions
- extractors.py: Prefect tasks for extracting data from sessions
- persisters.py: Prefect tasks for persisting data to database
- utils.py: Helper functions and queries
- rate_limiter.py: Rate limit management tasks
"""
