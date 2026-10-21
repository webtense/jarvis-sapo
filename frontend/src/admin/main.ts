/**
 * JARVIS — Admin screen (/admin.html).
 *
 * Six sections: who you are, the voice that answers, the ear that listens,
 * what JARVIS is connected to, what it may do in the house, and what it is
 * running. The first three are editable; the last three are readouts.
 *
 * WHAT THIS PAGE MAY WRITE is decided by the server, not here: every save
 * goes to `POST /api/admin/settings`, whose schema is closed (a fixed list
 * of keys, each with an enumeration or an integer range). The controls below
 * are BUILT from the schema that `GET /api/admin/settings` returns, so the
 * page cannot offer a value the server would refuse, and cannot be edited
 * into offering a key the server does not own.
 *
 * Keys and tokens are not here at all. The Fish Audio key keeps its own
 * field in Settings on the main page; nothing on this screen reads or writes
 * a credential, and no response it receives carries one.
 */

import { ago, t, unit } from "../i18n";
import {
  actions, button, el, field, lines, note, numberInput, panel, readout,
  say, select, textInput,
} from "./dom";

// ---------------------------------------------------------------------------
// What the server says
// ---------------------------------------------------------------------------

interface Schema {
  enums: Record<string, string[]>;
  ints: Record<string, { min: number; max: number }>;
  text: Record<string, { max_chars: number }>;
  applies: Record<string, string>;
}

interface SettingsResponse {
  values: Record<string, string>;
  schema: Schema;
}

interface SaveResponse {
  success?: boolean;
  error?: string;
  key?: string;
  saved?: string[];
  applies?: Record<string, string>;
}

interface VoiceProviderInfo {
  id: string;
  available: boolean;
  cloud: boolean;
  voices: string[];
  voice: string;
}

interface StatusResponse {
  voice_provider?: string;
  voice?: { wanted: string; in_use: string; providers: VoiceProviderInfo[] };
  voice_notice?: string;
  server_port: number;
  uptime_seconds: number;
}

interface ServerEntry {
  name: string;
  kind: string;
  status: string;
}

interface ConnectionsResponse {
  servers: ServerEntry[];
  problems: string[];
  home_assistant: {
    configured: boolean;
    address_set: boolean;
    read_token: string;
    actions_token: string;
  };
  google: {
    configured: boolean;
    token_file: string;
    credentials_complete: boolean;
    scopes: string[];
    account_known: boolean;
  };
  sapofit: {
    configured: boolean;
    token_file: string;
    watch_enabled: boolean;
    watch_running: boolean;
    cursor: string;
    last_read_at: string;
  };
}

interface HomeLogLine {
  ts: string;
  entity: string;
  result: string;
  dryrun: boolean;
}

interface HomeResponse {
  enabled: boolean;
  reason: string;
  dryrun: boolean;
  confirm_strict: boolean;
  panel_confirm: boolean;
  by_domain: Record<string, number>;
  names: string[];
  problems: string[];
  excluded: { valid: boolean; entity_ids: number; patterns: number; domains: number };
  log: HomeLogLine[];
}

/** `GET /api/bridge/pending`: only the `home` and `google` lists are used here. */
interface PendingItem { id: string; text: string; age: number }
interface PendingResponse {
  home?: PendingItem[];
  google?: PendingItem[];
}

interface GoogleLogLine {
  ts: string;
  kind: string;
  result: string;
  confirmed_by: string;
  dryrun: boolean;
}

interface GoogleResponse {
  enabled: boolean;
  reason: string;
  dryrun: boolean;
  confirm_strict: boolean;
  panel_confirm: boolean;
  calendars: string[];
  task_lists: string[];
  max_per_minute: number;
  ttl_sec: number;
  account_known: boolean;
  problems: string[];
  log: GoogleLogLine[];
}

interface DecisionResponse {
  ok?: boolean;
  text?: string;
  error?: string;
}

interface SystemResponse {
  version: string;
  uptime_seconds: number;
  service: string;
  voice_provider: string;
  language: string;
  listening: {
    mode: string;
    wake: string;
    wake_window_sec: number;
    barge_in: boolean;
    model: string;
    warnings: string[];
  };
}

// ---------------------------------------------------------------------------
// Talking to it
// ---------------------------------------------------------------------------

async function apiGet<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(String(res.status));
  return (await res.json()) as T;
}

async function saveSettings(settings: Record<string, string>): Promise<SaveResponse> {
  const res = await fetch("/api/admin/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings }),
  });
  return (await res.json()) as SaveResponse;
}

/** The sentence that says when a saved setting starts counting. */
function appliesText(applies: Record<string, string>): string {
  const kinds = new Set(Object.values(applies));
  if (kinds.has("restart")) {
    return t("Saved. Restart the server for this to take effect.");
  }
  if (kinds.has("next_listen")) {
    return t("Saved. It applies the next time JARVIS starts listening.");
  }
  return t("Saved. It applies right away.");
}

