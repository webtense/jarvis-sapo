/**
 * JARVIS — Settings Panel
 *
 * Overlay panel for API keys, connection status, preferences, and system info.
 * Slides in from the right with glass-morphism styling.
 */

import { t, unit } from "./i18n";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Which voice answers right now (tts.provider() on the server). */
type VoiceProvider = "fish" | "piper" | "none";

interface StatusResponse {
  claude_code_installed: boolean;
  /** Absent on a server that predates the field. */
  voice_provider?: VoiceProvider;
  server_port: number;
  uptime_seconds: number;
  env_keys_set: {
    fish_audio: boolean;
    fish_voice_id: boolean;
    user_name: string;
  };
}

interface PreferencesResponse {
  user_name: string;
  honorific: string;
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let panelEl: HTMLElement | null = null;
let isOpen = false;
let isFirstTimeSetup = false;
let setupStep = 0; // 0=fish, 1=name, 2=done

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function apiGet<T>(url: string): Promise<T> {
  const res = await fetch(url);
  return res.json();
}

async function apiPost<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

// ---------------------------------------------------------------------------
// Panel HTML
// ---------------------------------------------------------------------------

/** Escapes a translated phrase for the template below. The catalogue is ours,
 * but this is HTML built from strings, so nothing goes in unescaped. */
function h(text: string): string {
  return text
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/** The voice is Piper (local, free) unless a Fish key says otherwise, so the
 * Fish key is an OPTION. The red dot and the setup wizard only exist for the
 * one case that is actually broken: no voice at all. */
function voiceMissing(status: StatusResponse): boolean {
  return status.voice_provider
    ? status.voice_provider === "none"
    : !status.env_keys_set.fish_audio;
}

function buildPanelHTML(): string {
  return `
    <div class="settings-backdrop" id="settings-backdrop"></div>
    <div class="settings-panel" id="settings-panel-inner">
      <div class="settings-header">
        <h2>${h(t("Settings"))}</h2>
        <button class="settings-close" id="settings-close">&times;</button>
      </div>

      <div class="settings-welcome" id="settings-welcome" style="display:none">
        <p>${h(t("Welcome to JARVIS. Let's get you set up."))}</p>
      </div>

      <div class="settings-body">

        <!-- API Keys -->
        <section class="settings-section" id="section-api-keys">
          <h3>${h(t("API Keys"))}</h3>

          <div class="settings-field">
            <label>${h(t("Active voice"))}</label>
            <div class="status-row">
              <span class="status-dot" id="status-voice"></span>
              <span id="voice-provider-text">--</span>
            </div>
            <p class="settings-restart-notice" id="voice-none-notice" style="display:none; text-align:left">
              ${h(t("No voice is available. Install the local Piper voice or add a Fish Audio key below."))}
            </p>
          </div>

          <div class="settings-field">
            <label>${h(t("Fish Audio API Key (optional)"))}</label>
            <div class="settings-input-row">
              <input type="password" id="input-fish-key" placeholder="${h(t("Fish Audio key..."))}" />
              <button class="settings-btn" id="btn-test-fish">${h(t("Test"))}</button>
              <span class="status-dot" id="status-fish"></span>
            </div>
            <p class="settings-restart-notice" style="text-align:left">
              <a id="fish-key-link" href="https://fish.audio/app/api-keys/" target="_blank" rel="noopener noreferrer">${h(t("Get a key at fish.audio"))}</a>
            </p>
          </div>

          <div class="settings-field">
            <label>${h(t("Fish Voice ID"))}</label>
            <div class="settings-input-row">
              <input type="text" id="input-fish-voice-id" placeholder="612b878b113047d9a770c069c8b4fdfe" />
              <button class="settings-btn" id="btn-save-voice-id">${h(t("Save"))}</button>
            </div>
          </div>

          <div class="settings-actions">
            <button class="settings-btn primary" id="btn-save-keys">${h(t("Save Keys"))}</button>
          </div>
          <p class="settings-restart-notice" id="keys-restart-notice" style="display:none">
            ${h(t("Saved. Restart the server (menu → Restart Server) for this to take effect."))}
          </p>
        </section>

        <!-- Connection Status -->
        <section class="settings-section" id="section-status">
          <h3>${h(t("Connection Status"))}</h3>
          <div class="status-grid">
            <div class="status-row"><span class="status-dot" id="status-claude-cli"></span><span>${h(t("Claude Code CLI"))}</span></div>
            <div class="status-row"><span class="status-dot" id="status-server"></span><span>${h(t("Server"))}</span><span class="status-detail" id="status-server-detail"></span></div>
          </div>
        </section>

        <!-- User Preferences -->
        <section class="settings-section" id="section-preferences">
          <h3>${h(t("User Preferences"))}</h3>

          <div class="settings-field">
            <label>${h(t("Your Name"))}</label>
            <input type="text" id="input-user-name" placeholder="${h(t("Your name"))}" />
          </div>

          <div class="settings-field">
            <label>${h(t("Honorific"))}</label>
            <select id="input-honorific">
              <option value="sir">${h(t("Sir"))}</option>
              <option value="ma'am">${h(t("Ma'am"))}</option>
              <option value="none">${h(t("None"))}</option>
            </select>
          </div>

          <div class="settings-actions">
            <button class="settings-btn primary" id="btn-save-prefs">${h(t("Save Preferences"))}</button>
          </div>
        </section>

        <!-- System Info -->
        <section class="settings-section" id="section-sysinfo">
          <h3>${h(t("System Info"))}</h3>
          <div class="sysinfo-grid">
            <div class="sysinfo-row"><span class="sysinfo-label">${h(t("Server port"))}</span><span id="sysinfo-port">--</span></div>
            <div class="sysinfo-row"><span class="sysinfo-label">${h(t("Uptime"))}</span><span id="sysinfo-uptime">--</span></div>
          </div>
        </section>

        <!-- Setup Navigation (first-time only) -->
        <div class="setup-nav" id="setup-nav" style="display:none">
          <button class="settings-btn primary" id="btn-setup-next">${h(t("Next"))}</button>
        </div>

      </div>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Panel lifecycle
// ---------------------------------------------------------------------------

function createPanel(): HTMLElement {
  const container = document.createElement("div");
  container.id = "settings-container";
  container.innerHTML = buildPanelHTML();
  document.body.appendChild(container);
  return container;
}

function setDotStatus(id: string, status: "green" | "red" | "yellow" | "off") {
  const dot = document.getElementById(id);
  if (!dot) return;
  dot.className = "status-dot";
  if (status !== "off") dot.classList.add(`status-${status}`);
}

function formatUptime(seconds: number): string {
  if (seconds < 60) return unit(Math.floor(seconds), "s");
  if (seconds < 3600) return unit(Math.floor(seconds / 60), "m");
  const hours = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  return `${unit(hours, "h")} ${unit(mins, "m")}`;
}

/** Which voice is active, in words, and the notice for the no-voice case. */
function paintVoice(status: StatusResponse) {
  const text = document.getElementById("voice-provider-text");
  const notice = document.getElementById("voice-none-notice");
  const provider = status.voice_provider;
  if (provider === "fish") {
    setDotStatus("status-voice", "green");
    if (text) text.textContent = t("Fish Audio");
  } else if (provider === "piper") {
    setDotStatus("status-voice", "green");
    if (text) text.textContent = t("Piper (local, free)");
  } else if (provider === "none") {
    setDotStatus("status-voice", "red");
    if (text) text.textContent = t("No voice");
  } else {
    // A server that does not say: no claim either way.
    setDotStatus("status-voice", "off");
    if (text) text.textContent = "—";
  }
  if (notice) notice.style.display = provider === "none" ? "block" : "none";
}

async function loadStatus() {
  try {
    const status = await apiGet<StatusResponse>("/api/settings/status");

    setDotStatus("status-claude-cli", status.claude_code_installed ? "green" : "red");
    setDotStatus("status-server", "green");

    const serverDetail = document.getElementById("status-server-detail");
    if (serverDetail) serverDetail.textContent = t("port {port} | up {uptime}", {
      port: status.server_port, uptime: formatUptime(status.uptime_seconds),
    });

    // The voice, then the optional Fish key. Fish is red ONLY when there is
    // no voice at all; with Piper answering, a missing key is not a fault.
    paintVoice(status);
    setDotStatus("status-fish",
      status.env_keys_set.fish_audio ? "green" : voiceMissing(status) ? "red" : "off");

    // System info
    const portEl = document.getElementById("sysinfo-port");
    if (portEl) portEl.textContent = String(status.server_port);
    const upEl = document.getElementById("sysinfo-uptime");
    if (upEl) upEl.textContent = formatUptime(status.uptime_seconds);

    return status;
  } catch (e) {
    console.error("[settings] failed to load status:", e);
    // EVERY dot, not just the server's. The CLI and Fish dots are drawn from
    // fields of the answer that never arrived, so leaving them green states
    // as fact something this call failed to find out. "off" is the absence
    // of a reading, which is what we have.
    setDotStatus("status-server", "red");
    setDotStatus("status-claude-cli", "off");
    setDotStatus("status-fish", "off");
    setDotStatus("status-voice", "off");
    const voiceText = document.getElementById("voice-provider-text");
    if (voiceText) voiceText.textContent = "—";
    const serverDetail = document.getElementById("status-server-detail");
    if (serverDetail) serverDetail.textContent = t("no answer from the server");
    for (const id of ["sysinfo-port", "sysinfo-uptime"]) {
      const node = document.getElementById(id);
      if (node) node.textContent = "—";
    }
    return null;
  }
}

async function loadPreferences() {
  try {
    const prefs = await apiGet<PreferencesResponse>("/api/settings/preferences");
    const nameEl = document.getElementById("input-user-name") as HTMLInputElement;
    const honEl = document.getElementById("input-honorific") as HTMLSelectElement;
    if (nameEl) nameEl.value = prefs.user_name || "";
    if (honEl) honEl.value = prefs.honorific || "sir";
  } catch (e) {
    console.error("[settings] failed to load preferences:", e);
  }
}

function wireEvents() {
  // Close
  document.getElementById("settings-close")?.addEventListener("click", closeSettings);
  document.getElementById("settings-backdrop")?.addEventListener("click", closeSettings);

  // Save keys
  document.getElementById("btn-save-keys")?.addEventListener("click", async () => {
    const fishKey = (document.getElementById("input-fish-key") as HTMLInputElement).value.trim();

    let savedAny = false;
    if (fishKey) {
      await apiPost("/api/settings/keys", { key_name: "FISH_API_KEY", key_value: fishKey });
      savedAny = true;
    }
    await loadStatus();

    const notice = document.getElementById("keys-restart-notice");
    if (notice) notice.style.display = savedAny ? "block" : "none";
  });

  // Save voice ID
  document.getElementById("btn-save-voice-id")?.addEventListener("click", async () => {
    const voiceId = (document.getElementById("input-fish-voice-id") as HTMLInputElement).value.trim();
    if (voiceId) {
      await apiPost("/api/settings/keys", { key_name: "FISH_VOICE_ID", key_value: voiceId });
    }
  });

  // Test Fish
  document.getElementById("btn-test-fish")?.addEventListener("click", async () => {
    setDotStatus("status-fish", "yellow");
    const key = (document.getElementById("input-fish-key") as HTMLInputElement).value.trim();
    try {
      const result = await apiPost<{ valid: boolean; error?: string }>("/api/settings/test-fish", { key_value: key || undefined });
      setDotStatus("status-fish", result.valid ? "green" : "red");
    } catch {
      setDotStatus("status-fish", "red");
    }
  });

  // Save preferences
  document.getElementById("btn-save-prefs")?.addEventListener("click", async () => {
    const user_name = (document.getElementById("input-user-name") as HTMLInputElement).value.trim();
    const honorific = (document.getElementById("input-honorific") as HTMLSelectElement).value;
    await apiPost("/api/settings/preferences", { user_name, honorific });
    await loadStatus();
  });

  // Setup next button
  document.getElementById("btn-setup-next")?.addEventListener("click", advanceSetup);
}

// ---------------------------------------------------------------------------
// First-time setup wizard
// ---------------------------------------------------------------------------

function enterSetupMode() {
  isFirstTimeSetup = true;
  setupStep = 0;

  const welcome = document.getElementById("settings-welcome");
  if (welcome) welcome.style.display = "block";

  const nav = document.getElementById("setup-nav");
  if (nav) nav.style.display = "flex";

  // Hide sections except API keys
  showSetupStep(0);
}

function showSetupStep(step: number) {
  const sections = ["section-api-keys", "section-preferences"];
  sections.forEach((id, i) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (step === 0 && i === 0) el.style.display = "";
    else if (step === 1 && i === 1) el.style.display = "";
    else el.style.display = "none";
  });

  const nextBtn = document.getElementById("btn-setup-next");
  if (nextBtn) {
    if (step === 0) nextBtn.textContent = t("Next: Set Your Name");
    else if (step === 1) nextBtn.textContent = t("Finish Setup");
    else nextBtn.style.display = "none";
  }
}

async function advanceSetup() {
  setupStep++;
  if (setupStep >= 2) {
    // Done — save everything and close
    isFirstTimeSetup = false;
    const welcome = document.getElementById("settings-welcome");
    if (welcome) welcome.style.display = "none";
    const nav = document.getElementById("setup-nav");
    if (nav) nav.style.display = "none";

    // Show all sections
    ["section-api-keys", "section-status", "section-preferences", "section-sysinfo"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.style.display = "";
    });

    closeSettings();
    return;
  }
  showSetupStep(setupStep);
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export async function openSettings() {
  if (isOpen) return;
  isOpen = true;

  if (!panelEl) {
    panelEl = createPanel();
    wireEvents();
  }

  panelEl.style.display = "block";

  // Trigger animation
  requestAnimationFrame(() => {
    panelEl!.classList.add("open");
  });

  // Load data
  const status = await loadStatus();
  await loadPreferences();

  // Check for first-time setup
  if (status && voiceMissing(status)) {
    enterSetupMode();
  }
}

export function closeSettings() {
  if (!panelEl || !isOpen) return;
  isOpen = false;
  panelEl.classList.remove("open");
  setTimeout(() => {
    if (panelEl) panelEl.style.display = "none";
  }, 300);
}

export function isSettingsOpen(): boolean {
  return isOpen;
}

/**
 * Check if first-time setup is needed and auto-open.
 */
export async function checkFirstTimeSetup(): Promise<boolean> {
  try {
    const status = await apiGet<StatusResponse>("/api/settings/status");
    if (voiceMissing(status)) {
      openSettings();
      return true;
    }
  } catch {
    // Server not ready yet, skip
  }
  return false;
}
