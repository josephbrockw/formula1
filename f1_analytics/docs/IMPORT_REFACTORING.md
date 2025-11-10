# Import Command Refactoring

## Overview

Extracted common logic from `import_driver_performance.py` and `import_constructor_performance.py` into a shared utilities module to improve code maintainability and reduce duplication.

## Shared Utilities Module

**File:** `analytics/management/commands/_performance_import_utils.py`

### Functions Extracted

#### 1. **`find_most_recent_file(data_dir, pattern)`**
- Finds most recent file matching a glob pattern
- Used by both commands to auto-discover CSV files
- **Before:** Duplicated in both commands (~10 lines each)
- **After:** Single implementation (10 lines)

#### 2. **`get_season(year)`**
- Retrieves Season object for given year
- Handles Season.DoesNotExist error consistently
- **Before:** Try-except block duplicated (~7 lines each)
- **After:** Single implementation (12 lines)

#### 3. **`resolve_csv_file(options, year, file_pattern, data_subdir)`**
- Resolves CSV file from options or finds most recent
- Handles file validation and error messages
- **Before:** Duplicated logic (~16 lines each)
- **After:** Single implementation (22 lines)

#### 4. **`get_or_create_race(season, race_name, race_order)`**
- Creates/retrieves Race with proper round number tracking
- Handles race ordering logic
- **Before:** Duplicated logic (~9 lines each)
- **After:** Single implementation (15 lines)

#### 5. **`parse_fantasy_price(price_string)`**
- Parses fantasy price strings ('$30.4M' → Decimal)
- Strips currency symbols
- **Before:** Duplicated parsing (~2 lines each)
- **After:** Single implementation (4 lines)

#### 6. **`parse_event_score_fields(row)`**
- Parses points, position, frequency from CSV row
- Handles empty values with proper defaults
- **Before:** Duplicated parsing (~3 lines each)
- **After:** Single implementation (8 lines)

#### 7. **`extract_event_types(rows)`**
- Extracts set of event types from row list
- Used for event participation flags
- **Before:** Duplicated (~1 line each)
- **After:** Single implementation (3 lines)

#### 8. **`get_or_create_team(team_name)`**
- Creates/retrieves Team with short name
- **Before:** Duplicated (~3 lines each)
- **After:** Single implementation (5 lines)

#### 9. **`parse_totals(race_total_str, season_total_str)`**
- Parses race and season totals to integers
- Handles empty values
- **Before:** Duplicated (~2 lines each)
- **After:** Single implementation (5 lines)

---

## Code Reduction Summary

### Before Refactoring
- **`import_driver_performance.py`**: 220 lines
- **`import_constructor_performance.py`**: 220 lines
- **Total**: 440 lines
- **Duplicated logic**: ~50 lines per file = 100 lines total

### After Refactoring
- **`import_driver_performance.py`**: 172 lines (-48 lines)
- **`import_constructor_performance.py`**: 161 lines (-59 lines)
- **`_performance_import_utils.py`**: 174 lines (new)
- **Total**: 507 lines
- **Net change**: +67 lines overall, but:
  - Eliminated 100 lines of duplication
  - Added 167 lines of well-documented utilities
  - Improved maintainability significantly

---

## Benefits

### 1. **DRY Principle** (Don't Repeat Yourself)
- Single source of truth for common logic
- Bug fixes apply to both commands automatically
- Consistent behavior between driver and constructor imports

### 2. **Maintainability**
- Changes to parsing logic only need to be made once
- Easier to understand command-specific logic
- Clear separation between generic utilities and domain-specific code

### 3. **Testability**
- Utility functions can be unit tested independently
- Easier to mock for command testing
- Clear function boundaries

### 4. **Readability**
- Commands are more concise and focused
- Intent is clearer with descriptive function names
- Comments concentrated in utility module

### 5. **Extensibility**
- Easy to add new performance import types (e.g., team performance)
- Utilities can be reused by future import commands
- Consistent patterns established

---

## Import Structure

```
analytics/management/commands/
├── _performance_import_utils.py    # Shared utilities
├── import_driver_performance.py    # Driver-specific logic
├── import_constructor_performance.py  # Constructor-specific logic
├── import_fantasy_prices.py        # Price snapshots (separate)
└── ...
```

### What's Shared?
- File discovery and validation
- Season retrieval
- Race creation with round numbers
- Data parsing (prices, totals, scores)
- Team creation

### What's Command-Specific?
- Model imports (Driver vs. Team)
- CSV column names ('Driver Name' vs. 'Constructor Name')
- Performance model creation (DriverRacePerformance vs. ConstructorRacePerformance)
- Event score model creation (DriverEventScore vs. ConstructorEventScore)

---

## Usage (No Changes)

Both commands work exactly as before:

```bash
# Driver performance import
python manage.py import_driver_performance
python manage.py import_driver_performance --file path/to/file.csv
python manage.py import_driver_performance --year 2024

# Constructor performance import
python manage.py import_constructor_performance
python manage.py import_constructor_performance --file path/to/file.csv
python manage.py import_constructor_performance --year 2024
```

---

## Testing Checklist

- [x] Driver import works with auto-discovery
- [x] Constructor import works with auto-discovery
- [x] Driver import works with explicit file
- [x] Constructor import works with explicit file
- [x] Proper error messages for missing season
- [x] Proper error messages for missing files
- [x] Race ordering maintained correctly
- [x] Team creation consistent
- [x] Event score parsing correct

---

## Future Enhancements

### Potential Additional Utilities
1. **`group_rows_by_entity_race(rows, entity_key, race_key)`**
   - Generic row grouping logic
   - Currently duplicated in both commands

2. **`create_performance_record(model, entity, race, data)`**
   - Generic performance creation
   - Abstract over Driver vs. Constructor differences

3. **`bulk_create_event_scores(model, performance, rows)`**
   - Bulk insert optimization
   - Reduce database queries

### Possible New Commands
- `import_team_performance` - Team-wide statistics
- `import_historical_prices` - Batch historical price imports
- `import_race_results` - Official F1 results correlation

---

## Conclusion

The refactoring successfully reduces code duplication while maintaining full backward compatibility. The codebase is now easier to maintain, test, and extend. All existing functionality works exactly as before, with the added benefit of a clean, reusable utilities module.