/** Saves a form and reports honestly: which field was refused, and why. */
async function submit(settings: Record<string, string>, result: HTMLElement,
                      after?: () => Promise<void>): Promise<void> {
  say(result, t("Saving..."));
  try {
    const body = await saveSettings(settings);
    if (!body.success) {
      say(result, body.error || t("That could not be saved"), "tone-bad");
      return;
    }
    say(result, appliesText(body.applies || {}), "tone-ok");
    if (after) await after();
  } catch {
    say(result, t("No answer from the server"), "tone-bad");
  }
}

// ---------------------------------------------------------------------------
// Words for the values the server returns
// ---------------------------------------------------------------------------

function honorificLabel(value: string): string {
  switch (value) {
    case "sir": return t("Sir");
    case "ma'am": return t("Ma'am");
    case "none": return t("None");
    case "name": return t("My name");
    default: return value;
  }
}

function languageLabel(value: string): string {
  return value === "es" ? t("Castilian") : t("English");
}

function sttLabel(value: string): string {
  switch (value) {
    case "browser": return t("The browser's (Chrome, in the cloud)");
    case "local": return t("This machine's (Whisper, offline)");
    case "auto": return t("Whichever the browser has");
    default: return value;
  }
}

function modelLabel(value: string): string {
  switch (value) {
    case "tiny": return t("tiny — fastest, least accurate");
    case "base": return t("base — the balanced default");
    case "small": return t("small — most accurate, slowest");
    default: return value;
  }
}

function wakeLabel(value: string): string {
  switch (value) {
    case "off": return t("Off — every phrase is taken");
    case "on": return t("\"Hey Jarvis\" (openWakeWord, English only)");
    case "whisper": return t("Phrases starting with \"Jarvis\" — transcribes everything it hears");
    default: return value;
  }
}

function onOffLabel(value: string): string {
  return value === "on" ? t("On") : t("Off");
}

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

function serverStatusLabel(status: string): string {
  switch (status) {
    case "ready": return t("answers");
    case "down": return t("does not answer");
    case "missing": return t("its program is not installed");
    default: return t("not checked");
  }
}

function tokenStateLabel(state: string): string {
  switch (state) {
    case "ok": return t("present, permissions correct");
    case "permissions": return t("present, but readable by others (chmod 600)");
    case "missing": return t("not found");
    case "unreadable": return t("cannot be read");
    default: return t("not configured");
  }
}

function toneFor(ok: boolean): string {
  return ok ? "tone-ok" : "tone-bad";
}

function formatUptime(seconds: number): string {
  if (seconds < 60) return unit(Math.floor(seconds), "s");
  if (seconds < 3600) return unit(Math.floor(seconds / 60), "m");
  const hours = Math.floor(seconds / 3600);
  return `${unit(hours, "h")} ${unit(Math.floor((seconds % 3600) / 60), "m")}`;
}

/** An enum <select> straight from the server's own list of values. */
function enumSelect(schema: Schema, key: string, current: string,
                    label: (value: string) => string): HTMLSelectElement {
  const values = schema.enums[key] ?? [];
  return select(values.map((v): [string, string] => [v, label(v)]), current);
}

// ---------------------------------------------------------------------------
// Section 1 — Profile
// ---------------------------------------------------------------------------

function renderProfile(view: HTMLElement, data: SettingsResponse): void {
  view.replaceChildren();
  const { root, body } = panel(t("Who you are"));

  const max = data.schema.text["USER_NAME"]?.max_chars ?? 64;
  const name = textInput(data.values["USER_NAME"] ?? "", max);
  body.appendChild(field("admin-user-name", t("Your name"), name));

  const honorific = enumSelect(data.schema, "HONORIFIC",
    data.values["HONORIFIC"] ?? "sir", honorificLabel);
  body.appendChild(field("admin-honorific", t("How JARVIS addresses you"), honorific));
  body.appendChild(note(t("With \"My name\" JARVIS uses the name above; with no name there, it behaves as \"None\".")));

  const language = enumSelect(data.schema, "JARVIS_LANGUAGE",
    data.values["JARVIS_LANGUAGE"] ?? "en", languageLabel);
  body.appendChild(field("admin-language", t("Language"), language));
  body.appendChild(note(t("The language of the spoken replies and of this interface. It needs a restart, and the interface is compiled per language.")));

  const save = button(t("Save"), "primary");
  const bar = actions(save);
  body.appendChild(bar.root);
  save.addEventListener("click", () => {
    void submit({
      USER_NAME: name.value.trim(),
      HONORIFIC: honorific.value,
      JARVIS_LANGUAGE: language.value,
    }, bar.result);
  });

  view.appendChild(root);
}

// ---------------------------------------------------------------------------
// Section 2 — Voice out
// ---------------------------------------------------------------------------

/** The voice section reuses the endpoints the Settings panel already has
 * (`/api/settings/status`, `/api/settings/voice`, `/api/settings/voice-test`):
 * one validator for the voice, not two that can disagree. */
