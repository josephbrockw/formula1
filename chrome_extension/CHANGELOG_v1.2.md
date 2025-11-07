# Version 1.2 - Date-Based Filenames

## New Feature

✅ **Date-Based Filenames**: CSV files now use the current date in their filename

### Filename Format

Files are now named: `YYYY-MM-DD-[type].csv`

**Examples:**
- `2025-11-07-drivers.csv`
- `2025-11-07-constructors.csv`

### Benefits

- **Automatic organization**: Files are naturally sorted by date
- **No overwrites**: Each day's data gets a unique filename
- **Easy tracking**: Quickly identify when data was exported
- **Historical comparison**: Keep multiple exports from different dates

## Version History

### v1.2 (Current)
- Added date-based filenames (YYYY-MM-DD format)

### v1.1
- Fixed bug where both buttons exported constructor data
- Added automatic tab switching
- Improved status messages

### v1.0
- Initial release
- Driver data export
- Constructor data export
- CSV download functionality

## Upgrading to v1.2

To install this version:

1. Go to `chrome://extensions/`
2. Click the refresh icon ↻ on the F1 Fantasy Exporter card

OR

1. Remove the old extension
2. Unzip `f1-fantasy-exporter-v1.2.zip`
3. Load unpacked

Your next exports will automatically use the new date-based naming!
