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
type VoiceProvider = "fish" | "piper" | "kokoro" | "edge" | "none";

/** One provider in the picker (tts.status() on the server). */
interface VoiceProviderInfo {
  id: "fish" | "piper" | "kokoro" | "edge";
  available: boolean;
  /** The text of every sentence leaves the machine. */
  cloud: boolean;
  voices: string[];
  /** The voice it would use now. */
  voice: string;
}

interface VoiceStatus {
  /** What JARVIS_TTS_PROVIDER asks for: "auto" or a provider id or "none". */
  wanted: string;
  /** The voice the NEXT sentence is really said with. */
  in_use: VoiceProvider;
  providers: VoiceProviderInfo[];
}

interface StatusResponse {
  claude_code_installed: boolean;
  /** Absent on a server that predates the field. */
  voice_provider?: VoiceProvider;
  /** Absent on a server that predates the picker. */
  voice?: VoiceStatus;
  /** A sentence (already in the language) when the voice is failing, or "". */
  voice_notice?: string;
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
/** The last status the server gave, so "Test" can tell whether there is a saved key. */
let lastStatus: StatusResponse | null = null;
/** The picker has a change that is not saved: do not repaint it under the user. */
let voicePickerDirty = false;
let lastVoice: VoiceStatus | null = null;
let testAudio: HTMLAudioElement | null = null;

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
            <p class="settings-restart-notice" id="voice-fish-notice" style="display:none; text-align:left"></p>
          </div>

          <div class="settings-field" id="voice-picker" style="display:none">
            <label for="select-voice-provider">${h(t("Voice engine"))}</label>
            <select id="select-voice-provider"></select>
            <label for="select-voice-name" id="label-voice-name">${h(t("Voice"))}</label>
            <select id="select-voice-name"></select>
            <p class="settings-restart-notice" id="voice-cloud-warning" style="display:none; text-align:left"></p>
            <div class="settings-input-row">
              <button class="settings-btn" id="btn-test-voice">${h(t("Test voice"))}</button>
              <button class="settings-btn primary" id="btn-save-voice">${h(t("Save voice"))}</button>
            </div>
            <p class="settings-restart-notice" id="voice-picker-result" style="display:none; text-align:left"></p>
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
              <option value="name">${h(t("My name"))}</option>
            </select>
            <p class="settings-restart-notice" id="honorific-name-hint" style="display:none; text-align:left">
              ${h(t("JARVIS will call you by the name above."))}
            </p>
            <p class="settings-restart-notice" id="honorific-name-warning"
               style="display:none; text-align:left; color:var(--settings-warning-color, #e0a030)">
              ${h(t("Add your name above, or this behaves as \"None\"."))}
            </p>
          </div>