function renderVoice(view: HTMLElement, status: StatusResponse | null): void {
  view.replaceChildren();
  const { root, body } = panel(t("The voice that answers"));

  if (!status) {
    body.appendChild(note(t("No answer from the server")));
    view.appendChild(root);
    return;
  }

  const inUse = status.voice?.in_use ?? status.voice_provider ?? "";
  body.appendChild(readout(t("Voice in use now"), providerLabel(inUse),
    toneFor(inUse !== "" && inUse !== "none")));
  if (status.voice_notice) {
    body.appendChild(note(status.voice_notice));
  }

  const voice = status.voice;
  if (!voice) {
    body.appendChild(note(t("This server does not offer the voice picker.")));
    view.appendChild(root);
    return;
  }

  const options: Array<[string, string]> = [["auto", providerLabel("auto")]];
  for (const p of voice.providers) {
    if (p.available) options.push([p.id, providerLabel(p.id)]);
  }
  options.push(["none", providerLabel("none")]);
  const engine = select(options, voice.wanted);
  body.appendChild(field("admin-voice-engine", t("Voice engine"), engine));

  const names = select([], "");
  const namesField = field("admin-voice-name", t("Voice"), names);
  body.appendChild(namesField);

  const cloud = note("");
  cloud.hidden = true;
  body.appendChild(cloud);

  const test = button(t("Test voice"));
  const save = button(t("Save voice"), "primary");
  const bar = actions(test, save);
  body.appendChild(bar.root);

  const paintNames = (): void => {
    const info = voice.providers.find((p) => p.id === engine.value);
    const list = info ? info.voices : [];
    names.replaceChildren();
    for (const name of list) {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      names.appendChild(option);
    }
    if (info && list.includes(info.voice)) names.value = info.voice;
    const many = list.length > 1;
    namesField.hidden = !many;
    let message = "";
    if (engine.value === "edge") {
      message = t("Cloud voice: the text of every sentence is sent to Microsoft.");
    } else if (engine.value === "fish") {
      message = t("Cloud voice: the text of every sentence is sent to Fish Audio.");
    }
    cloud.textContent = message;
    cloud.hidden = !message;
    test.disabled = engine.value === "none";
  };
  paintNames();
  engine.addEventListener("change", () => { paintNames(); say(bar.result, ""); });

  const chosenVoice = (): string => (namesField.hidden ? "" : names.value);

  test.addEventListener("click", () => {
    void (async () => {
      test.disabled = true;
      say(bar.result, t("Testing the voice..."));
      try {
        const res = await fetch("/api/settings/voice-test", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ provider: engine.value, voice: chosenVoice() }),
        });
        const out = (await res.json()) as { audio?: string | null; mime?: string; error?: string };
        if (out.audio) {
          say(bar.result, t("Playing the test phrase."), "tone-ok");
          // The bytes are DATA: they reach an <audio> through a blob and
          // never touch the page as markup.
          const raw = atob(out.audio);
          const bytes = new Uint8Array(raw.length);
          for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
          const url = URL.createObjectURL(new Blob([bytes], { type: out.mime || "audio/wav" }));
          const audio = new Audio(url);
          audio.addEventListener("ended", () => URL.revokeObjectURL(url));
          audio.addEventListener("error", () => URL.revokeObjectURL(url));
          await audio.play();
        } else {
          say(bar.result, out.error || t("That voice did not answer"), "tone-bad");
        }
      } catch {
        say(bar.result, t("That voice did not answer"), "tone-bad");
      } finally {
        test.disabled = engine.value === "none";
      }
    })();
  });

  save.addEventListener("click", () => {
    void (async () => {
      say(bar.result, t("Saving..."));
      try {
        const res = await fetch("/api/settings/voice", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ provider: engine.value, voice: chosenVoice() }),
        });
        const out = (await res.json()) as { success?: boolean; error?: string };
        if (!res.ok || !out.success) {
          say(bar.result, out.error || t("That voice could not be saved"), "tone-bad");
          return;
        }
        say(bar.result, t("Saved. It applies from the next sentence."), "tone-ok");
      } catch {
        say(bar.result, t("That voice could not be saved"), "tone-bad");
      }
    })();
  });

  view.appendChild(root);
}

// ---------------------------------------------------------------------------
// Section 3 — Listening
// ---------------------------------------------------------------------------

