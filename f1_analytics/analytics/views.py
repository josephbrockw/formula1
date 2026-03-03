from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Max, Q
from django.utils import timezone
from analytics.models import (
    User, CurrentLineup, Driver, DriverSnapshot, 
    Team, ConstructorSnapshot, Season, Session, SessionWeather,
    SessionResult, Lap, Telemetry, PitStop, Circuit, Corner
)
from analytics.forms import CurrentLineupForm


def dashboard(request):
    """
    Main dashboard view showing:
    - Current lineup for user 'joe'
    - All drivers sorted by price (most expensive first)
    - All teams sorted by price (most expensive first)
    """
    # Get the most recent snapshot date
    latest_date = DriverSnapshot.objects.aggregate(
        Max('snapshot_date')
    )['snapshot_date__max']
    
    # Try to get the current lineup for user 'joe'
    current_lineup = None
    lineup_data = None
    
    try:
        user = User.objects.get(username='joe')
        current_lineup = CurrentLineup.objects.filter(user=user).latest('updated_at')
        
        # Build lineup data with snapshot information
        if latest_date and current_lineup:
            lineup_data = {
                'lineup': current_lineup,
                'drivers': [],
                'teams': [],
                'total_budget': current_lineup.total_budget,
                'cap_space': current_lineup.cap_space,
            }
            
            # Get driver snapshots for the 5 drivers
            driver_ids = [
                current_lineup.driver1_id,
                current_lineup.driver2_id,
                current_lineup.driver3_id,
                current_lineup.driver4_id,
                current_lineup.driver5_id,
            ]
            
            driver_snapshots = DriverSnapshot.objects.filter(
                driver_id__in=driver_ids,
                snapshot_date=latest_date
            ).select_related('driver', 'team')
            
            # Create a lookup dict
            snapshot_dict = {snap.driver_id: snap for snap in driver_snapshots}
            
            # Build driver data with DRS flag
            for driver_id in driver_ids:
                snapshot = snapshot_dict.get(driver_id)
                if snapshot:
                    lineup_data['drivers'].append({
                        'driver': snapshot.driver,
                        'team': snapshot.team,
                        'price': snapshot.fantasy_price,
                        'price_change': snapshot.price_change,
                        'is_drs': driver_id == current_lineup.drs_driver_id,
                    })
            
            # Get team snapshots
            team_ids = [current_lineup.team1_id, current_lineup.team2_id]
            team_snapshots = ConstructorSnapshot.objects.filter(
                team_id__in=team_ids,
                snapshot_date=latest_date
            ).select_related('team')
            
            for snap in team_snapshots:
                lineup_data['teams'].append({
                    'team': snap.team,
                    'price': snap.fantasy_price,
                    'price_change': snap.price_change,
                })
    
    except User.DoesNotExist:
        pass
    except CurrentLineup.DoesNotExist:
        pass
    
    # Get all drivers with latest snapshot data (sorted by price)
    all_drivers = []
    if latest_date:
        driver_snapshots = DriverSnapshot.objects.filter(
            snapshot_date=latest_date
        ).select_related('driver', 'team').order_by('-fantasy_price')
        
        for snap in driver_snapshots:
            all_drivers.append({
                'driver': snap.driver,
                'team': snap.team,
                'price': snap.fantasy_price,
                'price_change': snap.price_change,
                'season_points': snap.season_points,
                'points_per_million': snap.points_per_million,
            })
    
    # Get all teams with latest snapshot data (sorted by price)
    all_teams = []
    if latest_date:
        team_snapshots = ConstructorSnapshot.objects.filter(
            snapshot_date=latest_date
        ).select_related('team').order_by('-fantasy_price')
        
        for snap in team_snapshots:
            all_teams.append({
                'team': snap.team,
                'price': snap.fantasy_price,
                'price_change': snap.price_change,
                'season_points': snap.season_points,
                'points_per_million': snap.points_per_million,
            })
    
    context = {
        'lineup_data': lineup_data,
        'all_drivers': all_drivers,
        'all_teams': all_teams,
        'latest_date': latest_date,
    }
    
    return render(request, 'analytics/dashboard.html', context)


