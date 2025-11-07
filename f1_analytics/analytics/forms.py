from django import forms
from django.db.models import Max
from analytics.models import CurrentLineup, Driver, Team, DriverSnapshot, ConstructorSnapshot


class CurrentLineupForm(forms.ModelForm):
    """Form for editing current lineup"""
    
    class Meta:
        model = CurrentLineup
        fields = ['driver1', 'driver2', 'driver3', 'driver4', 'driver5', 'drs_driver', 'team1', 'team2', 'cap_space']
        widgets = {
            'driver1': forms.Select(attrs={'class': 'mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-red-500 focus:ring-red-500'}),
            'driver2': forms.Select(attrs={'class': 'mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-red-500 focus:ring-red-500'}),
            'driver3': forms.Select(attrs={'class': 'mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-red-500 focus:ring-red-500'}),
            'driver4': forms.Select(attrs={'class': 'mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-red-500 focus:ring-red-500'}),
            'driver5': forms.Select(attrs={'class': 'mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-red-500 focus:ring-red-500'}),
            'drs_driver': forms.Select(attrs={'class': 'mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-red-500 focus:ring-red-500'}),
            'team1': forms.Select(attrs={'class': 'mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-red-500 focus:ring-red-500'}),
            'team2': forms.Select(attrs={'class': 'mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-red-500 focus:ring-red-500'}),
            'cap_space': forms.NumberInput(attrs={'class': 'mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-red-500 focus:ring-red-500', 'step': '0.1'}),
        }
        labels = {
            'driver1': 'Driver 1',
            'driver2': 'Driver 2',
            'driver3': 'Driver 3',
            'driver4': 'Driver 4',
            'driver5': 'Driver 5',
            'drs_driver': 'DRS Driver (must be one of the 5 drivers)',
            'team1': 'Constructor 1',
            'team2': 'Constructor 2',
            'cap_space': 'Remaining Cap Space (M)',
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Get the most recent snapshot date to show current prices
        latest_date = DriverSnapshot.objects.aggregate(Max('snapshot_date'))['snapshot_date__max']
        
        if latest_date:
            # Customize driver queryset to show prices
            driver_snapshots = DriverSnapshot.objects.filter(
                snapshot_date=latest_date
            ).select_related('driver').order_by('driver__full_name')
            
            driver_choices = [(snap.driver.id, f"{snap.driver.full_name} - ${snap.fantasy_price}M") 
                            for snap in driver_snapshots]
            
            for field_name in ['driver1', 'driver2', 'driver3', 'driver4', 'driver5', 'drs_driver']:
                self.fields[field_name].choices = [('', '---------')] + driver_choices
            
            # Customize team queryset to show prices
            team_snapshots = ConstructorSnapshot.objects.filter(
                snapshot_date=latest_date
            ).select_related('team').order_by('team__name')
            
            team_choices = [(snap.team.id, f"{snap.team.name} - ${snap.fantasy_price}M") 
                          for snap in team_snapshots]
            
            for field_name in ['team1', 'team2']:
                self.fields[field_name].choices = [('', '---------')] + team_choices
    
    def clean(self):
        cleaned_data = super().clean()
        
        # Validate that DRS driver is one of the 5 drivers
        drs_driver = cleaned_data.get('drs_driver')
        driver_ids = [
            cleaned_data.get('driver1'),
            cleaned_data.get('driver2'),
            cleaned_data.get('driver3'),
            cleaned_data.get('driver4'),
            cleaned_data.get('driver5'),
        ]
        
        if drs_driver and drs_driver.id not in [d.id for d in driver_ids if d]:
            raise forms.ValidationError('DRS driver must be one of the 5 selected drivers.')
        
        # Validate no duplicate drivers
        driver_ids_list = [d.id for d in driver_ids if d]
        if len(driver_ids_list) != len(set(driver_ids_list)):
            raise forms.ValidationError('You cannot select the same driver multiple times.')
        
        # Validate no duplicate teams
        team1 = cleaned_data.get('team1')
        team2 = cleaned_data.get('team2')
        if team1 and team2 and team1.id == team2.id:
            raise forms.ValidationError('You cannot select the same constructor twice.')
        
        return cleaned_data
