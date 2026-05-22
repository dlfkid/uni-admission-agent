/**
 * UniAdmission Agent — Background Service Worker
 *
 * Handles extension icon clicks to open the side panel.
 */

// Open the side panel when the extension icon is clicked
chrome.action.onClicked.addListener(async (tab) => {
    if (tab.id !== undefined) {
        try {
            await chrome.sidePanel.open({ tabId: tab.id });
        } catch (error) {
            console.error("Failed to open side panel:", error);
        }
    }
});

// Set the side panel behavior to open on action click
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch((error) => {
    console.error("Failed to set panel behavior:", error);
});
