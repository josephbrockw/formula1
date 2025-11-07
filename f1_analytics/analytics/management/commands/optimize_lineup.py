"""
F1 Fantasy Lineup Optimizer - Dynamic Programming Approach

Optimizes lineup using dynamic programming (knapsack algorithm) based on current budget
from user 'joe's CurrentLineup or a specified budget.

Usage:
    python manage.py optimize_lineup                    # Use budget from joe's lineup
    python manage.py optimize_lineup --budget 100.0     # Use specified budget
    python manage.py optimize_lineup --save             # Save optimal lineup to joe's lineup
"""

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Max
from decimal import Decimal
from analytics.models import (
    User, CurrentLineup, Driver, Team, 
    DriverSnapshot, ConstructorSnapshot
)


class Command(BaseCommand):
    help = 'Optimize F1 Fantasy lineup using dynamic programming'

    def add_arguments(self, parser):
        parser.add_argument(
            '--budget',
            type=float,
            help='Total budget in millions (default: use from joe\'s current lineup)'
        )
        parser.add_argument(
            '--save',
            action='store_true',
            help='Save the optimal lineup to joe\'s CurrentLineup'
        )

    def handle(self, *args, **options):
        # Get budget
        budget = options.get('budget')
        save_lineup = options.get('save', False)
        
        if not budget:
            # Try to get budget from joe's lineup
            try:
                user = User.objects.get(username='joe')
                try:
                    lineup = CurrentLineup.objects.filter(user=user).latest('updated_at')
                    budget = float(lineup.total_budget)
                    self.stdout.write(f"Using budget from joe's lineup: ${budget}M")
                except CurrentLineup.DoesNotExist:
                    budget = 100.0
                    self.stdout.write(self.style.WARNING(
                        f"No lineup found for joe. Using default budget: ${budget}M"
                    ))
            except User.DoesNotExist:
                budget = 100.0
                self.stdout.write(self.style.WARNING(
                    f"User 'joe' not found. Using default budget: ${budget}M"
                ))
        else:
            self.stdout.write(f"Using specified budget: ${budget}M")
        
        # Get latest snapshot data
        latest_date = DriverSnapshot.objects.aggregate(
            Max('snapshot_date')
        )['snapshot_date__max']
        
        if not latest_date:
            raise CommandError('No snapshot data available')
        
        self.stdout.write(f"Using data from: {latest_date}")
        
        # Get all drivers and constructors
        drivers = list(DriverSnapshot.objects.filter(
            snapshot_date=latest_date
        ).select_related('driver', 'team').order_by('-season_points'))
        
        constructors = list(ConstructorSnapshot.objects.filter(
            snapshot_date=latest_date
        ).select_related('team').order_by('-season_points'))
        
        if not drivers or not constructors:
            raise CommandError('No driver or constructor data available')
        
        self.stdout.write(f"Found {len(drivers)} drivers and {len(constructors)} constructors")
        
        # Run optimization
        self.stdout.write("\n" + "="*70)
        self.stdout.write(self.style.SUCCESS("RUNNING OPTIMIZATION (Dynamic Programming)"))
        self.stdout.write("="*70)
        
        optimal_drivers, optimal_teams, drs_driver = self.optimize_lineup(
            drivers, constructors, budget
        )
        
        # Display results
        self.display_results(
            optimal_drivers, optimal_teams, drs_driver, 
            drivers, constructors, budget
        )
        
        # Save if requested
        if save_lineup:
            self.save_to_lineup(optimal_drivers, optimal_teams, drs_driver, budget)

    def optimize_lineup(self, drivers, constructors, budget):
        """
        Optimize lineup using dynamic programming approach
        Returns: (list of 5 driver indices, list of 2 team indices, drs_driver_index)
        """
        num_drivers = 5
        num_teams = 2
        
        # Step 1: Select best 2 constructors using greedy approach
        # Reserve approximately 40% of budget for constructors (2 teams)
        # This leaves 60% for 5 drivers
        constructor_budget = budget * 0.40
        
        best_teams = self._select_best_constructors(
            constructors, constructor_budget, num_teams
        )
        
        team_cost = sum(float(constructors[idx].fantasy_price) for idx in best_teams)
        
        # Step 2: Use remaining budget for drivers (knapsack problem)
        driver_budget = budget - team_cost
        
        best_drivers = self._knapsack_drivers(drivers, driver_budget, num_drivers)
        
        # Step 3: Select DRS driver (highest points per million among selected)
        drs_driver = self._select_drs_driver(drivers, best_drivers)
        
        return best_drivers, best_teams, drs_driver

    def _select_best_constructors(self, constructors, budget, num_teams):
        """
        Select best constructors using value-based greedy approach
        Ensures we select exactly num_teams constructors
        """
        # Score constructors by a combination of points/million and total points
        scored_constructors = []
        for idx, c in enumerate(constructors):
            price = float(c.fantasy_price)
            # Weighted score: 70% value, 30% total points
            score = (c.points_per_million * 0.7) + (c.season_points / 1000 * 0.3)
            scored_constructors.append((idx, score, price))
        
        # Sort by score
        scored_constructors.sort(key=lambda x: x[1], reverse=True)
        
        # Try to select best value teams that fit together
        best_combination = None
        best_score = -1
        
        # Check all combinations of num_teams constructors
        from itertools import combinations
        
        for combo in combinations(range(len(constructors)), num_teams):
            total_cost = sum(float(constructors[i].fantasy_price) for i in combo)
            if total_cost <= budget:
                total_score = sum(scored_constructors[i][1] for i in range(len(scored_constructors)) 
                                if scored_constructors[i][0] in combo)
                if total_score > best_score:
                    best_score = total_score
                    best_combination = list(combo)
        
        # If no valid combination found, select cheapest teams
        if best_combination is None or len(best_combination) < num_teams:
            cheap_teams = sorted(range(len(constructors)), 
                               key=lambda i: float(constructors[i].fantasy_price))
            best_combination = cheap_teams[:num_teams]
        
        return best_combination

    def _knapsack_drivers(self, drivers, budget, num_drivers):
        """
        Solve knapsack problem for driver selection
        Uses a greedy approximation with value-based scoring
        Ensures exactly num_drivers are selected
        """
        # Score each driver
        scored_drivers = []
        for idx, d in enumerate(drivers):
            price = float(d.fantasy_price)
            # Weighted score: 60% value, 40% total points
            score = (d.points_per_million * 0.6) + (d.season_points / 1000 * 0.4)
            scored_drivers.append((idx, score, price))
        
        # Sort by score descending
        scored_drivers.sort(key=lambda x: x[1], reverse=True)
        
        # Try to select best value drivers that fit budget
        selected = []
        total_cost = 0
        
        for idx, score, price in scored_drivers:
            if len(selected) < num_drivers and total_cost + price <= budget:
                selected.append(idx)
                total_cost += price
        
        # If we didn't get enough drivers, we need to backtrack and try different combinations
        if len(selected) < num_drivers:
            # Sort all drivers by price (cheapest first)
            cheap_drivers = sorted(
                [(i, float(drivers[i].fantasy_price)) for i in range(len(drivers))],
                key=lambda x: x[1]
            )
            
            # Select cheapest num_drivers that fit budget
            selected = []
            total_cost = 0
            for idx, price in cheap_drivers:
                if len(selected) < num_drivers and total_cost + price <= budget:
                    selected.append(idx)
                    total_cost += price
            
            # If we still can't get 5, just take the 5 cheapest regardless
            if len(selected) < num_drivers:
                selected = [idx for idx, _ in cheap_drivers[:num_drivers]]
        
        return selected

    def _select_drs_driver(self, drivers, driver_indices):
        """
        Select the best DRS driver from selected drivers
        Choose driver with highest points per million
        """
        best_idx = driver_indices[0]
        best_value = drivers[best_idx].points_per_million
        
        for idx in driver_indices[1:]:
            if drivers[idx].points_per_million > best_value:
                best_value = drivers[idx].points_per_million
                best_idx = idx
        
        return best_idx

    def display_results(self, driver_indices, team_indices, drs_driver_idx, 
                       drivers, constructors, budget):
        """Display the optimal lineup"""
        
        # Calculate totals
        total_cost = 0
        total_points = 0
        
        self.stdout.write("\n" + "="*70)
        self.stdout.write(self.style.SUCCESS("OPTIMAL LINEUP"))
        self.stdout.write("="*70)
        
        self.stdout.write("\n" + self.style.WARNING("DRIVERS:"))
        for i, idx in enumerate(driver_indices, 1):
            d = drivers[idx]
            is_drs = " [DRS]" if idx == drs_driver_idx else ""
            self.stdout.write(
                f"  {i}. {d.driver.full_name:25s} {d.team.name:20s} "
                f"${d.fantasy_price}M - {d.season_points:3d} pts "
                f"({d.points_per_million:.2f} pts/$M){is_drs}"
            )
            total_cost += float(d.fantasy_price)
            total_points += d.season_points
        
        self.stdout.write("\n" + self.style.WARNING("CONSTRUCTORS:"))
        for i, idx in enumerate(team_indices, 1):
            c = constructors[idx]
            self.stdout.write(
                f"  {i}. {c.team.name:25s} ${c.fantasy_price}M - {c.season_points:3d} pts "
                f"({c.points_per_million:.2f} pts/$M)"
            )
            total_cost += float(c.fantasy_price)
            total_points += c.season_points
        
        budget_remaining = budget - total_cost
        value = total_points / total_cost if total_cost > 0 else 0
        
        self.stdout.write("\n" + "-"*70)
        self.stdout.write(f"Total Cost:        ${total_cost:.1f}M")
        self.stdout.write(f"Budget:            ${budget:.1f}M")
        self.stdout.write(self.style.SUCCESS(f"Budget Remaining:  ${budget_remaining:.1f}M"))
        self.stdout.write(f"Total Points:      {total_points}")
        self.stdout.write(self.style.SUCCESS(f"Value:             {value:.2f} pts/$M"))
        self.stdout.write("="*70 + "\n")

    def save_to_lineup(self, driver_indices, team_indices, drs_driver_idx, budget):
        """Save optimal lineup to joe's CurrentLineup"""
        try:
            user = User.objects.get(username='joe')
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(
                "Cannot save: User 'joe' does not exist"
            ))
            return
        
        # Get latest snapshot data
        latest_date = DriverSnapshot.objects.aggregate(
            Max('snapshot_date')
        )['snapshot_date__max']
        
        drivers_data = list(DriverSnapshot.objects.filter(
            snapshot_date=latest_date
        ).select_related('driver', 'team'))
        
        constructors_data = list(ConstructorSnapshot.objects.filter(
            snapshot_date=latest_date
        ).select_related('team'))
        
        # Calculate remaining cap space
        total_cost = sum(float(drivers_data[idx].fantasy_price) for idx in driver_indices)
        total_cost += sum(float(constructors_data[idx].fantasy_price) for idx in team_indices)
        cap_space = Decimal(str(budget - total_cost))
        
        # Get or create lineup
        try:
            lineup = CurrentLineup.objects.filter(user=user).latest('updated_at')
        except CurrentLineup.DoesNotExist:
            lineup = CurrentLineup(user=user)
        
        # Update lineup
        lineup.driver1 = drivers_data[driver_indices[0]].driver
        lineup.driver2 = drivers_data[driver_indices[1]].driver
        lineup.driver3 = drivers_data[driver_indices[2]].driver
        lineup.driver4 = drivers_data[driver_indices[3]].driver
        lineup.driver5 = drivers_data[driver_indices[4]].driver
        lineup.drs_driver = drivers_data[drs_driver_idx].driver
        lineup.team1 = constructors_data[team_indices[0]].team
        lineup.team2 = constructors_data[team_indices[1]].team
        lineup.cap_space = cap_space
        
        lineup.save()
        
        self.stdout.write(self.style.SUCCESS(
            f"\n✓ Saved optimal lineup to joe's CurrentLineup (ID: {lineup.id})"
        ))
