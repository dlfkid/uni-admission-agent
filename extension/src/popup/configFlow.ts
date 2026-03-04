import type { ShowStatusFn, StructuredConfig } from "./types";

interface ConfigFlowDeps {
    apiBase: string;
    configBtn: HTMLButtonElement;
    closeConfigBtn: HTMLButtonElement;
    saveConfigBtn: HTMLButtonElement;
    configModal: HTMLDivElement;
    dbUrlInput: HTMLInputElement;
    llmList: HTMLUListElement;
    showStatus: ShowStatusFn;
}

export function initConfigFlow(deps: ConfigFlowDeps): void {
    const {
        apiBase,
        configBtn,
        closeConfigBtn,
        saveConfigBtn,
        configModal,
        dbUrlInput,
        llmList,
        showStatus,
    } = deps;

    let draggedItem: HTMLElement | null = null;

    function createProviderItem(name: string, settings: Record<string, string>): HTMLElement {
        const li = document.createElement("li");
        li.className = "llm-item";
        li.draggable = true;
        li.dataset.provider = name;

        const header = document.createElement("div");
        header.className = "llm-header";

        const handle = document.createElement("span");
        handle.className = "handle";
        handle.textContent = "☰";
        header.appendChild(handle);

        const nameSpan = document.createElement("span");
        nameSpan.className = "name";
        nameSpan.textContent = name;
        header.appendChild(nameSpan);

        const toggle = document.createElement("button");
        toggle.className = "toggle-btn";
        toggle.textContent = "▼";
        header.appendChild(toggle);

        li.appendChild(header);

        const body = document.createElement("div");
        body.className = "llm-settings hidden";

        const keys = Object.keys(settings).sort((a, b) => {
            if (a.includes("API_KEY")) return -1;
            if (b.includes("API_KEY")) return 1;
            return a.localeCompare(b);
        });

        keys.forEach(key => {
            const row = document.createElement("div");
            row.className = "setting-row";

            const label = document.createElement("label");
            if (name === "custom") {
                const cleanKey = key.replace("CUSTOM_LLM_", "").replace(/_/g, " ");
                label.textContent = cleanKey;
            } else {
                label.textContent = key.replace(`${name.toUpperCase()}_`, "").replace(/_/g, " ");
            }

            const input = document.createElement("input");
            if (key.includes("API_KEY")) {
                input.type = "password";
                input.autocomplete = "off";
            } else {
                input.type = "text";
            }
            input.value = settings[key] || "";
            input.dataset.key = key;

            if (name === "custom") {
                if (key === "CUSTOM_LLM_BASE_URL") {
                    input.placeholder = "https://api.openai.com/v1";
                } else if (key === "CUSTOM_LLM_API_KEY") {
                    input.placeholder = "sk-...";
                } else if (key === "CUSTOM_LLM_MODEL_NAME") {
                    input.placeholder = "gpt-4o-mini";
                }
            }

            row.appendChild(label);
            row.appendChild(input);
            body.appendChild(row);
        });

        li.appendChild(body);

        toggle.addEventListener("click", (e) => {
            e.stopPropagation();
            body.classList.toggle("hidden");
            toggle.textContent = body.classList.contains("hidden") ? "▼" : "▲";
        });

        li.addEventListener("dragstart", (e) => {
            draggedItem = li;
            e.dataTransfer!.effectAllowed = "move";
            e.dataTransfer!.setData("text/plain", name);
            li.classList.add("dragging");
        });

        li.addEventListener("dragend", () => {
            li.classList.remove("dragging");
            draggedItem = null;
        });

        li.addEventListener("dragover", (e) => {
            e.preventDefault();
            e.dataTransfer!.dropEffect = "move";

            const targetLi = (e.target as HTMLElement).closest(".llm-item") as HTMLElement;
            if (targetLi && targetLi !== draggedItem) {
                const rect = targetLi.getBoundingClientRect();
                const next = (e.clientY - rect.top) / (rect.bottom - rect.top) > 0.5;
                llmList.insertBefore(draggedItem!, next ? targetLi.nextSibling : targetLi);
            }
        });

        return li;
    }

    function renderConfigForm(config: StructuredConfig) {
        dbUrlInput.value = config.database_url;
        llmList.innerHTML = "";

        const allProviders = { ...config.providers };
        if (!allProviders.custom) {
            allProviders.custom = {
                "CUSTOM_LLM_BASE_URL": "",
                "CUSTOM_LLM_API_KEY": "",
                "CUSTOM_LLM_MODEL_NAME": "gpt-4o-mini",
            };
        }

        const orderedKeys = [...config.llm_priority];
        const providerNames = Object.keys(allProviders);

        for (const p of providerNames) {
            if (!orderedKeys.includes(p)) {
                orderedKeys.push(p);
            }
        }

        orderedKeys.forEach(providerName => {
            const settings = allProviders[providerName];
            if (!settings) return;

            const li = createProviderItem(providerName, settings);
            llmList.appendChild(li);
        });
    }

    configBtn.addEventListener("click", async () => {
        try {
            const res = await fetch(`${apiBase}/config/structured`);
            if (!res.ok) throw new Error("Failed to load config");
            const config: StructuredConfig = await res.json();

            renderConfigForm(config);
            configModal.classList.remove("hidden");
        } catch (err) {
            showStatus("Could not load config", "error");
            console.error(err);
        }
    });

    closeConfigBtn.addEventListener("click", () => {
        configModal.classList.add("hidden");
    });

    saveConfigBtn.addEventListener("click", async () => {
        saveConfigBtn.disabled = true;
        saveConfigBtn.textContent = "Saving...";

        try {
            const newPriority: string[] = [];
            const newProviders: Record<string, Record<string, string>> = {};

            const items = llmList.querySelectorAll(".llm-item");
            items.forEach((item) => {
                const li = item as HTMLElement;
                const name = li.dataset.provider!;

                const settings: Record<string, string> = {};
                const inputs = li.querySelectorAll("input");
                inputs.forEach(inp => {
                    settings[inp.dataset.key!] = inp.value.trim();
                });

                if (name === "custom") {
                    const baseUrl = settings["CUSTOM_LLM_BASE_URL"];
                    if (baseUrl) {
                        newPriority.push(name);
                        newProviders[name] = settings;
                    }
                } else {
                    newPriority.push(name);
                    newProviders[name] = settings;
                }
            });

            const payload: StructuredConfig = {
                database_url: dbUrlInput.value.trim(),
                llm_priority: newPriority,
                providers: newProviders,
            };

            const res = await fetch(`${apiBase}/config/structured`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });

            if (!res.ok) throw new Error("Failed to save");

            showStatus("Config saved!", "success");
            configModal.classList.add("hidden");
        } catch (err) {
            showStatus("Failed to save config", "error");
            console.error(err);
        } finally {
            saveConfigBtn.disabled = false;
            saveConfigBtn.textContent = "Save Changes";
        }
    });
}
