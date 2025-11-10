// Constructor Performance Export Module
// This file contains all logic for extracting constructor/team performance data

// This function automates clicking through all constructor cards and collecting their performance data
async function automateConstructorPerformanceExport() {
  const allConstructorData = [];
  
  // First, make sure we're on the drivers tab (to reset state)
  const driversTab = document.querySelector('button[aria-label*="DRIVER"]');
  if (driversTab && !driversTab.classList.contains('si-active')) {
    driversTab.click();
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  
  // Now click the Constructor tab (try multiple selectors)
  let constructorTab = document.querySelector('button[aria-label*="CONSTRUCTOR"]');
  
  if (!constructorTab) {
    // Fallback: try finding by text content
    const buttons = document.querySelectorAll('.si-tabs__wrap button');
    for (const btn of buttons) {
      if (btn.textContent.includes('CONSTRUCTOR')) {
        constructorTab = btn;
        break;
      }
    }
  }
  
  if (!constructorTab) {
    console.error('Constructor tab not found');
    return [];
  }
  
  constructorTab.click();
  
  // Wait for tab content to load
  await new Promise(resolve => setTimeout(resolve, 1500));
  
  // Find all constructor card rows (skip first one which is the header)
  const constructorRows = document.querySelectorAll('.si-stats__list-grid ul li');
  
  if (constructorRows.length <= 1) {
    console.error('No constructors found');
    return [];
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
  
  // Process each constructor (skip index 0 which is header)
  for (let i = 1; i < constructorRows.length; i++) {
    const row = constructorRows[i];
    
    // Get constructor info from the list before clicking
    const nameElement = row.querySelector('.si-miniCard__name');
    
    if (!nameElement) continue;
    
    const nameSpans = nameElement.querySelectorAll('span');
    if (nameSpans.length < 1) continue;
    
    const constructorName = nameSpans[0].textContent.trim();
    
    try {
      // Click the constructor card
      const clickTarget = row.querySelector('.si-miniCard__wrap');
      if (!clickTarget) continue;
      
      clickTarget.click();
      
      // Wait for modal to open
      await waitForModal();
      
      // Extract performance data directly (inlined logic)
      const performanceData = [];
      
      // Get constructor value from modal
      const constructorValueElement = document.querySelector('.si-player__trends span');
      const constructorValue = constructorValueElement ? constructorValueElement.textContent.trim() : '';
      
      // Get season total from modal
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
                'Constructor Name': constructorName,
                'Constructor Value': constructorValue,
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
        allConstructorData.push({
          constructorName: constructorName,
          data: performanceData
        });
      }
      
      // Close modal
      closeModal();
      
      // Wait a bit before clicking next constructor
      await new Promise(resolve => setTimeout(resolve, 300));
      
    } catch (error) {
      console.error(`Error processing constructor ${constructorName}:`, error);
      // Try to close modal in case of error
      closeModal();
      await new Promise(resolve => setTimeout(resolve, 300));
      continue;
    }
  }
  
  return allConstructorData;
}
