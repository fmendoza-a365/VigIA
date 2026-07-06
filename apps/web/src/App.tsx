import {
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  Clock3,
  Database,
  FileText,
  Mic,
  MicOff,
  Pause,
  Play,
  Radio,
  Send,
  ShieldCheck,
  Sparkles,
  Volume2,
  VolumeX,
} from "lucide-react";
import type { CSSProperties, ReactNode } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { AnalysisResult, BiSnapshot, TranscriptLine } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8080";

type SessionStatus = "idle" | "listening" | "paused" | "closed";

type SpeechPayload = {
  speaker: string;
  role: string;
  text: string;
};

type StreamTranscriptMessage = {
  type: "transcript";
  text: string;
  is_final: boolean;
  confidence: number | null;
};

type StreamStatusMessage =
  | { type: "ready" }
  | { type: "error"; message: string }
  | StreamTranscriptMessage;

type BrowserSpeechRecognition = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult: ((event: BrowserSpeechRecognitionEvent) => void) | null;
  onend: (() => void) | null;
  onerror: ((event: { error?: string }) => void) | null;
};

type BrowserSpeechRecognitionConstructor = new () => BrowserSpeechRecognition;

type BrowserSpeechRecognitionEvent = {
  resultIndex: number;
  results: ArrayLike<{
    isFinal: boolean;
    0: {
      transcript: string;
    };
  }>;
};

declare global {
  interface Window {
    SpeechRecognition?: BrowserSpeechRecognitionConstructor;
    webkitSpeechRecognition?: BrowserSpeechRecognitionConstructor;
  }
}

const demoLines: SpeechPayload[] = [
  {
    speaker: "Gerente BCP",
    role: "Gerente Operaciones",
    text: "BCP esta estable, el SLA esta normal y sin riesgo.",
  },
  {
    speaker: "Controller",
    role: "Finanzas",
    text: "El margen bruto de Claro esta en 21.6%, dentro de rango.",
  },
  {
    speaker: "Gerente Entel",
    role: "Gerente Operaciones",
    text: "Entel cumple con SLA de 95%, podemos cerrar sin acciones.",
  },
];