          <div class="settings-actions">
            <button class="settings-btn primary" id="btn-save-prefs">${h(t("Save Preferences"))}</button>
          </div>
          <p class="settings-restart-notice" id="prefs-saved-notice" style="display:none">
            ${h(t("Saved. JARVIS's spoken replies use it right away; the brain picks it up next conversation, or after \"start fresh\"."))}
          </p>
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

/** The name of a voice engine, as the page shows it. */
function providerLabel(id: string): string {
  switch (id) {
    case "fish": return t("Fish Audio");
    case "piper": return t("Piper (local, free)");
    case "kokoro": return t("Kokoro (local, free)");
    case "edge": return t("Edge (cloud, Microsoft)");
    case "none": return t("No voice");
    default: return t("Automatic");
  }
}

/** Which voice is active, in words, and the notice for the no-voice case. */
function paintVoice(status: StatusResponse) {
  const text = document.getElementById("voice-provider-text");
  const notice = document.getElementById("voice-none-notice");
  // The engine the next sentence really uses; an older server only says what
  // it chose.
  const provider = status.voice?.in_use ?? status.voice_provider;
  if (provider === "fish" || provider === "piper" || provider === "kokoro" || provider === "edge") {
    setDotStatus("status-voice", "green");
    if (text) text.textContent = providerLabel(provider);
  } else if (provider === "none") {
    setDotStatus("status-voice", "red");
    if (text) text.textContent = t("No voice");
  } else {
    // A server that does not say: no claim either way.
    setDotStatus("status-voice", "off");
    if (text) text.textContent = "—";
  }
  if (notice) notice.style.display = provider === "none" ? "block" : "none";

  // Fish configured but failing (or Edge): what the server says, as text.
  const failing = document.getElementById("voice-fish-notice");
  if (failing) {
    failing.textContent = status.voice_notice ?? "";
    failing.style.display = status.voice_notice ? "block" : "none";
  }
}

function pickerProvider(): string {
  return (document.getElementById("select-voice-provider") as HTMLSelectElement | null)?.value ?? "auto";
}

function addOption(select: HTMLSelectElement, value: string, label: string) {
  const option = document.createElement("option");
  option.value = value;
  option.textContent = label;
  select.appendChild(option);
}

/** The voices of the engine picked, the cloud warning and the buttons. */
function paintVoiceChoice() {
  const voices = document.getElementById("select-voice-name") as HTMLSelectElement | null;
  const label = document.getElementById("label-voice-name");
  const warning = document.getElementById("voice-cloud-warning");
  const test = document.getElementById("btn-test-voice") as HTMLButtonElement | null;
  if (!voices || !lastVoice) return;
  const id = pickerProvider();
  const info = lastVoice.providers.find((p) => p.id === id);
  const previous = voices.value;
  voices.replaceChildren();
  const names = info ? info.voices : [];
  for (const name of names) addOption(voices, name, name);
  // Keep the user's pick if the list still has it, else the voice in use.
  if (previous && names.includes(previous)) voices.value = previous;
  else if (info && names.includes(info.voice)) voices.value = info.voice;
  const hasVoices = names.length > 1;
  voices.style.display = hasVoices ? "" : "none";
  if (label) label.style.display = hasVoices ? "" : "none";
  if (warning) {
    let message = "";
    if (id === "edge") {
      message = t("Cloud voice: the text of every sentence is sent to Microsoft. It is an unofficial service that may stop working; Piper takes over if it fails.");
    } else if (id === "fish") {
      message = t("Cloud voice: the text of every sentence is sent to Fish Audio.");
    }
    warning.textContent = message;
    warning.style.display = message ? "block" : "none";
  }
  if (test) test.disabled = id === "none";
}

/** Fills the engine list with what is installed. Skipped while there is an
 * unsaved change, so a status refresh does not undo the user's choice. */
function paintVoicePicker(status: StatusResponse) {
  const picker = document.getElementById("voice-picker");
  const select = document.getElementById("select-voice-provider") as HTMLSelectElement | null;
  if (!picker || !select) return;
  if (!status.voice) {
    picker.style.display = "none";            // a server that predates the picker
    return;
  }
  picker.style.display = "";
  lastVoice = status.voice;
  if (voicePickerDirty) return;
  select.replaceChildren();
  addOption(select, "auto", providerLabel("auto"));
  for (const p of status.voice.providers) {
    if (p.available) addOption(select, p.id, providerLabel(p.id));
  }
  addOption(select, "none", providerLabel("none"));
  const wanted = status.voice.wanted;
  select.value = Array.from(select.options).some((o) => o.value === wanted) ? wanted : "auto";
  paintVoiceChoice();
}

function showPickerResult(message: string) {
  const out = document.getElementById("voice-picker-result");
  if (!out) return;
  out.textContent = message;
  out.style.display = message ? "block" : "none";
}

/** Plays the audio a voice test returned. The bytes are data, not markup:
 * they go to an <audio> element through a blob, never into the page. */
async function playTest(base64: string, mime: string): Promise<void> {
  const raw = atob(base64);
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
  const url = URL.createObjectURL(new Blob([bytes], { type: mime }));
  if (testAudio) testAudio.pause();
  const audio = new Audio(url);
  testAudio = audio;
  const release = () => URL.revokeObjectURL(url);
  audio.addEventListener("ended", release);
  audio.addEventListener("error", release);
  await audio.play();
}

async function testVoice() {
  const button = document.getElementById("btn-test-voice") as HTMLButtonElement | null;
  if (button) button.disabled = true;
  showPickerResult(t("Testing the voice..."));
  try {
    const voiceSelect = document.getElementById("select-voice-name") as HTMLSelectElement | null;
    const voice = voiceSelect && voiceSelect.style.display !== "none" ? voiceSelect.value : "";
    const result = await apiPost<{ audio: string | null; mime?: string; error?: string; seconds?: number }>(
      "/api/settings/voice-test", { provider: pickerProvider(), voice });
    if (result.audio) {
      showPickerResult(t("Playing the test phrase."));
      await playTest(result.audio, result.mime || "audio/wav");
    } else {
      showPickerResult(result.error || t("That voice did not answer"));
    }
  } catch (e) {
    console.error("[settings] voice test failed:", e);
    showPickerResult(t("That voice did not answer"));
  } finally {
    if (button) button.disabled = pickerProvider() === "none";
  }
}

async function saveVoice() {
  const voiceSelect = document.getElementById("select-voice-name") as HTMLSelectElement | null;
  const voice = voiceSelect && voiceSelect.style.display !== "none" ? voiceSelect.value : "";
  try {
    const res = await fetch("/api/settings/voice", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: pickerProvider(), voice }),
    });
    const body = await res.json() as { success?: boolean; error?: string };
    if (!res.ok || !body.success) {
      showPickerResult(body.error || t("That voice could not be saved"));
      return;
    }
    voicePickerDirty = false;
    showPickerResult(t("Saved. It applies from the next sentence."));
    await loadStatus();
  } catch (e) {
    console.error("[settings] voice save failed:", e);
    showPickerResult(t("That voice could not be saved"));
  }
}