function renderListening(view: HTMLElement, data: SettingsResponse): void {
  view.replaceChildren();
  const { root, body } = panel(t("The ear that listens"));
  const schema = data.schema;
  const values = data.values;

  const stt = enumSelect(schema, "JARVIS_STT", values["JARVIS_STT"] ?? "browser", sttLabel);
  body.appendChild(field("admin-stt", t("Speech recognition"), stt));
  body.appendChild(note(t("The browser's is Chrome's and sends your audio to Google; the local one needs faster-whisper and never leaves the machine.")));

  const model = enumSelect(schema, "JARVIS_STT_MODEL",
    values["JARVIS_STT_MODEL"] ?? "base", modelLabel);
  body.appendChild(field("admin-stt-model", t("Local model"), model));

  const wake = enumSelect(schema, "JARVIS_WAKE_WORD",
    values["JARVIS_WAKE_WORD"] ?? "off", wakeLabel);
  body.appendChild(field("admin-wake", t("Wake word"), wake));
  body.appendChild(note(t("The \"Jarvis\" option needs no extra model, and that is its cost: every phrase it hears is transcribed to find out whether it begins with the word.")));

  const windowRange = schema.ints["JARVIS_WAKE_WINDOW_SEC"] ?? { min: 5, max: 300 };
  const windowSec = numberInput(values["JARVIS_WAKE_WINDOW_SEC"] ?? "20",
    windowRange.min, windowRange.max);
  body.appendChild(field("admin-wake-window", t("Conversation window (seconds)"), windowSec));
  body.appendChild(note(t("After an accepted phrase, how long you may go on talking without saying \"Jarvis\" again.")));

  const barge = enumSelect(schema, "JARVIS_BARGE_IN",
    values["JARVIS_BARGE_IN"] ?? "off", onOffLabel);
  body.appendChild(field("admin-barge", t("Interrupt by talking over him"), barge));

  const rmsRange = schema.ints["JARVIS_BARGE_IN_RMS"] ?? { min: 200, max: 20000 };
  const rms = numberInput(values["JARVIS_BARGE_IN_RMS"] ?? "1200",
    rmsRange.min, rmsRange.max);
  body.appendChild(field("admin-barge-rms", t("Loudness that counts as your voice"), rms));

  const msRange = schema.ints["JARVIS_BARGE_IN_MS"] ?? { min: 100, max: 2000 };
  const ms = numberInput(values["JARVIS_BARGE_IN_MS"] ?? "400", msRange.min, msRange.max);
  body.appendChild(field("admin-barge-ms", t("How long you must keep talking (ms)"), ms));
  body.appendChild(note(t("Raise the loudness if JARVIS cuts himself off on his own echo. The Esc key is still the reliable way to stop him.")));

  const save = button(t("Save"), "primary");
  const bar = actions(save);
  body.appendChild(bar.root);
  save.addEventListener("click", () => {
    void submit({
      JARVIS_STT: stt.value,
      JARVIS_STT_MODEL: model.value,
      JARVIS_WAKE_WORD: wake.value,
      JARVIS_WAKE_WINDOW_SEC: windowSec.value.trim(),
      JARVIS_BARGE_IN: barge.value,
      JARVIS_BARGE_IN_RMS: rms.value.trim(),
      JARVIS_BARGE_IN_MS: ms.value.trim(),
    }, bar.result);
  });

  view.appendChild(root);
}

// ---------------------------------------------------------------------------
// Section 4 — Connections (read only)
// ---------------------------------------------------------------------------

function renderConnections(view: HTMLElement, data: ConnectionsResponse | null): void {
  view.replaceChildren();
  if (!data) {
    const { root, body } = panel(t("Connections"));
    body.appendChild(note(t("No answer from the server")));
    view.appendChild(root);
    return;
  }

  const servers = panel(t("Connections"), t("read only"));
  servers.body.appendChild(note(t("What JARVIS is connected to, by name. Addresses, command lines and token paths are deliberately not shown — they are edited in the connections file.")));
  if (data.servers.length === 0) {
    servers.body.appendChild(note(t("Nothing is connected.")));
  }
  for (const entry of data.servers) {
    const kind = entry.kind === "remote" ? t("remote service") : t("local program");
    servers.body.appendChild(readout(entry.name,
      `${kind} · ${serverStatusLabel(entry.status)}`,
      toneFor(entry.status === "ready" || entry.status === "unknown")));
  }
  if (data.problems.length > 0) {
    servers.body.appendChild(el("h3", "subhead", t("Left out of the connection")));
    servers.body.appendChild(lines(data.problems, ""));
  }
  view.appendChild(servers.root);

  const ha = panel(t("Home Assistant"));
  ha.body.appendChild(readout(t("Configured"),
    data.home_assistant.configured ? t("yes") : t("no"),
    toneFor(data.home_assistant.configured)));
  ha.body.appendChild(readout(t("Address"),
    data.home_assistant.address_set ? t("set") : t("not set"),
    toneFor(data.home_assistant.address_set)));
  ha.body.appendChild(readout(t("Reading token"),
    tokenStateLabel(data.home_assistant.read_token),
    toneFor(data.home_assistant.read_token === "ok")));
  ha.body.appendChild(readout(t("Acting token"),
    tokenStateLabel(data.home_assistant.actions_token)));
  ha.body.appendChild(note(t("The acting token is a different file from the reading one, on purpose. Neither is ever shown here.")));
  view.appendChild(ha.root);

  const google = panel(t("Google"));
  google.body.appendChild(readout(t("Configured"),
    data.google.configured ? t("yes") : t("no"), toneFor(data.google.configured)));
  google.body.appendChild(readout(t("Credentials file"),
    tokenStateLabel(data.google.token_file), toneFor(data.google.token_file === "ok")));
  google.body.appendChild(readout(t("Credentials"),
    data.google.credentials_complete ? t("complete") : t("incomplete or absent"),
    toneFor(data.google.credentials_complete)));
  google.body.appendChild(readout(t("Account"), t("JARVIS does not store it")));
  google.body.appendChild(note(t("The authorisation records permissions, not the address of the account, so this screen will not guess one.")));
  if (data.google.scopes.length > 0) {
    google.body.appendChild(el("h3", "subhead", t("Permissions granted")));
    google.body.appendChild(lines(data.google.scopes, ""));
  }
  view.appendChild(google.root);

  // SapoFit and Croac's watch: states and times only. No message text is ever
  // sent to this page, so none can be shown here.
  const sapofit = panel(t("SapoFit"));
  sapofit.body.appendChild(readout(t("Configured"),
    data.sapofit.configured ? t("yes") : t("no"), toneFor(data.sapofit.configured)));
  sapofit.body.appendChild(readout(t("Token file"),
    tokenStateLabel(data.sapofit.token_file), toneFor(data.sapofit.token_file === "ok")));
  const watchState = data.sapofit.watch_running ? t("watch running")
    : data.sapofit.watch_enabled ? t("switched on, not running") : t("switched off");
  sapofit.body.appendChild(readout(t("Coach watch"), watchState,
    toneFor(data.sapofit.watch_running)));
  sapofit.body.appendChild(readout(t("Last reading"),
    data.sapofit.last_read_at || t("never")));
  sapofit.body.appendChild(readout(t("Last cursor"),
    data.sapofit.cursor || t("none")));
  sapofit.body.appendChild(note(t("Only the coach's feed is read. No message is shown here, only when it was last read.")));
  view.appendChild(sapofit.root);
}

