// Content script for F1 Fantasy Data Exporter
// This file runs on fantasy.formula1.com pages

console.log('F1 Fantasy Data Exporter loaded');

// Listen for messages from popup
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'extractDrivers') {
    const drivers = extractDriverData();
    sendResponse({ data: drivers });
  } else if (request.action === 'extractConstructors') {
    const constructors = extractConstructorData();
    sendResponse({ data: constructors });
  }
  return true;
});
