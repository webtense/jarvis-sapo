/**
 * Local microphone: stream PCM to the server, which recognises it.
 *
 * Used instead of the Web Speech API when the server's config frame says
 * `stt: "local"` (JARVIS_STT=local). The page does no recognition at all: it
 * captures the microphone, turns it into 16 kHz mono signed 16-bit PCM in an
 * AudioWorklet and sends it down `/ws/mic`. The server finds the phrases (and
 * the wake word, if enabled), transcribes them with faster-whisper and feeds
 * the text into the same turn handling a browser final result goes through.
 * Nothing the server says back is ever treated as markup: it is only ever
 * handed to callbacks as strings.
 */
import { t } from "./i18n";


export interface LocalMic {
  start(): void;
  stop(): void;
  pause(): void;
  resume(): void;
}

export interface LocalMicHandlers {
  /** The server heard and transcribed a phrase (display / liveness only —
   *  the server already acted on it). */
  onHeard(text: string): void;
  onState(state: string): void;
  onError(msg: string): void;
  onEvent(event: string): void;
}

const TARGET_RATE = 16000;
const FRAME_SAMPLES = 1280;            // 80 ms, what the server slices to
const RECONNECT_MIN_MS = 1000;
const RECONNECT_MAX_MS = 15000;
// Past this much unsent audio the socket is not keeping up. Audio is dropped
// rather than queued: stale speech answered seconds later is worse than a gap.
const MAX_BUFFERED_BYTES = 256 * 1024;

// The worklet runs in its own global scope, so it is shipped as text and
// loaded from a Blob. It averages the input down to 16 kHz (the context runs
// at the device's rate, usually 48 kHz) and posts one Int16 frame at a time.
const WORKLET_SOURCE = `
class PcmTap extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / ${TARGET_RATE};
    this.pos = 0;
    this.acc = 0;
    this.n = 0;
    this.out = new Int16Array(${FRAME_SAMPLES});
    this.k = 0;
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      this.acc += ch[i];
      this.n++;
      this.pos += 1;
      if (this.pos >= this.ratio) {
        this.pos -= this.ratio;
        const v = Math.max(-1, Math.min(1, this.acc / this.n));
        this.acc = 0;
        this.n = 0;
        this.out[this.k++] = v < 0 ? v * 32768 : v * 32767;
        if (this.k === this.out.length) {
          const buf = this.out.buffer.slice(0);
          this.port.postMessage(buf, [buf]);
          this.k = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor("jarvis-pcm-tap", PcmTap);
`;

export function createLocalMic(h: LocalMicHandlers): LocalMic {
  let wanted = false;
  let paused = false;
  let ws: WebSocket | null = null;
  let reconnectDelay = RECONNECT_MIN_MS;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  let ctx: AudioContext | null = null;
  let stream: MediaStream | null = null;
  let capturing = false;
  // Bumped by stop(): a capture still opening when stop() ran must not go live.
  let generation = 0;

  function wsUrl(): string {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${window.location.host}/ws/mic`;
  }

  function connect() {
    if (!wanted || ws) return;
    const sock = new WebSocket(wsUrl());
    sock.binaryType = "arraybuffer";
    ws = sock;

    sock.onopen = () => {
      reconnectDelay = RECONNECT_MIN_MS;
      h.onEvent("local mic: connected");
    };
    sock.onmessage = (event) => {
      if (typeof event.data !== "string") return;
      let msg: Record<string, unknown>;
      try {
        msg = JSON.parse(event.data);
      } catch {
        return;
      }
      const type = msg.type;
      if (type === "transcript") h.onHeard(String(msg.text ?? ""));
      else if (type === "state") h.onState(String(msg.state ?? ""));
      else if (type === "error") h.onError(t(String(msg.text ?? "Local speech recognition error")));
    };
    sock.onclose = (ev) => {
      if (ws === sock) ws = null;
      if (!wanted) return;
      // 4001: another page took the microphone. Do not fight it for it.
      if (ev.code === 4001) {
        h.onEvent("local mic: another page took the microphone");
        return;
      }
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        connect();
      }, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
    };
    sock.onerror = () => sock.close();
  }

  function sendFrame(buf: ArrayBuffer) {
    if (paused || !ws || ws.readyState !== WebSocket.OPEN) return;
    if (ws.bufferedAmount > MAX_BUFFERED_BYTES) return;
    ws.send(buf);
  }

  async function startCapture() {
    if (capturing) return;
    capturing = true;
    const mine = generation;
    // Held in locals until the last check passes, so a capture that lost the
    // race against stop() cleans up only what it made itself.
    let myStream: MediaStream | null = null;
    let myCtx: AudioContext | null = null;
    const abandon = () => {
      myStream?.getTracks().forEach((track) => track.stop());
      myCtx?.close().catch(() => {});
    };
    try {
      // The browser's own echo cancellation is worth having here: JARVIS's
      // voice comes out of the same laptop the microphone is in.
      myStream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
      myCtx = new AudioContext();
      const url = URL.createObjectURL(new Blob([WORKLET_SOURCE], { type: "application/javascript" }));
      try {
        await myCtx.audioWorklet.addModule(url);
      } finally {
        URL.revokeObjectURL(url);
      }
      if (mine !== generation) { abandon(); return; }
      const source = myCtx.createMediaStreamSource(myStream);
      const tap = new AudioWorkletNode(myCtx, "jarvis-pcm-tap");
      tap.port.onmessage = (e: MessageEvent) => sendFrame(e.data as ArrayBuffer);
      // A worklet nobody pulls on may never run, so it is wired to the
      // destination through a gain of zero: pulled, and silent.
      const silent = myCtx.createGain();
      silent.gain.value = 0;
      source.connect(tap);
      tap.connect(silent);
      silent.connect(myCtx.destination);
      stream = myStream;
      ctx = myCtx;
      h.onEvent(`local mic: capturing at ${myCtx.sampleRate} Hz, sending ${TARGET_RATE} Hz`);
    } catch (e) {
      abandon();
      if (mine === generation) capturing = false;
      const name = e && (e as { name?: string }).name;
      h.onError(name === "NotAllowedError"
        ? t("Microphone blocked — check this origin's mic permission.")
        : t("Could not open the microphone."));
      h.onEvent(`local mic: capture failed: ${name}`);
    }
  }

  function releaseCapture() {
    stream?.getTracks().forEach((track) => track.stop());
    stream = null;
    if (ctx) {
      ctx.close().catch(() => {});
      ctx = null;
    }
    capturing = false;
  }

  return {
    start() {
      wanted = true;
      paused = false;
      connect();
      void startCapture();
    },
    stop() {
      generation++;
      wanted = false;
      paused = false;
      if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
      const sock = ws;
      ws = null;
      sock?.close();
      releaseCapture();
    },
    pause() {
      if (paused) return;
      paused = true;
      // The server drops a half-heard phrase rather than finishing it later
      // with whatever comes after the pause.
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "pause" }));
    },
    resume() {
      // Called constantly from the server's status frames: must be cheap and
      // idempotent, and must not revive a stopped microphone.
      paused = false;
    },
  };
}
