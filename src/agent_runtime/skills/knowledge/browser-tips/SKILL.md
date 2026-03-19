---
name: browser-tips
description: Browser automation tips, retry strategies, and troubleshooting
---

# Browser Tips

## When to Use Browser Automation
- Always use `browser_automation_skill` to fetch HTML before calling `analyze_page_skill`.
- Some university pages require JavaScript rendering — the browser handles this.

## Retry Strategy
- If a page fetch fails, retry up to 2 times with a brief pause.
- Common failures: timeouts (page too slow), connection refused (rate limiting).
- If retries fail, log the URL and skip it rather than blocking the entire batch.

## Large Pages
- Some index pages may have 100+ links. The analysis will extract all of them,
  but `select_detail_candidates_skill` will filter to the most relevant ones.
- If a page is extremely large (>500KB HTML), consider whether it's the right page.

## Dynamic Content
- Some pages load programs via AJAX/infinite scroll. The browser client waits
  for initial page load, but may not capture all dynamically loaded content.
- If you suspect missing programs, note this in your summary.