// ---------------------------------------------------------------------------
// Section 5 — House (read only)
// ---------------------------------------------------------------------------

const PENDING_REFRESH_MS = 4000;
let pendingTimer: number | null = null;
/** What the last decision said; it outlives the repaint of the list. */
let lastDecision: { text: string; tone: string } | null = null;

/** The yes or no to one pending house action. It reuses the route the rest of
 * the interface already has (`/api/home/{id}/{confirm|reject}`): the server
 * decides whether a panel confirmation is allowed at all, not this page. */
async function decideProposal(kind: "home" | "google", id: string,
                              decision: "confirm" | "reject"): Promise<{ text: string; tone: string }> {
  try {
    const res = await fetch(`/api/${kind}/${encodeURIComponent(id)}/${decision}`, { method: "POST" });
    if (res.status === 404) {
      return { text: t("That request is no longer pending."), tone: "tone-bad" };
    }
    const out = (await res.json()) as DecisionResponse;
    // The sentence is the server's own, already in the spoken language.
    return {
      text: out.text || out.error || t("No answer from the server"),
      tone: res.ok && out.ok ? "tone-ok" : "tone-bad",
    };
  } catch {
    return { text: t("No answer from the server"), tone: "tone-bad" };
  }
}

async function decideHome(id: string, decision: "confirm" | "reject"): Promise<void> {
  lastDecision = await decideProposal("home", id, decision);
}

/** Paints the list of house actions waiting for the user. Text is DATA. */
function paintPending(host: HTMLElement, data: PendingResponse | null): void {
  host.replaceChildren();
  const { root, body } = panel(t("Waiting for your confirmation"), t("refreshes by itself"));
  body.appendChild(note(t("An action JARVIS proposed while nobody could hear him. Nothing happens until you confirm it here; Reject always works.")));
  const outcome = el("p", "result");
  outcome.hidden = true;
  if (lastDecision) say(outcome, lastDecision.text, lastDecision.tone);
  body.appendChild(outcome);
  const items = data?.home ?? [];
  if (!data) {
    body.appendChild(note(t("No answer from the server")));
  } else if (items.length === 0) {
    body.appendChild(note(t("Nothing is waiting.")));
  }
  for (const item of items) {
    const row = el("div", "pending-item");
    row.appendChild(el("p", "pending-text", item.text));
    row.appendChild(el("p", "pending-age", ago(unit(item.age, "s"))));
    const confirm = button(t("Confirm"), "primary");
    const reject = button(t("Reject"));
    row.appendChild(actions(confirm, reject).root);
    const decide = (decision: "confirm" | "reject"): void => {
      confirm.disabled = true;
      reject.disabled = true;
      void decideHome(item.id, decision).then(() => refreshPending(host));
    };
    confirm.addEventListener("click", () => decide("confirm"));
    reject.addEventListener("click", () => decide("reject"));
    body.appendChild(row);
  }
  host.appendChild(root);
}

async function refreshPending(host: HTMLElement): Promise<void> {
  let data: PendingResponse | null = null;
  try {
    data = await apiGet<PendingResponse>("/api/bridge/pending");
  } catch { data = null; }
  paintPending(host, data);
}