async function loadStatus() {
  try {
    const status = await apiGet<StatusResponse>("/api/settings/status");
    lastStatus = status;

    setDotStatus("status-claude-cli", status.claude_code_installed ? "green" : "red");
    setDotStatus("status-server", "green");

    const serverDetail = document.getElementById("status-server-detail");
    if (serverDetail) serverDetail.textContent = t("port {port} | up {uptime}", {
      port: status.server_port, uptime: formatUptime(status.uptime_seconds),
    });

    // The voice, then the optional Fish key. Fish is red ONLY when there is
    // no voice at all; with Piper answering, a missing key is not a fault.
    paintVoice(status);
    paintVoicePicker(status);
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

/** Shows or hides the "My name" hint and empty-name warning under the
 * Honorific select, from whatever is currently in the two fields — called
 * on load and on every change to either one, so the panel never shows a
 * warning that is already out of date. */
function updateHonorificNotice() {
  const nameEl = document.getElementById("input-user-name") as HTMLInputElement | null;
  const honEl = document.getElementById("input-honorific") as HTMLSelectElement | null;
  const hint = document.getElementById("honorific-name-hint");
  const warning = document.getElementById("honorific-name-warning");
  const isName = honEl?.value === "name";
  const hasName = !!(nameEl && nameEl.value.trim());
  if (hint) hint.style.display = isName ? "block" : "none";
  if (warning) warning.style.display = isName && !hasName ? "block" : "none";
}

async function loadPreferences() {
  try {
    const prefs = await apiGet<PreferencesResponse>("/api/settings/preferences");
    const nameEl = document.getElementById("input-user-name") as HTMLInputElement;
    const honEl = document.getElementById("input-honorific") as HTMLSelectElement;
    if (nameEl) nameEl.value = prefs.user_name || "";
    if (honEl) honEl.value = prefs.honorific || "sir";
    updateHonorificNotice();
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

  // Voice picker
  document.getElementById("select-voice-provider")?.addEventListener("change", () => {
    voicePickerDirty = true;
    showPickerResult("");
    paintVoiceChoice();
  });
  document.getElementById("select-voice-name")?.addEventListener("change", () => {
    voicePickerDirty = true;
    showPickerResult("");
  });
  document.getElementById("btn-test-voice")?.addEventListener("click", () => { void testVoice(); });
  document.getElementById("btn-save-voice")?.addEventListener("click", () => { void saveVoice(); });

  // Test Fish
  document.getElementById("btn-test-fish")?.addEventListener("click", async () => {
    const key = (document.getElementById("input-fish-key") as HTMLInputElement).value.trim();
    // An empty field and no saved key: there is nothing to test, and the key is
    // optional. The dot goes back to what the status says (neutral with Piper
    // answering, red only when there is no voice at all), not to "No key
    // provided" in red.
    if (!key && lastStatus && !lastStatus.env_keys_set.fish_audio) {
      setDotStatus("status-fish", voiceMissing(lastStatus) ? "red" : "off");
      return;
    }
    setDotStatus("status-fish", "yellow");
    try {
      const result = await apiPost<{ valid: boolean; error?: string }>("/api/settings/test-fish", { key_value: key || undefined });
      setDotStatus("status-fish", result.valid ? "green" : "red");
    } catch {
      setDotStatus("status-fish", "red");
    }
  });

  // Preferences: keep the "My name" hint/warning in step with both fields.
  document.getElementById("input-user-name")?.addEventListener("input", updateHonorificNotice);
  document.getElementById("input-honorific")?.addEventListener("change", updateHonorificNotice);

  // Save preferences
  document.getElementById("btn-save-prefs")?.addEventListener("click", async () => {
    const user_name = (document.getElementById("input-user-name") as HTMLInputElement).value.trim();
    const honorific = (document.getElementById("input-honorific") as HTMLSelectElement).value;
    const result = await apiPost<{ success: boolean; error?: string }>(
      "/api/settings/preferences", { user_name, honorific });
    const notice = document.getElementById("prefs-saved-notice");
    if (notice) notice.style.display = result?.success ? "block" : "none";
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