export function App() {
  const [speaker, setSpeaker] = useState("Sala");
  const [role, setRole] = useState("Reunión");
  const [text, setText] = useState("");
  const [interimText, setInterimText] = useState("");
  const [transcript, setTranscript] = useState<TranscriptLine[]>([]);
  const [alerts, setAlerts] = useState<AnalysisResult[]>([]);
  const [snapshot, setSnapshot] = useState<BiSnapshot | null>(null);
  const [sessionStatus, setSessionStatus] = useState<SessionStatus>("idle");
  const [sessionStartedAtIso, setSessionStartedAtIso] = useState<string | null>(null);
  const [sessionEndedAtIso, setSessionEndedAtIso] = useState<string | null>(null);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [minutes, setMinutes] = useState("");
  const [apiState, setApiState] = useState("conectando");
  const [micState, setMicState] = useState("sin iniciar");
  const [sttProvider, setSttProvider] = useState("Google Speech");
  const [audioLevel, setAudioLevel] = useState(0);
  const [hasAudioInput, setHasAudioInput] = useState(false);
  const [audioPeak, setAudioPeak] = useState(0);
  const [pendingIntervention, setPendingIntervention] = useState<AnalysisResult | null>(null);
  const [speaking, setSpeaking] = useState(false);

  const recognitionRef = useRef<BrowserSpeechRecognition | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const recorderTimerRef = useRef<number | null>(null);
  const sessionTimerRef = useRef<number | null>(null);
  const sessionStartedAtIsoRef = useRef<string | null>(null);
  const activeStartedAtRef = useRef<number | null>(null);
  const accumulatedMsRef = useRef(0);
  const streamSocketRef = useRef<WebSocket | null>(null);
  const streamClosingRef = useRef(false);
  const streamSendQueueRef = useRef<Promise<void>>(Promise.resolve());
  const audioContextRef = useRef<AudioContext | null>(null);
  const audioMeterFrameRef = useRef<number | null>(null);
  const chunkPeakRef = useRef(0);
  const chunkHadAudioRef = useRef(false);
  const lastFinalTextRef = useRef("");
  const listeningRef = useRef(false);

  const speechSupported =
    typeof window !== "undefined" && Boolean(window.SpeechRecognition ?? window.webkitSpeechRecognition);

  useEffect(() => {
    fetch(`${API_BASE}/api/bi-snapshot`)
      .then((response) => response.json())
      .then((data) => {
        setSnapshot(data);
        setApiState("conectado");
      })
      .catch(() => setApiState("sin conexion"));

    fetch(`${API_BASE}/api/speech-status`)
      .then((response) => response.json())
      .then((data: { configured: boolean; language: string }) => {
        setSttProvider(data.configured ? `Google STT ${data.language}` : "Google STT sin credencial");
      })
      .catch(() => setSttProvider("Google STT no disponible"));

    return () => {
      listeningRef.current = false;
      stopSessionTicker();
      stopGoogleRecorder(true);
      recognitionRef.current?.abort();
      window.speechSynthesis?.cancel();
    };
  }, []);

  const criticalCount = useMemo(
    () => alerts.filter((alert) => alert.severity === "critical").length,
    [alerts],
  );

  const sessionLabel = {
    idle: "lista",
    listening: "escuchando",
    paused: "pausada",
    closed: "cerrada",
  }[sessionStatus];

  const audioActive = sessionStatus === "listening" && hasAudioInput;
  const elapsedLabel = formatDuration(elapsedMs);

  async function submitLine(payload: SpeechPayload) {
    if (!payload.text.trim() || sessionStatus === "closed") return;

    const line: TranscriptLine = {
      ...payload,
      timestamp: new Date().toLocaleTimeString("es-PE", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      }),
    };

    setTranscript((current) => [...current, line]);
    setText("");

    try {
      const response = await fetch(`${API_BASE}/api/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = (await response.json()) as AnalysisResult;
      if (result.should_respond) {
        setAlerts((current) => [result, ...current]);
        if (result.severity === "critical") {
          setPendingIntervention(result);
        }
      }
      setApiState("conectado");
    } catch {
      setApiState("sin conexion");
      setAlerts((current) => [
        {
          should_respond: true,
          severity: "warning",
          category: "api_unavailable",
          message: "API no disponible. El contraste se activa cuando el backend responde en 127.0.0.1:8080.",
          evidence: [],
        },
        ...current,
      ]);
    }
  }

  async function startMeeting() {
    const isResuming = sessionStatus === "paused";
    if (sessionStatus === "closed") {
      setTranscript([]);
      setAlerts([]);
      setPendingIntervention(null);
      setMinutes("");
    }
    setSessionStatus("listening");
    listeningRef.current = true;

    try {
      const stream = await navigator.mediaDevices?.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      if (!stream) {
        setMicState("micrófono no disponible");
        setSessionStatus(isResuming ? "paused" : "idle");
        listeningRef.current = false;
        return;
      }
      mediaStreamRef.current = stream;
      startSessionClock({ reset: !isResuming });
      startAudioMeter(stream);

      if (typeof MediaRecorder !== "undefined") {
        setMicState("conectando stream Google");
        setSttProvider("Google STT en vivo");
        startGoogleRecorder(stream);
        return;
      }

      if (speechSupported) {
        setMicState("micrófono activo");
        setSttProvider("Reconocimiento del navegador");
        startRecognition("Navegador STT");
        return;
      }

      setMicState("voz no soportada");
    } catch {
      setSessionStatus(isResuming ? "paused" : "idle");
      listeningRef.current = false;
      setMicState("permiso denegado");
    }
  }

  function startSessionClock({ reset }: { reset: boolean }) {
    const now = Date.now();
    if (reset || !sessionStartedAtIsoRef.current) {
      const startedAtIso = new Date(now).toISOString();
      sessionStartedAtIsoRef.current = startedAtIso;
      accumulatedMsRef.current = 0;
      setSessionStartedAtIso(startedAtIso);
      setSessionEndedAtIso(null);
      setElapsedMs(0);
    }

    activeStartedAtRef.current = now;
    setElapsedMs(getCurrentElapsedMs());
    startSessionTicker();
  }

  function startSessionTicker() {
    stopSessionTicker();
    sessionTimerRef.current = window.setInterval(() => {
      setElapsedMs(getCurrentElapsedMs());
    }, 1000);
  }

  function stopSessionTicker() {
    if (sessionTimerRef.current !== null) {
      window.clearInterval(sessionTimerRef.current);
      sessionTimerRef.current = null;
    }
  }

  function getCurrentElapsedMs() {
    if (activeStartedAtRef.current === null) {
      return accumulatedMsRef.current;
    }
    return accumulatedMsRef.current + Date.now() - activeStartedAtRef.current;
  }

  function pauseSessionClock() {
    accumulatedMsRef.current = getCurrentElapsedMs();
    activeStartedAtRef.current = null;
    stopSessionTicker();
    setElapsedMs(accumulatedMsRef.current);
  }

  function closeSessionClock(endedAtIso: string) {
    const finalElapsedMs = getCurrentElapsedMs();
    accumulatedMsRef.current = finalElapsedMs;
    activeStartedAtRef.current = null;
    stopSessionTicker();
    setElapsedMs(finalElapsedMs);
    setSessionEndedAtIso(endedAtIso);
    return finalElapsedMs;
  }

  function getStreamingSocketUrl(mimeType: string) {
    const url = new URL(API_BASE);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.pathname = "/api/transcribe-stream";
    url.searchParams.set("mime_type", mimeType);
    return url.toString();
  }

  function getRecorderMimeType() {
    const supportedTypes = [
      "audio/webm;codecs=opus",
      "audio/ogg;codecs=opus",
      "audio/webm",
      "audio/ogg",
    ];
    return supportedTypes.find((mimeType) => MediaRecorder.isTypeSupported(mimeType)) ?? "";
  }

  function startGoogleRecorder(stream: MediaStream) {
    const mimeType = getRecorderMimeType();
    const socket = new WebSocket(getStreamingSocketUrl(mimeType || "audio/webm;codecs=opus"));
    streamSocketRef.current = socket;
    streamClosingRef.current = false;
    streamSendQueueRef.current = Promise.resolve();
    lastFinalTextRef.current = "";
    chunkHadAudioRef.current = false;
    chunkPeakRef.current = 0;

    socket.onopen = () => {
      if (!listeningRef.current) return;
      let recorder: MediaRecorder;
      try {
        recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      } catch {
        setMicState("formato de audio no soportado");
        startRecognitionFallback();
        socket.close();
        return;
      }

      recorder.ondataavailable = (event) => {
        if (!listeningRef.current || event.data.size === 0 || socket.readyState !== WebSocket.OPEN) {
          return;
        }
        streamSendQueueRef.current = streamSendQueueRef.current
          .then(async () => {
            const buffer = await event.data.arrayBuffer();
            if (socket.readyState === WebSocket.OPEN) {
              socket.send(buffer);
            }
          })
          .catch(() => setMicState("audio no enviado"));
      };

      recorder.onerror = () => {
        setMicState("error capturando audio");
        startRecognitionFallback();
      };

      try {
        recorder.start(400);
        mediaRecorderRef.current = recorder;
        setMicState("stream Google activo");
      } catch {
        setMicState("grabación no iniciada");
        startRecognitionFallback();
        socket.close();
      }
    };

    socket.onmessage = (event) => {
      let data: StreamStatusMessage;
      try {
        data = JSON.parse(event.data as string) as StreamStatusMessage;
      } catch {
        setMicState("respuesta STT inválida");
        return;
      }
      if (data.type === "ready") {
        setMicState("stream Google listo");
        return;
      }

      if (data.type === "error") {
        setMicState(data.message);
        stopStreamingTransport();
        startRecognitionFallback();
        return;
      }

      handleStreamTranscript(data);
    };

    socket.onerror = () => {
      setMicState("error stream Google");
      stopStreamingTransport();
      startRecognitionFallback();
    };

    socket.onclose = () => {
      streamSocketRef.current = null;
      if (!streamClosingRef.current && listeningRef.current) {
        setMicState("stream Google desconectado");
        startRecognitionFallback();
      }
    };
  }

  function handleStreamTranscript(data: StreamTranscriptMessage) {
    const transcriptText = data.text.trim();
    if (!transcriptText) return;

    if (!data.is_final) {
      setInterimText(transcriptText);
      setMicState("transcribiendo en vivo");
      return;
    }

    const normalized = transcriptText.toLocaleLowerCase("es-PE");
    if (normalized === lastFinalTextRef.current) return;

    lastFinalTextRef.current = normalized;
    setInterimText("");
    setMicState("frase transcrita");
    void submitLine({
      speaker: "Sala",
      role: "Google STT Streaming",
      text: transcriptText,
    });
  }

  function startAudioMeter(stream: MediaStream) {
    stopAudioMeter();

    const AudioContextConstructor = window.AudioContext;
    if (!AudioContextConstructor) return;

    const audioContext = new AudioContextConstructor();
    audioContext.resume().catch(() => undefined);
    const source = audioContext.createMediaStreamSource(stream);
    const analyser = audioContext.createAnalyser();
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.65;
    source.connect(analyser);

    const data = new Uint8Array(analyser.fftSize);
    audioContextRef.current = audioContext;

    const tick = () => {
      analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (const value of data) {
        const centered = (value - 128) / 128;
        sum += centered * centered;
      }
      const rms = Math.sqrt(sum / data.length);
      const normalized = Math.min(1, rms * 9);
      const active = normalized > 0.025;

      setAudioLevel(normalized);
      setHasAudioInput(active);
      setAudioPeak((currentPeak) => Math.max(normalized, currentPeak * 0.88));
      chunkPeakRef.current = Math.max(chunkPeakRef.current, normalized);
      if (active) {
        chunkHadAudioRef.current = true;
        setMicState((current) =>
          current === "transcribiendo en vivo" ||
          current === "stream Google activo" ||
          current === "stream Google listo" ||
          current === "frase transcrita"
            ? current
            : "escuchando voz",
        );
      } else if (listeningRef.current) {
        setMicState((current) =>
          current === "transcribiendo en vivo" || current === "stream Google activo" || current === "stream Google listo"
            ? current
            : "micrófono activo",
        );
      }

      audioMeterFrameRef.current = window.requestAnimationFrame(tick);
    };

    tick();
  }

  function stopAudioMeter() {
    if (audioMeterFrameRef.current !== null) {
      window.cancelAnimationFrame(audioMeterFrameRef.current);
      audioMeterFrameRef.current = null;
    }
    audioContextRef.current?.close().catch(() => undefined);
    audioContextRef.current = null;
    setAudioLevel(0);
    setAudioPeak(0);
    setHasAudioInput(false);
    chunkPeakRef.current = 0;
    chunkHadAudioRef.current = false;
  }

  function clearRecorderTimer() {
    if (recorderTimerRef.current !== null) {
      window.clearTimeout(recorderTimerRef.current);
      recorderTimerRef.current = null;
    }
  }

  function stopStreamingTransport() {
    clearRecorderTimer();
    streamClosingRef.current = true;
    const recorder = mediaRecorderRef.current;
    if (recorder && recorder.state === "recording") {
      recorder.stop();
    }
    mediaRecorderRef.current = null;

    const socket = streamSocketRef.current;
    if (socket) {
      if (socket.readyState === WebSocket.OPEN) {
        socket.send("stop");
      }
      socket.close();
    }
    streamSocketRef.current = null;
  }

  function stopGoogleRecorder(stopTracks: boolean) {
    stopStreamingTransport();

    if (stopTracks) {
      stopAudioMeter();
      mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
      mediaStreamRef.current = null;
    }
  }

  function startRecognitionFallback() {
    if (!speechSupported) {
      setSttProvider("Google STT en vivo");
      setMicState("STT en vivo no disponible");
      return;
    }
    if (recognitionRef.current) return;
    setSttProvider("Respaldo navegador");
    startRecognition("Navegador STT");
  }

  function startRecognition(transcriptRole: string) {
    const Recognition = window.SpeechRecognition ?? window.webkitSpeechRecognition;
    if (!Recognition || recognitionRef.current) return;

    const recognition = new Recognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = "es-PE";

    recognition.onresult = (event) => {
      let interim = "";
      let finalText = "";

      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const result = event.results[index];
        const transcriptText = result[0].transcript.trim();
        if (result.isFinal) {
          finalText += `${transcriptText} `;
        } else {
          interim += `${transcriptText} `;
        }
      }

      setInterimText(interim.trim());

      if (finalText.trim()) {
        void submitLine({
          speaker: "Sala",
          role: transcriptRole,
          text: finalText.trim(),
        });
      }
    };

    recognition.onerror = (event) => {
      setMicState(event.error ? `error: ${event.error}` : "error de micrófono");
    };

    recognition.onend = () => {
      recognitionRef.current = null;
      if (listeningRef.current && streamSocketRef.current === null) {
        try {
          recognition.start();
          recognitionRef.current = recognition;
        } catch {
          setMicState("reconectando");
        }
      }
    };

    recognitionRef.current = recognition;
    try {
      recognition.start();
    } catch {
      setMicState("no iniciado");
    }
  }

  function pauseMeeting() {
    listeningRef.current = false;
    pauseSessionClock();
    stopGoogleRecorder(true);
    recognitionRef.current?.stop();
    recognitionRef.current = null;
    setSessionStatus("paused");
    setInterimText("");
    setMicState("pausado");
  }

  function resumeMeeting() {
    void startMeeting();
  }

  async function closeSession() {
    const endedAtIso = new Date().toISOString();
    const finalElapsedMs = closeSessionClock(endedAtIso);
    listeningRef.current = false;
    stopGoogleRecorder(true);
    recognitionRef.current?.stop();
    recognitionRef.current = null;
    setSessionStatus("closed");
    setInterimText("");
    setMicState("cerrado");

    try {
      const response = await fetch(`${API_BASE}/api/end-session`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          meeting_type: "Comite ejecutivo A365",
          transcript,
          alerts,
          session_started_at: sessionStartedAtIsoRef.current,
          session_ended_at: endedAtIso,
          duration_seconds: Math.round(finalElapsedMs / 1000),
        }),
      });
      const result = await response.json();
      setMinutes(result.executive_minutes ?? "");
    } catch {
      setMinutes("# Minuta no generada\n\nLa API no esta disponible.");
    }
  }

  function speakIntervention() {
    if (!pendingIntervention) return;

    const evidence = pendingIntervention.evidence[0];
    const message = evidence
      ? `Permítanme precisar. Según la fuente oficial, ${evidence.campaign} tiene ${evidence.metric} en ${evidence.actual}${evidence.unit}, con meta ${evidence.target}${evidence.unit}. Sugiero validar este punto antes de decidir.`
      : pendingIntervention.message;

    try {
      window.speechSynthesis?.cancel();
      const utterance = new SpeechSynthesisUtterance(message);
      utterance.lang = "es-PE";
      utterance.rate = 0.95;
      utterance.onend = () => setSpeaking(false);
      utterance.onerror = () => setSpeaking(false);
      setSpeaking(true);
      window.speechSynthesis?.speak(utterance);
    } catch {
      setSpeaking(false);
    }
  }

  function stopSpeaking() {
    window.speechSynthesis?.cancel();
    setSpeaking(false);
  }

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">A365 VigIA</p>
          <h1>Sala en vivo</h1>
        </div>
        <div className="status-strip">
          <StatusPill label="API" value={apiState} tone={apiState === "conectado" ? "good" : "warn"} />
          <StatusPill label="Sesión" value={sessionLabel} tone={sessionStatus === "listening" ? "good" : "muted"} />
          <StatusPill label="Tiempo" value={elapsedLabel} tone={sessionStatus === "listening" ? "good" : "muted"} />
          <StatusPill label="Alertas" value={String(alerts.length)} tone={criticalCount > 0 ? "bad" : "good"} />
        </div>
      </header>

      <section className={`room-stage ${sessionStatus} ${audioActive ? "audio-active" : "audio-idle"}`}>
        <div className="stage-main">
          <div className="stage-heading">
            <div className="live-mark">
              {sessionStatus === "listening" ? <Radio size={24} /> : <Mic size={24} />}
            </div>
            <div>
              <span>{sttProvider} · {micState}</span>
              <h2>{sessionStatus === "listening" ? "VigIA está escuchando" : "VigIA está listo"}</h2>
            </div>
          </div>

          <div className={`voice-strip ${interimText ? "has-interim" : ""}`}>
            <div
              className="voice-bars"
              aria-hidden="true"
              style={{ "--audio-level": audioActive ? Math.max(audioLevel, audioPeak * 0.7) : 0 } as CSSProperties}
            >
              <span />
              <span />
              <span />
              <span />
              <span />
              <span />
              <span />
            </div>
            {interimText ? <p>{interimText}</p> : null}
          </div>

          {sessionStartedAtIso ? (
            <div className="session-timing">
              <Clock3 size={16} />
              <span>Inicio {formatClock(sessionStartedAtIso)}</span>
              <span>Duración {elapsedLabel}</span>
              {sessionEndedAtIso ? <span>Cierre {formatClock(sessionEndedAtIso)}</span> : null}
            </div>
          ) : null}

          <div className="session-actions">
            {sessionStatus === "idle" || sessionStatus === "closed" ? (
              <button className="primary" onClick={startMeeting}>
                <Play size={17} />
                Iniciar reunión
              </button>
            ) : null}
            {sessionStatus === "listening" ? (
              <button onClick={pauseMeeting}>
                <Pause size={17} />
                Pausar
              </button>
            ) : null}
            {sessionStatus === "paused" ? (
              <button className="primary" onClick={resumeMeeting}>
                <Play size={17} />
                Reanudar
              </button>
            ) : null}
            {(sessionStatus === "listening" || sessionStatus === "paused" || transcript.length > 0) && sessionStatus !== "closed" ? (
              <button className="danger" onClick={closeSession}>
                <FileText size={17} />
                Cerrar
              </button>
            ) : null}
          </div>
        </div>

        <aside className={`intervention-panel ${pendingIntervention ? "ready" : ""}`}>
          <SectionTitle
            icon={pendingIntervention ? <AlertTriangle size={18} /> : <ShieldCheck size={18} />}
            title="Intervención"
            badge={pendingIntervention ? "requiere decisión" : "sin pendiente"}
          />
          {pendingIntervention ? (
            <>
              <p>{pendingIntervention.message}</p>
              <div className="intervention-actions">
                <button className="primary" onClick={speakIntervention} disabled={speaking}>
                  <Volume2 size={16} />
                  Hablar
                </button>
                <button onClick={speaking ? stopSpeaking : () => setPendingIntervention(null)}>
                  {speaking ? <VolumeX size={16} /> : <MicOff size={16} />}
                  {speaking ? "Detener" : "Omitir"}
                </button>
              </div>
            </>
          ) : (
            <p className="quiet-text">Sin intervención pendiente.</p>
          )}
        </aside>
      </section>

      <section className="manual-card">
        <details>
          <summary>
            <Sparkles size={16} />
            Prueba manual
          </summary>
          <div className="manual-grid">
            <label>
              Orador
              <input value={speaker} onChange={(event) => setSpeaker(event.target.value)} />
            </label>
            <label>
              Rol
              <input value={role} onChange={(event) => setRole(event.target.value)} />
            </label>
            <label className="manual-text">
              Frase
              <textarea
                value={text}
                onChange={(event) => setText(event.target.value)}
                placeholder="Ej. BCP esta estable, el SLA esta normal y sin riesgo."
                rows={3}
              />
            </label>
            <div className="manual-actions">
              <button className="primary" onClick={() => submitLine({ speaker, role, text })}>
                <Send size={16} />
                Enviar
              </button>
              <button onClick={() => submitLine(demoLines[0])}>Ejemplo BCP</button>
            </div>
          </div>
        </details>
      </section>

      <section className="live-layout">
        <section className="panel transcript-panel">
          <SectionTitle icon={<Mic size={18} />} title="Transcripción" badge={`${transcript.length}`} />
          <div className="scroll">
            {transcript.length === 0 ? <Empty text="Sin frases registradas." /> : null}
            {transcript.map((line, index) => (
              <article className="line" key={`${line.timestamp}-${index}`}>
                <div>
                  <strong>{line.speaker}</strong>
                  <span>{line.timestamp}</span>
                </div>
                <small>{line.role}</small>
                <p>{line.text}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="panel vigia-panel">
          <SectionTitle
            icon={<AlertTriangle size={18} />}
            title="Contraste VigIA"
            badge={alerts.length === 0 ? "sin alerta" : `${alerts.length}`}
          />
          <div className="scroll">
            {alerts.length === 0 ? <Empty text="Sin contradicciones detectadas." /> : null}
            {alerts.map((alert, index) => (
              <article className={`alert ${alert.severity}`} key={`${alert.category}-${index}`}>
                <div className="alert-head">
                  {alert.severity === "critical" ? <AlertTriangle size={18} /> : <CheckCircle2 size={18} />}
                  <strong>{alert.severity === "critical" ? "Alerta crítica" : "Revisión"}</strong>
                </div>
                <p>{alert.message}</p>
                {alert.evidence.length > 0 ? (
                  <div className="evidence-list">
                    {alert.evidence.map((evidence) => (
                      <small key={`${evidence.campaign}-${evidence.metric}`}>
                        <span>{evidence.campaign}</span>
                        {evidence.metric}: {evidence.actual}{evidence.unit} / meta {evidence.target}{evidence.unit}
                      </small>
                    ))}
                  </div>
                ) : null}
              </article>
            ))}
          </div>
        </section>
      </section>

      <section className="source-panel">
        <SectionTitle icon={<Database size={18} />} title="Datos oficiales" badge={snapshot?.generated_at ?? "sin corte"} />
        <div className="source-grid">
          {snapshot?.campaigns.map((campaign) => (
            <article className="source-card" key={campaign.id}>
              <div>
                <strong>{campaign.name}</strong>
                <small>{campaign.owner}</small>
              </div>
              <MetricRow label="SLA" actual={campaign.metrics.sla.actual} target={campaign.metrics.sla.target} direction={campaign.metrics.sla.direction} />
              <MetricRow label="Margen" actual={campaign.metrics.gross_margin.actual} target={campaign.metrics.gross_margin.target} direction={campaign.metrics.gross_margin.direction} />
              <MetricRow label="Rotación" actual={campaign.metrics.attrition.actual} target={campaign.metrics.attrition.target} direction={campaign.metrics.attrition.direction} />
            </article>
          ))}
        </div>
      </section>

      {minutes ? (
        <section className="minutes">
          <SectionTitle icon={<FileText size={18} />} title="Minuta" badge={elapsedLabel} />
          <pre>{minutes}</pre>
        </section>
      ) : null}
    </main>
  );
}

function SectionTitle({ icon, title, badge }: { icon: ReactNode; title: string; badge: string }) {
  return (
    <div className="section-title">
      <div>
        {icon}
        <h2>{title}</h2>
      </div>
      <span>{badge}</span>
    </div>
  );
}

function MetricRow({
  label,
  actual,
  target,
  direction,
}: {
  label: string;
  actual: number;
  target: number;
  direction: "min" | "max";
}) {
  const compliant = direction === "min" ? actual >= target : actual <= target;
  return (
    <div className="metric-row">
      <span>{label}</span>
      <strong>{actual}%</strong>
      <em className={compliant ? "ok" : "risk"}>{compliant ? "OK" : "Riesgo"}</em>
    </div>
  );
}

function StatusPill({ label, value, tone }: { label: string; value: string; tone: "good" | "warn" | "bad" | "muted" }) {
  return (
    <div className={`status ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <p className="empty">{text}</p>;
}

function formatDuration(milliseconds: number) {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return [hours, minutes, seconds].map((value) => String(value).padStart(2, "0")).join(":");
}

function formatClock(isoValue: string) {
  return new Date(isoValue).toLocaleTimeString("es-PE", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
