# Bug Fix - v1.1

## What Was Fixed

The original version had a bug where both the "Export Drivers" and "Export Constructors" buttons were exporting constructor data regardless of which button you clicked.

## Changes in v1.1

✅ **Auto Tab Switching**: The extension now automatically switches to the correct tab (Drivers or Constructors) when you click a button
✅ **Smart Detection**: Each button now correctly identifies which tab it needs and switches to it automatically
✅ **Better Feedback**: You'll see a status message when the extension is switching tabs

## How It Works Now

1. Click "Export Drivers" - Extension automatically switches to Drivers tab and exports
2. Click "Export Constructors" - Extension automatically switches to Constructors tab and exports

You no longer need to manually switch tabs before clicking the export buttons!

## Upgrading

To install the fixed version:

1. Go to `chrome://extensions/`
2. Remove the old F1 Fantasy Data Exporter
3. Unzip the new `f1-fantasy-exporter-fixed.zip`
4. Click "Load unpacked" and select the new folder

Or simply:
1. Replace the old extension files with the new ones
2. Go to `chrome://extensions/`
3. Click the refresh icon on the F1 Fantasy Exporter card
