"""
Driver matching utilities for cross-source data integrity.

Matches drivers between Formula1.com (CSV) and FastF1 API sources.
Formula1.com names are canonical; FastF1 enriches with driver_number/abbreviation.
"""

from typing import Optional, Tuple
from analytics.models import Driver
import logging

logger = logging.getLogger(__name__)


def normalize_name(name: str) -> str:
    """Normalize driver name for comparison (case-insensitive, stripped)."""
    return name.strip().title() if name else ""


def find_driver_by_fastf1_data(
    full_name: str,
    driver_number: str = None,
    abbreviation: str = None,
    create_if_missing: bool = False
) -> Tuple[Optional[Driver], str]:
    """
    Find driver using multiple fallback strategies.
    
    Priority: exact_name → driver_number → abbreviation → normalized_name → unique_last_name
    
    Returns: (Driver or None, match_method)
    """
    # 1. Exact match (case-insensitive)
    if driver := Driver.objects.filter(full_name__iexact=full_name).first():
        return driver, "exact_name"
    
    # 2. Driver number
    if driver_number and (driver := Driver.objects.filter(driver_number=driver_number).first()):
        logger.info(f"Matched by number: {full_name} -> {driver.full_name} (#{driver_number})")
        return driver, "driver_number"
    
    # 3. Abbreviation
    if abbreviation and (driver := Driver.objects.filter(abbreviation__iexact=abbreviation).first()):
        logger.info(f"Matched by abbreviation: {full_name} -> {driver.full_name} ({abbreviation})")
        return driver, "abbreviation"
    
    # 4. Normalized name (handles case/whitespace)
    normalized = normalize_name(full_name)
    for existing in Driver.objects.all():
        if normalize_name(existing.full_name) == normalized:
            logger.info(f"Matched by normalized name: {full_name} -> {existing.full_name}")
            return existing, "normalized_name"
    
    # 5. Unique last name
    if full_name and ' ' in full_name:
        last_name = full_name.split()[-1]
        matches = Driver.objects.filter(last_name__iexact=last_name)
        if matches.count() == 1:
            driver = matches.first()
            logger.info(f"Matched by unique last name: {full_name} -> {driver.full_name}")
            return driver, "unique_last_name"
    
    # No match
    if create_if_missing:
        parts = full_name.split() if ' ' in full_name else [full_name, '']
        driver = Driver.objects.create(
            full_name=full_name,
            first_name=parts[0],
            last_name=' '.join(parts[1:]),
            driver_number=driver_number or '',
            abbreviation=abbreviation or ''
        )
        logger.warning(f"Created new driver: {full_name} (#{driver_number})")
        return driver, "created_new"
    
    logger.warning(f"No match found: {full_name} (#{driver_number}, {abbreviation})")
    return None, "no_match"


def update_driver_identifiers(
    driver: Driver,
    driver_number: str = None,
    abbreviation: str = None,
    fastf1_name: str = None
) -> bool:
    """Update driver's FastF1 identifiers if not set. Returns True if updated."""
    updated = False
    
    if driver_number and (not driver.driver_number or driver.driver_number != driver_number):
        driver.driver_number = driver_number
        updated = True
        logger.info(f"Updated {driver.full_name} driver_number: {driver_number}")
    
    if abbreviation and (not driver.abbreviation or driver.abbreviation != abbreviation):
        driver.abbreviation = abbreviation
        updated = True
        logger.info(f"Updated {driver.full_name} abbreviation: {abbreviation}")
    
    if fastf1_name and normalize_name(driver.full_name) != normalize_name(fastf1_name):
        logger.info(f"Name variation: DB='{driver.full_name}' FastF1='{fastf1_name}' (keeping DB)")
    
    if updated:
        driver.save()
    
    return updated