function watchPending(host: HTMLElement): void {
  if (pendingTimer !== null) window.clearInterval(pendingTimer);
  void refreshPending(host);
  pendingTimer = window.setInterval(() => {
    // Only while somebody is looking at the house.
    if (!host.isConnected || document.hidden) return;
    const view = host.closest(".view");
    if (view instanceof HTMLElement && view.hidden) return;
    // Never repaint under a pointer or a keyboard focus about to press a
    // button: a different proposal could take the place of the one that was
    // read, and the click would then confirm something never read.
    if (host.matches(":hover") || host.contains(document.activeElement)) return;
    void refreshPending(host);
  }, PENDING_REFRESH_MS);
}

function renderHouse(view: HTMLElement, data: HomeResponse | null): void {
  view.replaceChildren();
  if (!data) {
    const { root, body } = panel(t("The house"));
    body.appendChild(note(t("No answer from the server")));
    view.appendChild(root);
    return;
  }

  const state = panel(t("The house"), t("read only"));
  state.body.appendChild(readout(t("House control"),
    data.enabled ? t("on") : t("off"), data.enabled ? "tone-warn" : "tone-ok"));
  if (!data.enabled && data.reason) state.body.appendChild(note(data.reason));
  state.body.appendChild(readout(t("Dry run"),
    data.dryrun ? t("yes — nothing is really done") : t("no")));
  state.body.appendChild(readout(t("Strict confirmation"),
    data.confirm_strict ? t("yes") : t("no")));
  state.body.appendChild(note(t("The allow-list is NOT editable from this screen. That is a deliberate security decision: a web page that can add an entity to it turns one stolen click into a key to the house. Edit data/jarvis/home-actions.json by hand, with permissions 600, and restart.")));
  view.appendChild(state.root);

  if (data.panel_confirm) {
    const host = el("div", "pending-host");
    view.appendChild(host);
    watchPending(host);
  } else {
    const off = panel(t("Confirmation on the screen"));
    off.body.appendChild(note(t("Off: house actions are confirmed aloud only. It is turned on with \"panel_confirm\": true in the allow-list file, by hand.")));
    view.appendChild(off.root);
  }

  const domains = panel(t("What JARVIS may touch"));
  const entries = Object.entries(data.by_domain);
  if (entries.length === 0) {
    domains.body.appendChild(note(t("Nothing.")));
  }
  for (const [domain, count] of entries) {
    domains.body.appendChild(readout(domain, String(count)));
  }
  domains.body.appendChild(readout(t("Excluded by the resort filter"),
    `${data.excluded.entity_ids} + ${data.excluded.patterns} + ${data.excluded.domains}`,
    toneFor(data.excluded.valid)));
  domains.body.appendChild(note(t("Exclusions counted as exact ids + patterns + whole domains. An exclusion file that cannot be trusted blocks everything.")));
  if (data.names.length > 0) {
    domains.body.appendChild(el("h3", "subhead", t("The names JARVIS will say")));
    domains.body.appendChild(lines(data.names, ""));
  }
  if (data.problems.length > 0) {
    domains.body.appendChild(el("h3", "subhead", t("Ignored in the allow-list")));
    domains.body.appendChild(lines(data.problems, ""));
  }
  view.appendChild(domains.root);

  const log = panel(t("Last actions"), t("the last 20"));
  if (data.log.length === 0) {
    log.body.appendChild(note(t("Nothing has been attempted.")));
  }
  for (const line of data.log) {
    const suffix = line.dryrun ? ` (${t("dry run")})` : "";
    log.body.appendChild(readout(`${line.ts} · ${line.entity}`, `${line.result}${suffix}`,
      toneFor(line.result === "ok" || line.result === "dryrun")));
  }
  view.appendChild(log.root);
}

// ---------------------------------------------------------------------------
// Section 6 — Google (read only, plus the panel's yes/no if the user allowed it)
// ---------------------------------------------------------------------------

let googleTimer: number | null = null;
let googleDecision: { text: string; tone: string } | null = null;

/** What the sentence of a pending proposal still has to live. */
function windowLeft(ttl: number, age: number): string {
  const left = ttl - age;
  return left > 0
    ? t("{time} left to decide", { time: unit(left, "s") })
    : t("The window has run out");
}

function paintGoogleStatus(host: HTMLElement, data: GoogleResponse | null): void {
  host.replaceChildren();
  const { root, body } = panel(t("Google"), t("read only"));
  if (!data) {
    body.appendChild(note(t("No answer from the server")));
    host.appendChild(root);
    return;
  }
  body.appendChild(readout(t("Google writing"),
    data.enabled ? t("on") : t("off"), data.enabled ? "tone-warn" : "tone-ok"));
  if (!data.enabled && data.reason) body.appendChild(note(data.reason));
  body.appendChild(readout(t("Dry run"),
    data.dryrun ? t("yes — nothing is really done") : t("no")));
  body.appendChild(readout(t("Strict confirmation"),
    data.confirm_strict ? t("yes") : t("no")));
  body.appendChild(readout(t("Account"), t("JARVIS does not store it")));
  if (data.enabled) {
    body.appendChild(readout(t("Allowed per minute"), String(data.max_per_minute)));
    body.appendChild(el("h3", "subhead", t("Calendars it may write to")));
    body.appendChild(lines(data.calendars, t("Nothing.")));
    body.appendChild(el("h3", "subhead", t("Task lists it may write to")));
    body.appendChild(lines(data.task_lists, t("Nothing.")));
    if (data.problems.length > 0) {
      body.appendChild(el("h3", "subhead", t("Ignored in the allow-list")));
      body.appendChild(lines(data.problems, ""));
    }
  }
  body.appendChild(note(t("The Google allow-list is NOT editable from this screen, for the same reason as the house one. Edit data/jarvis/google-actions.json by hand, with permissions 600, and restart.")));
  host.appendChild(root);
}

