// Performance Export Module
// This file contains all logic for extracting driver performance data

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
  
  // Find all race accordions
  const accordions = document.querySelectorAll('.si-accordion__box');
  
  accordions.forEach(accordion => {
    const titleElement = accordion.querySelector('.si-league__card-title span');
    if (!titleElement) return;
    
    const raceName = titleElement.textContent.trim();
    if (raceName === 'Season') return; // Skip season summary
    
    const totalElement = accordion.querySelector('.si-totalPts__counts span em');
    const raceTotal = totalElement ? totalElement.textContent.trim() : '';
    
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

// This function automates clicking through all driver cards and collecting their performance data
async function automateDriverPerformanceExport() {
  const allDriverData = [];
  
  // Find all driver card rows (skip first one which is the header)
  const driverRows = document.querySelectorAll('.si-stats__list-grid ul li');
  
  if (driverRows.length <= 1) {
    return []; // No drivers found or only header
  }
  
  // Helper function to wait for a condition
  function waitFor(condition, timeout = 5000) {
    return new Promise((resolve, reject) => {
      const startTime = Date.now();
      const interval = setInterval(() => {
        if (condition()) {
          clearInterval(interval);
          resolve();
        } else if (Date.now() - startTime > timeout) {
          clearInterval(interval);
          reject(new Error('Timeout waiting for condition'));
        }
      }, 100);
    });
  }
  
  // Helper function to wait for modal to open
  async function waitForModal() {
    await waitFor(() => {
      const modal = document.querySelector('.si-popup__container');
      return modal && modal.offsetParent !== null;
    });
    // Wait for accordion content to be present
    await waitFor(() => {
      const accordions = document.querySelectorAll('.si-accordion__box');
      return accordions && accordions.length > 0;
    }, 3000);
    // Extra delay to ensure all content is rendered
    await new Promise(resolve => setTimeout(resolve, 800));
  }
  
  // Helper function to close modal
  function closeModal() {
    const closeButton = document.querySelector('.si-popup__close button');
    if (closeButton) {
      closeButton.click();
    }
  }
  
  // Process each driver (skip index 0 which is header)
  for (let i = 1; i < driverRows.length; i++) {
    const row = driverRows[i];
    
    // Get driver info from the list before clicking
    const nameElement = row.querySelector('.si-miniCard__name');
    const teamElement = row.querySelector('.si-stats__list-item.teamname .si-stats__list-value');
    
    if (!nameElement || !teamElement) continue;
    
    const nameSpans = nameElement.querySelectorAll('span');
    if (nameSpans.length < 2) continue;
    
    const firstName = nameSpans[0].textContent.trim();
    const lastName = nameSpans[1].textContent.trim();
    const driverName = firstName.charAt(0).toUpperCase() + firstName.slice(1).toLowerCase() + ' ' + 
                       lastName.charAt(0).toUpperCase() + lastName.slice(1).toLowerCase();
    const teamName = teamElement.textContent.trim();
    
    try {
      // Click the driver card
      const clickTarget = row.querySelector('.si-miniCard__wrap');
      if (!clickTarget) continue;
      
      clickTarget.click();
      
      // Wait for modal to open
      await waitForModal();
      
      // Extract performance data directly (inlined logic)
      const performanceData = [];
      
      // Get driver value from modal
      const driverValueElement = document.querySelector('.si-player__trends span');
      const driverValue = driverValueElement ? driverValueElement.textContent.trim() : '';
      
      // Get season total from modal
      const seasonTotalElement = document.querySelector('.si-driCon__list li[aria-label*="Season Points"] .si-driCon__list-stats span');
      const seasonTotal = seasonTotalElement ? seasonTotalElement.textContent.trim().replace(/\s*Pts.*/, '') : '';
      
      // Get team from modal image
      let modalTeamName = teamName; // Default to list team
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
          modalTeamName = teamMap[teamSlug] || teamSlug;
        }
      }
      
      // Find all race accordions
      const accordions = document.querySelectorAll('.si-accordion__box');
      
      accordions.forEach(accordion => {
        const titleElement = accordion.querySelector('.si-league__card-title span');
        if (!titleElement) return;
        
        const raceName = titleElement.textContent.trim();
        if (raceName === 'Season') return; // Skip season summary
        
        const totalElement = accordion.querySelector('.si-totalPts__counts span em');
        const raceTotal = totalElement ? totalElement.textContent.trim() : '';
        
        const tbody = accordion.querySelector('.si-performance__tbl tbody');
        
        if (tbody) {
          const rows = tbody.querySelectorAll('tr');
          
          rows.forEach(row => {
            const cells = row.querySelectorAll('td');
            
            if (cells.length >= 3) {
              const eventDescription = cells[0].textContent.trim();
              const frequencyText = cells[1].textContent.trim();
              const pointsText = cells[2].textContent.trim();
              
              const pointsMatch = pointsText.match(/(-?\d+)/);
              const points = pointsMatch ? pointsMatch[1] : '0';
              
              let eventType = 'race';
              if (eventDescription.toLowerCase().includes('qualifying')) {
                eventType = 'qualifying';
              } else if (eventDescription.toLowerCase().includes('sprint')) {
                eventType = 'sprint';
              } else if (eventDescription.toLowerCase().includes('weekend')) {
                eventType = 'weekend';
              }
              
              let position = '';
              let frequency = '';
              
              if (frequencyText && frequencyText !== '-') {
                const positionMatch = frequencyText.match(/^(\d+)(st|nd|rd|th)$/i);
                if (positionMatch) {
                  position = positionMatch[1];
                } else {
                  const intMatch = frequencyText.match(/^\d+$/);
                  if (intMatch) {
                    frequency = frequencyText;
                  }
                }
              }
              
              performanceData.push({
                'Driver Name': driverName,
                'Team': modalTeamName,
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
      
      // Add to results if we got data
      if (performanceData.length > 0) {
        allDriverData.push({
          driverName: driverName,
          teamName: modalTeamName,
          data: performanceData
        });
      }
      
      // Close modal
      closeModal();
      
      // Wait a bit before clicking next driver
      await new Promise(resolve => setTimeout(resolve, 300));
      
    } catch (error) {
      console.error(`Error processing driver ${driverName}:`, error);
      // Try to close modal in case of error
      closeModal();
      await new Promise(resolve => setTimeout(resolve, 300));
      continue;
    }
  }
  
  return allDriverData;
}
