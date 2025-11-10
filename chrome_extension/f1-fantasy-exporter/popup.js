document.addEventListener('DOMContentLoaded', function() {
  const exportDriversBtn = document.getElementById('exportDrivers');
  const exportConstructorsBtn = document.getElementById('exportConstructors');
  const exportDriverPerformanceBtn = document.getElementById('exportDriverPerformance');
  const statusDiv = document.getElementById('status');

  function showStatus(message, type) {
    statusDiv.textContent = message;
    statusDiv.className = `status ${type}`;
    setTimeout(() => {
      statusDiv.style.display = 'none';
    }, 3000);
  }

  function getDatePrefix() {
    const now = new Date();
    const year = now.getFullYear();
    const month = String(now.getMonth() + 1).padStart(2, '0');
    const day = String(now.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
  }

  function getDateFilename(suffix) {
    return `${getDatePrefix()}-${suffix}.csv`;
  }

  exportDriversBtn.addEventListener('click', async () => {
    try {
      exportDriversBtn.disabled = true;
      showStatus('Extracting driver data...', 'info');

      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      
      // Function to attempt extraction
      const attemptExtraction = (retryCount = 0) => {
        chrome.scripting.executeScript({
          target: { tabId: tab.id },
          function: extractDriverData
        }, (results) => {
          if (chrome.runtime.lastError) {
            showStatus('Error: ' + chrome.runtime.lastError.message, 'error');
            exportDriversBtn.disabled = false;
            return;
          }

          if (results && results[0] && results[0].result) {
            const result = results[0].result;
            
            // Check if we need to retry (tab was switched)
            if (result.needsRetry && retryCount < 3) {
              showStatus(result.message || 'Switching tabs...', 'info');
              setTimeout(() => attemptExtraction(retryCount + 1), 1000);
              return;
            }
            
            const drivers = Array.isArray(result) ? result : [];
            if (drivers.length === 0) {
              showStatus('No driver data found on this page', 'error');
            } else {
              downloadCSV(drivers, getDateFilename('drivers'));
              showStatus(`Exported ${drivers.length} drivers!`, 'success');
            }
          } else {
            showStatus('No data found', 'error');
          }
          exportDriversBtn.disabled = false;
        });
      };
      
      attemptExtraction();
    } catch (error) {
      showStatus('Error: ' + error.message, 'error');
      exportDriversBtn.disabled = false;
    }
  });

  exportConstructorsBtn.addEventListener('click', async () => {
    try {
      exportConstructorsBtn.disabled = true;
      showStatus('Extracting constructor data...', 'info');

      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      
      // Function to attempt extraction
      const attemptExtraction = (retryCount = 0) => {
        chrome.scripting.executeScript({
          target: { tabId: tab.id },
          function: extractConstructorData
        }, (results) => {
          if (chrome.runtime.lastError) {
            showStatus('Error: ' + chrome.runtime.lastError.message, 'error');
            exportConstructorsBtn.disabled = false;
            return;
          }

          if (results && results[0] && results[0].result) {
            const result = results[0].result;
            
            // Check if we need to retry (tab was switched)
            if (result.needsRetry && retryCount < 3) {
              showStatus(result.message || 'Switching tabs...', 'info');
              setTimeout(() => attemptExtraction(retryCount + 1), 1000);
              return;
            }
            
            const constructors = Array.isArray(result) ? result : [];
            if (constructors.length === 0) {
              showStatus('No constructor data found', 'error');
            } else {
              downloadCSV(constructors, getDateFilename('constructors'));
              showStatus(`Exported ${constructors.length} constructors!`, 'success');
            }
          } else {
            showStatus('No data found', 'error');
          }
          exportConstructorsBtn.disabled = false;
        });
      };
      
      attemptExtraction();
    } catch (error) {
      showStatus('Error: ' + error.message, 'error');
      exportConstructorsBtn.disabled = false;
    }
  });

  exportDriverPerformanceBtn.addEventListener('click', async () => {
    try {
      exportDriverPerformanceBtn.disabled = true;
      showStatus('Starting automated export (drivers + constructors)...', 'info');

      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      
      // Step 1: Export drivers
      showStatus('Exporting driver performance...', 'info');
      chrome.scripting.executeScript({
        target: { tabId: tab.id },
        function: automateDriverPerformanceExport
      }, (driverResults) => {
        if (chrome.runtime.lastError) {
          showStatus('Error: ' + chrome.runtime.lastError.message, 'error');
          exportDriverPerformanceBtn.disabled = false;
          return;
        }

        // Process driver results
        let driverCount = 0;
        let driverRecordCount = 0;
        
        if (driverResults && driverResults[0] && driverResults[0].result) {
          const allDriverResults = driverResults[0].result;
          
          if (allDriverResults && allDriverResults.length > 0) {
            const combinedDriverData = [];
            
            allDriverResults.forEach(result => {
              if (result && result.data && result.data.length > 0) {
                combinedDriverData.push(...result.data);
                driverCount++;
              }
            });
            
            if (combinedDriverData.length > 0) {
              const filename = `${getDatePrefix()}-all-drivers-performance.csv`;
              downloadCSV(combinedDriverData, filename);
              driverRecordCount = combinedDriverData.length;
            }
          }
        }
        
        // Step 2: Export constructors
        showStatus('Exporting constructor performance...', 'info');
        chrome.scripting.executeScript({
          target: { tabId: tab.id },
          function: automateConstructorPerformanceExport
        }, (constructorResults) => {
          if (chrome.runtime.lastError) {
            showStatus(`Drivers exported (${driverCount}). Constructor error: ` + chrome.runtime.lastError.message, 'error');
            exportDriverPerformanceBtn.disabled = false;
            return;
          }

          // Process constructor results
          let constructorCount = 0;
          let constructorRecordCount = 0;
          
          if (constructorResults && constructorResults[0] && constructorResults[0].result) {
            const allConstructorResults = constructorResults[0].result;
            
            if (allConstructorResults && allConstructorResults.length > 0) {
              const combinedConstructorData = [];
              
              allConstructorResults.forEach(result => {
                if (result && result.data && result.data.length > 0) {
                  combinedConstructorData.push(...result.data);
                  constructorCount++;
                }
              });
              
              if (combinedConstructorData.length > 0) {
                const filename = `${getDatePrefix()}-all-constructors-performance.csv`;
                downloadCSV(combinedConstructorData, filename);
                constructorRecordCount = combinedConstructorData.length;
              }
            }
          }
          
          // Final status
          showStatus(
            `✓ Exported ${driverCount} drivers (${driverRecordCount} records) + ${constructorCount} constructors (${constructorRecordCount} records)!`,
            'success'
          );
          exportDriverPerformanceBtn.disabled = false;
        });
      });
    } catch (error) {
      showStatus('Error: ' + error.message, 'error');
      exportDriverPerformanceBtn.disabled = false;
    }
  });

  function downloadCSV(data, filename) {
    if (data.length === 0) return;

    // Define explicit column order for performance data
    let headers;
    if (filename.includes('drivers-performance')) {
      headers = [
        'Driver Name',
        'Team',
        'Driver Value',
        'Race',
        'Event Type',
        'Scoring Item',
        'Frequency',
        'Position',
        'Points',
        'Race Total',
        'Season Total'
      ];
    } else if (filename.includes('constructors-performance')) {
      headers = [
        'Constructor Name',
        'Constructor Value',
        'Race',
        'Event Type',
        'Scoring Item',
        'Frequency',
        'Position',
        'Points',
        'Race Total',
        'Season Total'
      ];
    } else {
      // For other exports, use keys from first object
      headers = Object.keys(data[0]);
    }
    
    // Create CSV content
    let csv = headers.join(',') + '\n';
    
    data.forEach(row => {
      const values = headers.map(header => {
        const value = row[header] || '';
        // Escape values that contain commas or quotes
        if (value.toString().includes(',') || value.toString().includes('"')) {
          return '"' + value.toString().replace(/"/g, '""') + '"';
        }
        return value;
      });
      csv += values.join(',') + '\n';
    });

    // Create download link
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }
});

// This function will be injected into the page
function extractDriverData() {
  // Check if we're on the Drivers tab, if not, click it
  const driversTab = document.querySelector('a[aria-label="Drivers"]');
  const driversTabActive = driversTab && driversTab.classList.contains('si-active');
  
  if (!driversTabActive && driversTab) {
    // Click the drivers tab
    driversTab.click();
    // Wait a moment for the page to update
    // Return empty array with a flag to retry
    return { needsRetry: true, message: 'Switching to Drivers tab...' };
  }

  const drivers = [];
  const driverCards = document.querySelectorAll('.si-playerListCard');

  driverCards.forEach(card => {
    const driver = {};

    // Extract driver name from alt text
    const img = card.querySelector('img[alt]');
    if (img) {
      driver['Driver Name'] = img.alt;

      // Extract team from URL - only drivers have team paths
      const teamMatch = img.src.match(/\/f1\/2025\/([^/]+)\//);
      if (teamMatch) {
        const teamMap = {
          'mclaren': 'McLaren',
          'redbullracing': 'Red Bull Racing',
          'mercedes': 'Mercedes',
          'ferrari': 'Ferrari',
          'alpine': 'Alpine',
          'astonmartin': 'Aston Martin',
          'williams': 'Williams',
          'haas': 'Haas',
          'rb': 'RB',
          'racingbulls': 'RB',
          'sauber': 'Sauber',
          'kicksauber': 'Sauber'
        };
        driver['Team'] = teamMap[teamMatch[1]] || teamMatch[1];
      }
    }

    // Extract stats (percentage picked and season points)
    const stats = card.querySelectorAll('.si-player__stats-nums');
    if (stats.length >= 2) {
      // First stat is % Picked
      const pickedText = stats[0].textContent.trim();
      const pickedMatch = pickedText.match(/(\d+\.\d+)%/);
      if (pickedMatch) {
        driver['% Picked'] = pickedMatch[1];
      }

      // Second stat is Season Points
      const ptsText = stats[1].textContent.trim();
      const ptsMatch = ptsText.match(/(\d+)\s*Pts/);
      if (ptsMatch) {
        driver['Season Points'] = ptsMatch[1];
      }
    }

    // Extract current value and price change
    const trendsDiv = card.querySelector('.si-player__trends');
    if (trendsDiv) {
      // Get current value
      const valueSpan = trendsDiv.querySelector('.si-bgTxt');
      if (valueSpan) {
        driver['Current Value'] = valueSpan.textContent.trim();
      }

      // Get price change amount
      const ariaLabel = trendsDiv.getAttribute('aria-label');
      if (ariaLabel) {
        // Check if price went down and make it negative
        if (trendsDiv.classList.contains('si-down')) {
          driver['Price Change'] = '-' + ariaLabel;
        } else {
          driver['Price Change'] = ariaLabel;
        }
      } else {
        driver['Price Change'] = '$0.0M';
      }
    }

    if (driver['Driver Name']) {
      drivers.push(driver);
    }
  });

  return drivers;
}

// This function will be injected into the page
function extractConstructorData() {
  // Check if we're on the Constructors tab, if not, click it
  const constructorsTab = document.querySelector('a[aria-label="Constructors"]');
  const constructorsTabActive = constructorsTab && constructorsTab.classList.contains('si-active');
  
  if (!constructorsTabActive && constructorsTab) {
    // Click the constructors tab
    constructorsTab.click();
    // Return empty array with a flag to retry
    return { needsRetry: true, message: 'Switching to Constructors tab...' };
  }

  const constructors = [];
  const constructorCards = document.querySelectorAll('.si-playerListCard');

  constructorCards.forEach(card => {
    const constructor = {};

    // Extract constructor name from alt text
    const img = card.querySelector('img[alt]');
    if (img) {
      constructor['Constructor Name'] = img.alt;
    }

    // Extract stats (percentage picked and season points)
    const stats = card.querySelectorAll('.si-player__stats-nums');
    if (stats.length >= 2) {
      // First stat is % Picked
      const pickedText = stats[0].textContent.trim();
      const pickedMatch = pickedText.match(/(\d+\.\d+)%/);
      if (pickedMatch) {
        constructor['% Picked'] = pickedMatch[1];
      }

      // Second stat is Season Points
      const ptsText = stats[1].textContent.trim();
      const ptsMatch = ptsText.match(/(\d+)\s*Pts/);
      if (ptsMatch) {
        constructor['Season Points'] = ptsMatch[1];
      }
    }

    // Extract current value and price change
    const trendsDiv = card.querySelector('.si-player__trends');
    if (trendsDiv) {
      // Get current value
      const valueSpan = trendsDiv.querySelector('.si-bgTxt');
      if (valueSpan) {
        constructor['Current Value'] = valueSpan.textContent.trim();
      }

      // Get price change amount
      const ariaLabel = trendsDiv.getAttribute('aria-label');
      if (ariaLabel) {
        // Check if price went down and make it negative
        if (trendsDiv.classList.contains('si-down')) {
          constructor['Price Change'] = '-' + ariaLabel;
        } else {
          constructor['Price Change'] = ariaLabel;
        }
      } else {
        constructor['Price Change'] = '$0.0M';
      }
    }

    if (constructor['Constructor Name']) {
      constructors.push(constructor);
    }
  });

  return constructors;
}