function paintGooglePending(host: HTMLElement, data: GoogleResponse | null,
                            pending: PendingResponse | null): void {
  host.replaceChildren();
  const canConfirm = data?.panel_confirm === true;
  const { root, body } = panel(t("Waiting for your confirmation"), t("refreshes by itself"));
  if (canConfirm) {
    body.appendChild(note(t("A change to your Google account that JARVIS proposed. Nothing happens until you confirm it here or aloud; Reject always works.")));
  } else {
    body.appendChild(note(t("Confirm by voice. Confirming from this screen is off: it is turned on with \"panel_confirm\": true in the Google allow-list file, by hand. Reject always works.")));
  }
  const outcome = el("p", "result");
  outcome.hidden = true;
  if (googleDecision) say(outcome, googleDecision.text, googleDecision.tone);
  body.appendChild(outcome);
  const items = pending?.google ?? [];
  if (!pending) {
    body.appendChild(note(t("No answer from the server")));
  } else if (items.length === 0) {
    body.appendChild(note(t("Nothing is waiting.")));
  }
  const ttl = data?.ttl_sec ?? 0;
  for (const item of items) {
    const row = el("div", "pending-item");
    // The sentence JARVIS said aloud, exactly. It is DATA: textContent only.
    row.appendChild(el("p", "pending-text", item.text));
    row.appendChild(el("p", "pending-age",
      `${ago(unit(item.age, "s"))} · ${windowLeft(ttl, item.age)}`));
    const confirm = button(t("Confirm"), "primary");
    const reject = button(t("Reject"));
    row.appendChild(actions(...(canConfirm ? [confirm, reject] : [reject])).root);
    const decide = (decision: "confirm" | "reject"): void => {
      confirm.disabled = true;
      reject.disabled = true;
      void decideProposal("google", item.id, decision).then((out) => {
        googleDecision = out;
        return refreshGoogle(host.closest(".view") as HTMLElement);
      });
    };
    confirm.addEventListener("click", () => decide("confirm"));
    reject.addEventListener("click", () => decide("reject"));
    body.appendChild(row);
  }
  host.appendChild(root);
}

function paintGoogleLog(host: HTMLElement, data: GoogleResponse | null): void {
  host.replaceChildren();
  const { root, body } = panel(t("Last actions"), t("the last 20"));
  if (!data || data.log.length === 0) {
    body.appendChild(note(t("Nothing has been attempted.")));
  } else {
    for (const line of data.log) {
      const suffix = line.dryrun ? ` (${t("dry run")})` : "";
      const via = line.confirmed_by === "none" ? "" : ` · ${line.confirmed_by}`;
      body.appendChild(readout(`${line.ts} · ${line.kind}${via}`, `${line.result}${suffix}`,
        toneFor(line.result === "ok" || line.result === "dryrun"
          || line.result === "proposed" || line.result === "rejected")));
    }
  }
  host.appendChild(root);
}

/** The three parts of the Google view, found by id inside it. */
function googleHosts(view: HTMLElement): { status: HTMLElement; pending: HTMLElement; log: HTMLElement } | null {
  const status = view.querySelector<HTMLElement>(".g-status");
  const pending = view.querySelector<HTMLElement>(".g-pending");
  const log = view.querySelector<HTMLElement>(".g-log");
  return status && pending && log ? { status, pending, log } : null;
}

async function refreshGoogle(view: HTMLElement | null): Promise<void> {
  if (!view) return;
  const hosts = googleHosts(view);
  if (!hosts) return;
  let data: GoogleResponse | null = null;
  let pending: PendingResponse | null = null;
  try { data = await apiGet<GoogleResponse>("/api/admin/google"); } catch { data = null; }
  try { pending = await apiGet<PendingResponse>("/api/bridge/pending"); } catch { pending = null; }
  paintGoogleStatus(hosts.status, data);
  paintGoogleLog(hosts.log, data);
  paintGooglePending(hosts.pending, data, pending);
}

function renderGoogle(view: HTMLElement): void {
  view.replaceChildren();
  for (const name of ["g-status", "g-pending", "g-log"]) view.appendChild(el("div", name));
  if (googleTimer !== null) window.clearInterval(googleTimer);
  void refreshGoogle(view);
  googleTimer = window.setInterval(() => {
    // Only while somebody is looking at this section of a visible tab.
    if (!view.isConnected || document.hidden || view.hidden) return;
    const hosts = googleHosts(view);
    if (!hosts) return;
    // Never repaint under a pointer or a keyboard focus about to press a
    // button: a different proposal could take the place of the one that was
    // read, and the click would then decide something never read.
    if (hosts.pending.matches(":hover") || hosts.pending.contains(document.activeElement)) return;
    void refreshGoogle(view);
  }, PENDING_REFRESH_MS);
}

