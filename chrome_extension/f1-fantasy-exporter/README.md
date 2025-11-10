# F1 Fantasy Data Exporter

A Chrome extension that exports driver and constructor data from the F1 Fantasy game to CSV files.

## Features

- **Export Drivers** - Export all driver data including:
  - Driver name
  - Team
  - % Picked (ownership percentage)
  - Season Points
  - Current Value
  - Price Change (negative for decreases, positive for increases)

- **Export Constructors** - Export all constructor data with the same fields

- **Export Performance Data** (NEW in v1.3) - Automated batch export of detailed race-by-race performance data:
  - **Fully automated** - Click one button to export both drivers and constructors
  - Automatically clicks through each driver card, then each constructor card
  - Event-level breakdown (Qualifying, Sprint, Race)
  - Points by event type
  - Overtakes, positions gained/lost
  - Fastest lap and Driver of the Day bonuses
  - Team tracking for drivers (handles mid-season transfers)
  - Two separate CSV files: one for drivers, one for constructors

## Installation

### Method 1: Load as Unpacked Extension (For Development/Testing)

1. Download or clone this extension folder to your computer

2. Open Google Chrome and navigate to `chrome://extensions/`

3. Enable "Developer mode" by toggling the switch in the top-right corner

4. Click "Load unpacked" button

5. Select the `f1-fantasy-exporter` folder

6. The extension should now appear in your extensions list

### Method 2: Pack and Install

1. In `chrome://extensions/`, click "Pack extension"

2. Select the `f1-fantasy-exporter` folder as the extension root directory

3. Click "Pack Extension" (leave private key field empty for first time)

4. This will create a `.crx` file that you can share or install on other Chrome browsers

## Usage

### Exporting Current Prices (Drivers & Constructors)

1. Navigate to the F1 Fantasy website: https://fantasy.formula1.com/

2. Go to the page where you can see the driver or constructor list (usually in the team selection area)

3. Click the F1 Fantasy Exporter extension icon in your Chrome toolbar

