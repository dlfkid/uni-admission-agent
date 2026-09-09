/**
 * Top-level surface switch: Browse (what's in the database) vs Crawl
 * (put something in it).
 *
 * Browse used to be a modal over the crawl form. It is now a page of its
 * own, and reading the database has become the more frequent activity — so
 * on the web UI it is the first screen.
 *
 * The default differs by platform on purpose. The extension popup exists to
 * crawl the page you are already looking at (it reads the active tab's URL
 * for you) and is only ~400px wide, which a results list has to fight for;
 * opening it on Browse would put the wrong thing in front of the one user
 * who already told us what they want. Same code, same tabs, different
 * landing tab.
 */

export type TabName = "browse" | "crawl";

const PANE_OF: Record<TabName, string> = {
    browse: "pane-browse",
    crawl: "pane-crawl",
};

export interface TabsController {
    activate(name: TabName): void;
    current(): TabName;
}

export function initTabs(deps: {
    browseBtn: HTMLButtonElement;
    crawlBtn: HTMLButtonElement;
    browsePane: HTMLDivElement;
    crawlPane: HTMLDivElement;
    defaultTab: TabName;
    /** Called the first time a tab is shown, and on every later switch to
     *  it. Browse uses this to render its cached rows lazily. */
    onActivate?: (name: TabName) => void;
}): TabsController {
    const { browseBtn, crawlBtn, browsePane, crawlPane, defaultTab, onActivate } = deps;

    const buttons: Record<TabName, HTMLButtonElement> = { browse: browseBtn, crawl: crawlBtn };
    const panes: Record<TabName, HTMLDivElement> = { browse: browsePane, crawl: crawlPane };
    let active: TabName = defaultTab;

    function activate(name: TabName): void {
        active = name;
        for (const key of Object.keys(panes) as TabName[]) {
            const isActive = key === name;
            panes[key].classList.toggle("hidden", !isActive);
            buttons[key].classList.toggle("active", isActive);
            buttons[key].setAttribute("aria-selected", String(isActive));
        }
        onActivate?.(name);
    }

    for (const key of Object.keys(buttons) as TabName[]) {
        buttons[key].addEventListener("click", () => activate(key));
        buttons[key].setAttribute("aria-controls", PANE_OF[key]);
    }

    activate(defaultTab);
    return { activate, current: () => active };
}