// ---------------------------------------------------------------------------
// Section 7 — System
// ---------------------------------------------------------------------------

function renderSystem(view: HTMLElement, data: SystemResponse | null): void {
  view.replaceChildren();
  const { root, body } = panel(t("System"));
  if (!data) {
    body.appendChild(note(t("No answer from the server")));
    view.appendChild(root);
    return;
  }
  body.appendChild(readout(t("Version"), data.version));
  body.appendChild(readout(t("Uptime"), formatUptime(data.uptime_seconds)));
  body.appendChild(readout(t("Started by"),
    data.service === "systemd" ? t("the system service") : t("hand")));
  body.appendChild(readout(t("Voice in use"), providerLabel(data.voice_provider)));
  body.appendChild(readout(t("Language"), languageLabel(data.language)));
  body.appendChild(readout(t("Listening"), sttLabel(data.listening.mode)));
  body.appendChild(readout(t("Wake word"), wakeLabel(data.listening.wake === "openwakeword"
    ? "on" : data.listening.wake)));
  body.appendChild(readout(t("Local model"), data.listening.model));
  if (data.listening.warnings.length > 0) {
    body.appendChild(el("h3", "subhead", t("In the way")));
    body.appendChild(lines(data.listening.warnings, ""));
  }
  view.appendChild(root);

  const restart = panel(t("Restart"));
  const go = button(t("Restart Server"), "primary");
  const bar = actions(go);
  restart.body.appendChild(note(t("The settings marked \"needs a restart\" take effect after this. The page reloads on its own.")));
  restart.body.appendChild(bar.root);
  go.addEventListener("click", () => {
    void (async () => {
      go.disabled = true;
      say(bar.result, t("restarting..."));
      try {
        await fetch("/api/restart", { method: "POST" });
        setTimeout(() => window.location.reload(), 4000);
      } catch {
        go.disabled = false;
        say(bar.result, t("restart failed"), "tone-bad");
      }
    })();
  });
  view.appendChild(restart.root);
}

// ---------------------------------------------------------------------------
// The page
// ---------------------------------------------------------------------------

const VIEWS = ["profile", "voice", "listening", "connections", "house", "google", "system"] as const;
type ViewName = (typeof VIEWS)[number];

const banner = document.getElementById("admin-banner");

function warn(message: string): void {
  if (!banner) return;
  banner.textContent = message;
  banner.hidden = !message;
}

function viewEl(name: ViewName): HTMLElement | null {
  return document.getElementById(`view-${name}`);
}

function show(name: ViewName): void {
  for (const other of VIEWS) {
    const node = viewEl(other);
    if (node) node.hidden = other !== name;
    const tab = document.getElementById(`tab-${other}`);
    if (tab) {
      tab.classList.toggle("active", other === name);
      tab.setAttribute("aria-selected", other === name ? "true" : "false");
    }
  }
}

async function load(): Promise<void> {
  let failures = 0;

  let settings: SettingsResponse | null = null;
  try {
    settings = await apiGet<SettingsResponse>("/api/admin/settings");
  } catch { failures++; }

  const profile = viewEl("profile");
  const listening = viewEl("listening");
  if (settings) {
    if (profile) renderProfile(profile, settings);
    if (listening) renderListening(listening, settings);
  } else {
    for (const node of [profile, listening]) {
      if (!node) continue;
      node.replaceChildren();
      node.appendChild(note(t("No answer from the server")));
    }
  }

  let status: StatusResponse | null = null;
  try {
    status = await apiGet<StatusResponse>("/api/settings/status");
  } catch { failures++; }
  const voice = viewEl("voice");
  if (voice) renderVoice(voice, status);

  let connections: ConnectionsResponse | null = null;
  try {
    connections = await apiGet<ConnectionsResponse>("/api/admin/connections");
  } catch { failures++; }
  const connectionsView = viewEl("connections");
  if (connectionsView) renderConnections(connectionsView, connections);

  let house: HomeResponse | null = null;
  try {
    house = await apiGet<HomeResponse>("/api/admin/home");
  } catch { failures++; }
  const houseView = viewEl("house");
  if (houseView) renderHouse(houseView, house);

  const googleView = viewEl("google");
  if (googleView) renderGoogle(googleView);

  let system: SystemResponse | null = null;
  try {
    system = await apiGet<SystemResponse>("/api/admin/system");
  } catch { failures++; }
  const systemView = viewEl("system");
  if (systemView) renderSystem(systemView, system);

  // A section that could not be read says so in its own place; the banner is
  // for the case where the server is simply not there.
  warn(failures >= 4 ? t("No answer from the server") : "");
}

for (const name of VIEWS) {
  document.getElementById(`tab-${name}`)?.addEventListener("click", () => show(name));
}
show("profile");
void load();