def edit_lineup(request):
    """
    Edit current lineup for user 'joe'
    """
    try:
        user = User.objects.get(username='joe')
    except User.DoesNotExist:
        messages.error(request, "User 'joe' does not exist. Please create the user first.")
        return redirect('dashboard')
    
    # Get or create the current lineup
    try:
        lineup = CurrentLineup.objects.filter(user=user).latest('updated_at')
    except CurrentLineup.DoesNotExist:
        lineup = None
    
    if request.method == 'POST':
        form = CurrentLineupForm(request.POST, instance=lineup)
        if form.is_valid():
            lineup = form.save(commit=False)
            lineup.user = user
            lineup.save()
            messages.success(request, 'Lineup updated successfully!')
            return redirect('dashboard')
    else:
        form = CurrentLineupForm(instance=lineup)
    
    # Get latest snapshot date for displaying current prices
    latest_date = DriverSnapshot.objects.aggregate(Max('snapshot_date'))['snapshot_date__max']
    
    context = {
        'form': form,
        'lineup': lineup,
        'latest_date': latest_date,
    }
    
    return render(request, 'analytics/edit_lineup.html', context)


def data_status(request, year=None):
    """
    Data status dashboard showing gap detection and data completeness.
    
    Visualizes:
    - Overall data completeness summary
    - Coverage by data type (weather, drivers, telemetry, pit stops, circuit)
    - Sessions with missing data
    """
    # Default to current year if not specified
    if year is None:
        year = timezone.now().year
    
    # Get or detect gaps for the season
    from analytics.processing.gap_detection import detect_session_data_gaps
    
    try:
        season = Season.objects.get(year=year)
    except Season.DoesNotExist:
        # Season not found, show empty state
        context = {
            'year': year,
            'season_not_found': True,
        }
        return render(request, 'analytics/data_status.html', context)
    
    # Detect gaps
    gaps = detect_session_data_gaps(year)
    
    # Get all sessions for the season
    all_sessions = Session.objects.filter(race__season=season).count()
    
    # Calculate summary statistics
    sessions_with_gaps = len(gaps)
    complete_sessions = all_sessions - sessions_with_gaps
    completion_percentage = (complete_sessions / all_sessions * 100) if all_sessions > 0 else 0
    
    # Calculate estimated API calls (one per session with gaps)
    estimated_api_calls = sessions_with_gaps
    
    # Calculate data coverage by type
    total_circuits = Circuit.objects.count()
    
    # Count sessions with each data type
    sessions_with_weather = Session.objects.filter(
        race__season=season,
        id__in=SessionWeather.objects.filter(
            session__race__season=season
        ).values_list('session_id', flat=True)
    ).count()
    
    sessions_with_drivers = Session.objects.filter(
        race__season=season,
        id__in=SessionResult.objects.filter(
            session__race__season=season
        ).values_list('session_id', flat=True).distinct()
    ).count()
    
    sessions_with_telemetry = Session.objects.filter(
        race__season=season,
        id__in=Lap.objects.filter(
            session__race__season=season
        ).values_list('session_id', flat=True).distinct()
    ).count()
    
    sessions_with_pit_stops = Session.objects.filter(
        race__season=season,
        id__in=PitStop.objects.filter(
            session__race__season=season
        ).values_list('session_id', flat=True).distinct()
    ).count()
    
    circuits_with_data = Circuit.objects.filter(
        id__in=Corner.objects.values_list('circuit_id', flat=True).distinct()
    ).count()
    
    # Build data coverage dict
    data_coverage = {
        'weather': {
            'count': sessions_with_weather,
            'percentage': (sessions_with_weather / all_sessions * 100) if all_sessions > 0 else 0,
        },
        'drivers': {
            'count': sessions_with_drivers,
            'percentage': (sessions_with_drivers / all_sessions * 100) if all_sessions > 0 else 0,
        },
        'telemetry': {
            'count': sessions_with_telemetry,
            'percentage': (sessions_with_telemetry / all_sessions * 100) if all_sessions > 0 else 0,
        },
        'pit_stops': {
            'count': sessions_with_pit_stops,
            'percentage': (sessions_with_pit_stops / all_sessions * 100) if all_sessions > 0 else 0,
        },
        'circuit': {
            'circuits': circuits_with_data,
            'percentage': (circuits_with_data / total_circuits * 100) if total_circuits > 0 else 0,
        },
    }
    
    context = {
        'year': year,
        'summary': {
            'total_sessions': all_sessions,
            'complete_sessions': complete_sessions,
            'sessions_with_gaps': sessions_with_gaps,
            'completion_percentage': completion_percentage,
            'estimated_api_calls': estimated_api_calls,
            'total_circuits': total_circuits,
        },
        'data_coverage': data_coverage,
        'gaps': gaps,
    }
    
    return render(request, 'analytics/data_status.html', context)
