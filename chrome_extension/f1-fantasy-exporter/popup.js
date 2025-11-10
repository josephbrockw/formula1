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
      showStatus('Extracting driver performance data...', 'info');

      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      
      chrome.scripting.executeScript({
        target: { tabId: tab.id },
        function: extractDriverPerformanceData
      }, (results) => {
        if (chrome.runtime.lastError) {
          showStatus('Error: ' + chrome.runtime.lastError.message, 'error');
          exportDriverPerformanceBtn.disabled = false;
          return;
        }

        if (results && results[0] && results[0].result) {
          const result = results[0].result;
          
          if (!result || !result.data || result.data.length === 0) {
            showStatus('No performance data found. Make sure you are on a driver detail page.', 'error');
          } else {
            // Use driver name and team in filename
            const driverSlug = result.driverName
              .toLowerCase()
              .replace(/\s+/g, '-')
              .replace(/[^a-z0-9-]/g, '');
            const teamSlug = result.teamName
              .toLowerCase()
              .replace(/\s+/g, '-')
              .replace(/[^a-z0-9-]/g, '');
            const filename = `${getDatePrefix()}-${driverSlug}-${teamSlug}-performance.csv`;
            downloadCSV(result.data, filename);
            showStatus(`Exported ${result.data.length} performance records for ${result.driverName} (${result.teamName})!`, 'success');
          }
        } else {
          showStatus('No data found', 'error');
        }
        exportDriverPerformanceBtn.disabled = false;
      });
    } catch (error) {
      showStatus('Error: ' + error.message, 'error');
      exportDriverPerformanceBtn.disabled = false;
    }
  });

  function downloadCSV(data, filename) {
    if (data.length === 0) return;

    // Define explicit column order for driver performance data
    let headers;
    if (filename.includes('performance')) {
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

// This function will be injected into the page to extract driver performance data
function extractDriverPerformanceData() {
  const performanceData = [];
  
  // Get driver info from the popup
  const driverNameSpans = document.querySelectorAll('.si-player__name span');
  let driverName = '';
  if (driverNameSpans.length >= 2) {
    const firstName = driverNameSpans[0].textContent.trim();
    const lastName = driverNameSpans[1].textContent.trim();
    // Capitalize first letter of each name
    driverName = firstName.charAt(0).toUpperCase() + firstName.slice(1).toLowerCase() + ' ' + 
                 lastName.charAt(0).toUpperCase() + lastName.slice(1).toLowerCase();
  }
  
  // Return early if no driver name found
  if (!driverName) {
    return { driverName: 'Unknown', teamName: 'Unknown', data: [] };
  }
  
  // Get team name from the driver card image
  let teamName = 'Unknown';
  const driverImage = document.querySelector('.si-player__card-thumbnail img');
  if (driverImage && driverImage.src) {
    const teamMatch = driverImage.src.match(/\/f1\/2025\/([^/]+)\//);
    if (teamMatch) {
      const teamSlug = teamMatch[1];
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
      teamName = teamMap[teamSlug] || teamSlug;
    }
  }
  
  // Get driver value
  const driverValueElement = document.querySelector('.si-player__trends span');
  const driverValue = driverValueElement ? driverValueElement.textContent.trim() : '';
  
  // Get season total
  const seasonTotalElement = document.querySelector('.si-driCon__list li[aria-label*="Season Points"] .si-driCon__list-stats span');
  const seasonTotal = seasonTotalElement ? seasonTotalElement.textContent.trim().replace(/\s*Pts.*/, '') : '';
  
  // Get all race accordion items
  const accordions = document.querySelectorAll('.si-accordion__box');
  
  accordions.forEach(accordion => {
    // Get race name from the title
    const raceTitle = accordion.querySelector('.si-league__card-title span');
    const raceName = raceTitle ? raceTitle.textContent.trim() : '';
    
    // Skip the "Season" accordion
    if (raceName === 'Season') {
      return;
    }
    
    // Get total points for this race
    const raceTotalElement = accordion.querySelector('.si-totalPts__counts span em');
    const raceTotal = raceTotalElement ? raceTotalElement.textContent.trim() : '';
    
    // Get the performance table
    const tbody = accordion.querySelector('.si-performance__tbl tbody');
    
    if (tbody) {
      const rows = tbody.querySelectorAll('tr');
      
      rows.forEach(row => {
        const cells = row.querySelectorAll('td');
        
        if (cells.length >= 3) {
          const eventDescription = cells[0].textContent.trim();
          const frequencyText = cells[1].textContent.trim();
          const pointsText = cells[2].textContent.trim();
          
          // Extract just the number from points (could be positive or negative)
          const pointsMatch = pointsText.match(/(-?\d+)/);
          const points = pointsMatch ? pointsMatch[1] : '0';
          
          // Determine event type based on description
          let eventType = 'race'; // default
          if (eventDescription.toLowerCase().includes('qualifying')) {
            eventType = 'qualifying';
          } else if (eventDescription.toLowerCase().includes('sprint')) {
            eventType = 'sprint';
          } else if (eventDescription.toLowerCase().includes('weekend')) {
            eventType = 'weekend';
          }
          
          // Parse frequency/position
          let position = '';
          let frequency = '';
          
          if (frequencyText && frequencyText !== '-') {
            // Check if it's a position (1st, 2nd, 3rd, etc.)
            const positionMatch = frequencyText.match(/^(\d+)(st|nd|rd|th)$/i);
            if (positionMatch) {
              position = positionMatch[1]; // Extract just the number
            } else {
              // Check if it's a plain integer
              const intMatch = frequencyText.match(/^\d+$/);
              if (intMatch) {
                frequency = frequencyText;
              }
            }
          }
          
          performanceData.push({
            'Driver Name': driverName,
            'Team': teamName,
            'Driver Value': driverValue,
            'Race': raceName,
            'Event Type': eventType,
            'Scoring Item': eventDescription,
            'Frequency': frequency,
            'Position': position,
            'Points': points,
            'Race Total': raceTotal,
            'Season Total': seasonTotal
          });
        }
      });
    }
  });
  
  // Return object with driver name, team name, and data array
  return {
    driverName: driverName,
    teamName: teamName,
    data: performanceData
  };
}