4. In the popup:
   - Click "Export Drivers" to download driver data as CSV
   - Click "Export Constructors" to download constructor data as CSV (make sure you're on the Constructors tab first)

5. The CSV file will be automatically downloaded to your default downloads folder with the current date in the filename (e.g., `2025-11-07-drivers.csv`)

### Exporting Performance Data (Automated Batch Export)

1. Navigate to the **Drivers** tab on the F1 Fantasy website (the page showing all drivers in a list)

2. Click the F1 Fantasy Exporter extension icon

3. Click "Export Performance Data"

4. The extension will automatically:
   - Click through each driver card and extract their performance data
   - Switch to the Constructors tab
   - Click through each constructor card and extract their performance data
   - Download two separate CSV files
   
5. Two CSV files will be downloaded:
   - `YYYY-MM-DD-all-drivers-performance.csv` - All driver performance data
   - `YYYY-MM-DD-all-constructors-performance.csv` - All constructor performance data

**Note**: The automation process may take 2-3 minutes depending on the number of drivers and constructors. Please keep the browser tab visible during the export.

## CSV Format

Files are automatically named with the current date: `YYYY-MM-DD-[type].csv`

### Drivers CSV (e.g., `2025-11-07-drivers.csv`)
```csv
Driver Name,Team,% Picked,Season Points,Current Value,Price Change
Lando Norris,McLaren,22.00,614,$30.4M,-$0.1M
Oscar Piastri,McLaren,36.00,585,$26.0M,-$0.3M
...
```

### Constructors CSV (e.g., `2025-11-07-constructors.csv`)
```csv
Constructor Name,% Picked,Season Points,Current Value,Price Change
McLaren,45.00,1199,$32.0M,$0.5M
...
```

### Driver Performance CSV (e.g., `2025-11-07-all-drivers-performance.csv`)
```csv
Driver Name,Team,Driver Value,Race,Event Type,Scoring Item,Frequency,Position,Points,Race Total,Season Total
Lando Norris,McLaren,$30.4M,Australia,qualifying,Qualifying Position,,1,10,59,614
Lando Norris,McLaren,$30.4M,Australia,race,Race Position,,2,18,59,614
Lando Norris,McLaren,$30.4M,Australia,race,Race Positions Gained,5,,5,59,614
Oscar Piastri,McLaren,$25.7M,Australia,qualifying,Qualifying Position,,2,9,50,584
Oscar Piastri,McLaren,$25.7M,Australia,race,Race Position,,1,25,50,584
Max Verstappen,Red Bull Racing,$29.5M,Australia,qualifying,Qualifying Position,,3,8,45,618
...
```

**Column Descriptions:**
- **Driver Name**: Full driver name
- **Team**: Current team for this performance record (handles mid-season transfers)
- **Event Type**: Category (`qualifying`, `sprint`, `race`, `weekend`)
- **Scoring Item**: The specific fantasy scoring action
- **Frequency**: Count/number for frequency-based items (overtakes, positions gained/lost). Empty if not applicable.
- **Position**: Final position for position-based items (1st → 1, 2nd → 2, etc.). Empty if not applicable.

**Scoring Items** include: Qualifying Position, Race Position, Sprint Position, Race Overtake Bonus, Race Positions Gained/Lost, Fastest Lap, Driver Of The Day, etc.

### Constructor Performance CSV (e.g., `2025-11-07-all-constructors-performance.csv`)
```csv
Constructor Name,Constructor Value,Race,Event Type,Scoring Item,Frequency,Position,Points,Race Total,Season Total
McLaren,$32.0M,Australia,qualifying,Qualifying Position,,1,10,95,1199
McLaren,$32.0M,Australia,race,Race Position,,2,18,95,1199
McLaren,$32.0M,Australia,race,Pitstop Bonus,2,,2,95,1199
Ferrari,$31.5M,Australia,qualifying,Qualifying Position,,2,9,85,1134
Red Bull Racing,$30.8M,Australia,race,Race Position,,1,25,82,1156
...
```

**Column Descriptions:**
- **Constructor Name**: Team name
- **Constructor Value**: Fantasy price for the constructor
- **Event Type**: Category (`qualifying`, `sprint`, `race`, `weekend`)
- **Scoring Item**: The specific fantasy scoring action
- **Frequency**: Count/number for frequency-based items (pitstops, positions gained). Empty if not applicable.
- **Position**: Final position for position-based items. Empty if not applicable.

## Troubleshooting

**Extension doesn't work:**
- Make sure you're on the F1 Fantasy website (fantasy.formula1.com)
- Try refreshing the page after installing the extension
- Check that the driver/constructor list is visible on the page

**No data exported:**
- Ensure you're on a page that shows the driver or constructor list
- For constructors, make sure you've clicked the "Constructors" tab before exporting

**Error messages:**
- Check the browser console (F12 → Console tab) for detailed error messages
- Make sure you have the latest version of Chrome

## Development

The extension consists of:
- `manifest.json` - Extension configuration
- `popup.html` - Extension popup UI
- `popup.js` - Main popup logic and orchestration (~440 lines)
- `performance-export.js` - Driver performance export module (~370 lines)
- `constructor-performance-export.js` - Constructor performance export module (~240 lines)
- `content.js` - Content script (currently minimal)
- `styles.css` - Popup styling
- Icons (16x16, 48x48, 128x128)

**Code Organization:**
- Modular structure keeps file sizes manageable
- Separate modules for different export types
- Main popup.js orchestrates the workflow
- Each module is self-contained and focused

### Modifying the Extension

If you make changes to the extension files:
1. Go to `chrome://extensions/`
2. Click the refresh icon on the F1 Fantasy Exporter card
3. Test your changes

## Privacy

This extension:
- Only runs on fantasy.formula1.com
- Does not collect or transmit any data
- Does not access any personal information
- All data extraction happens locally in your browser

## License

MIT License - Feel free to modify and distribute

## Version History

- **1.3** - Performance Data Export (Current)
  - NEW: Automated batch export of driver AND constructor performance data
  - Automatically clicks through all drivers, then all constructors
  - Exports detailed race-by-race performance for each driver and constructor
  - Includes event-level breakdowns (Qualifying, Sprint, Race)
  - Tracks overtakes, positions gained/lost, bonuses, pitstops
  - Team field for drivers (handles mid-season driver transfers)
  - Two separate CSV files: drivers and constructors
  - Modular code structure (performance-export.js, constructor-performance-export.js)
- **1.2** - Date-based filenames (YYYY-MM-DD format)
- **1.1** - Fixed dual-export bug, added automatic tab switching
- **1.0** - Initial release
  - Driver data export
  - Constructor data export
  - CSV download functionality
