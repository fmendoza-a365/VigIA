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
  Settings,
  Users,
  MessageSquare,
} from "lucide-react";
import type { CSSProperties, ReactNode } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import type {
  AnalysisResult,
  BiSnapshot,
  TranscriptLine,
  VigIAResponse,
  SessionStatus,
  Participant,
  PostMeetingDocuments,
  DataSourceStatus,
  ExecutiveProfile,
  InterventionLevel,
  FinancialScopeSelection,
} from "./types";
import { API_BASE } from "./config";
import { FinancialScopeSelector } from "./FinancialScopeSelector";

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

type LiveMeetingMessage =
  | { type: "ready"; provider: string; model: string; playback?: "native" | "tts" }
  | { type: "error"; message: string }
  | { type: "input_transcript"; text: string; is_final: boolean; confidence: number | null }
  | { type: "output_transcript"; text: string; is_final: boolean }
  | { type: "analysis"; result: VigIAResponse }
  | { type: "tool"; name: string; status: string }
  | { type: "interrupted" }
  | { type: "assistant_turn_complete" }
  | { type: "reconnecting"; time_left?: string };

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

const SENTIMENT_EMOJIS: Record<string, string> = {
  preocupado: "😟",
  frustrado: "😤",
  neutral: "😐",
  positivo: "🟢",
  conflictivo: "⚡",
};

function normalizeSpokenText(value: string) {
  return value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase("es-PE")
    .trim();
}

function prepareTextForSpeech(value: string) {
  const number = "([-+]?(?:\\d{1,3}(?:[.,]\\d{3})+|\\d+)(?:[.,]\\d+)?)";
  return value
    .replace(/!\[([^\]]*)\]\([^)]+\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/https?:\/\/\S+/gi, "")
    .replace(/^\s{0,3}(?:#{1,6}\s*|[-*+•]\s+|>\s*)/gm, "")
    .replace(/^\s*\d+[.)]\s+/gm, "")
    .replace(/[*_~`#]+/g, "")
    .replace(new RegExp(`S/\\s*${number}`, "gi"), "$1 soles")
    .replace(new RegExp(`${number}\\s*S/`, "gi"), "$1 soles")
    .replace(new RegExp(`${number}\\s*PEN\\b`, "gi"), "$1 soles")
    .replace(new RegExp(`${number}\\s*%`, "g"), "$1 por ciento")
    .replace(/[✓✔]/g, " cumple ")
    .replace(/[✗✘]/g, " no cumple ")
    .replace(/\s+/g, " ")
    .trim();
}

function resumeRecorderAfterSpeech(recorder: MediaRecorder | null, listening: boolean) {
  if (recorder?.state === "paused" && listening) recorder.resume();
}

function isDirectVoiceRequest(value: string) {
  const normalized = normalizeSpokenText(value);
  if (!normalized) return false;

  if (/[?¿]/.test(value)) return true;

  if (/\b(vigia|me escuchas|puedes escucharme|estas ahi|respondeme|hablame)\b/.test(normalized)) {
    return true;
  }

  if (
    /\b(quiero saber|quisiera saber|necesito saber|quiero conocer|me gustaria saber|puedes decirme|podrias decirme|dime|dame|indicame|explicame|cuentame|confirma|revisa|analiza)\b/.test(normalized)
  ) {
    return true;
  }

  if (/\b(cual|cuales|cuanto|cuanta|cuantos|cuantas|donde|por que)\b/.test(normalized)) {
    return true;
  }

  if (/\b(como (esta|estan|va|van)|que (es|son|valor|margen|facturacion|ingreso|penalidad|cuenta))\b/.test(normalized)) {
    return true;
  }

  return /^(que|cual|cuales|cuanto|cuanta|cuantos|cuantas|como esta|como va|dime|confirma|revisa|explicame|analiza|cuentame|necesito saber|puedes decirme)\b/.test(
    normalized.replace(/^[¿?¡!\s]+/, ""),
  );
}

export function App() {
  // Session state
  const [sessionStatus, setSessionStatus] = useState<SessionStatus>("idle");
  const [meetingType, setMeetingType] = useState("Comite ejecutivo");
  const [participants, setParticipants] = useState<Participant[]>([
    { name: "", role: "" },
  ]);
  const [voiceMode, setVoiceMode] = useState(true);
  const [executiveProfile, setExecutiveProfile] = useState<ExecutiveProfile>("balanced");
  const [interventionLevel, setInterventionLevel] = useState<InterventionLevel>("warning");
  const [financialScope, setFinancialScope] = useState<FinancialScopeSelection>({ accounts: [], campaigns: [] });

  // Transcript & analysis state
  const [speaker, setSpeaker] = useState("Sala");
  const [role, setRole] = useState("Reunión");
  const [text, setText] = useState("");
  const [interimText, setInterimText] = useState("");
  const [transcript, setTranscript] = useState<TranscriptLine[]>([]);
  const [alerts, setAlerts] = useState<AnalysisResult[]>([]);
  const [responses, setResponses] = useState<VigIAResponse[]>([]);
  const [snapshot, setSnapshot] = useState<BiSnapshot | null>(null);
  const [dataSourceStatus, setDataSourceStatus] = useState<DataSourceStatus | null>(null);
  const [sessionStartedAtIso, setSessionStartedAtIso] = useState<string | null>(null);
  const [sessionEndedAtIso, setSessionEndedAtIso] = useState<string | null>(null);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [documents, setDocuments] = useState<PostMeetingDocuments | null>(null);

  // UI state
  const [apiState, setApiState] = useState("conectando");
  const [micState, setMicState] = useState("sin iniciar");
  const [sttProvider, setSttProvider] = useState("Google Speech");
  const [audioLevel, setAudioLevel] = useState(0);
  const [hasAudioInput, setHasAudioInput] = useState(false);
  const [audioPeak, setAudioPeak] = useState(0);
  const [speaking, setSpeaking] = useState(false);
  const [currentSentiment, setCurrentSentiment] = useState<string>("😐");
  const [question, setQuestion] = useState("");
  const [vigiaResponse, setVigiaResponse] = useState("");
  const [lastHeardText, setLastHeardText] = useState("");
  const [lastSpeechConfidence, setLastSpeechConfidence] = useState<number | null>(null);
  const [understandingStatus, setUnderstandingStatus] = useState("Esperando la primera frase");
  const [understandingTone, setUnderstandingTone] = useState<"idle" | "listening" | "processing" | "understood" | "warning" | "error">("idle");
  const [voiceOutputStatus, setVoiceOutputStatus] = useState("Voz lista");

  // Refs
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
  const transcriptScrollRef = useRef<HTMLDivElement | null>(null);
  const alertsScrollRef = useRef<HTMLDivElement | null>(null);

  // Silence detection refs
  const silenceTimerRef = useRef<number | null>(null);
  const pendingTextsRef = useRef<string[]>([]);
  const ANALYSIS_DELAY = 700; // Short debounce before analysis, transcript stays live.

  // TTS queue refs
  const ttsQueueRef = useRef<string[]>([]);
  const ttsPlayingRef = useRef(false);
  const currentAudioRef = useRef<HTMLAudioElement | null>(null);
  const lastAssistantSpeechRef = useRef<{ text: string; expiresAt: number } | null>(null);
  const liveSocketRef = useRef<WebSocket | null>(null);
  const liveClosingRef = useRef(false);
  const liveFallbackStartedRef = useRef(false);
  const liveReconnectTimerRef = useRef<number | null>(null);
  const liveInputContextRef = useRef<AudioContext | null>(null);
  const liveInputSourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const liveProcessorRef = useRef<ScriptProcessorNode | null>(null);
  const livePlaybackContextRef = useRef<AudioContext | null>(null);
  const livePlaybackSourcesRef = useRef<Set<AudioBufferSourceNode>>(new Set());
  const liveNextPlaybackAtRef = useRef(0);
  const livePlaybackModeRef = useRef<"native" | "tts">("tts");
  const assistantAudioActiveRef = useRef(false);

  const speechSupported =
    typeof window !== "undefined" && Boolean(window.SpeechRecognition ?? window.webkitSpeechRecognition);

  useEffect(() => {
    const refreshFinancialData = () => {
      fetch(`${API_BASE}/api/bi-snapshot`)
        .then((response) => {
          if (!response.ok) throw new Error("financial source unavailable");
          return response.json();
        })
        .then((data) => {
          setSnapshot(data);
          setApiState("conectado");
        })
        .catch(() => setApiState("sin datos"));

      fetch(`${API_BASE}/api/data-source/status`)
        .then((response) => response.json())
        .then((data: DataSourceStatus) => setDataSourceStatus(data))
        .catch(() => setDataSourceStatus(null));
    };

    refreshFinancialData();
    const financialRefreshTimer = window.setInterval(refreshFinancialData, 60_000);

    fetch(`${API_BASE}/api/speech-status`)
      .then((response) => response.json())
      .then((data: { configured: boolean; language: string }) => {
        setSttProvider(data.configured ? `Google STT ${data.language}` : "Google STT sin credencial");
      })
      .catch(() => setSttProvider("Google STT no disponible"));

    return () => {
      window.clearInterval(financialRefreshTimer);
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
    active: "escuchando",
    paused: "pausada",
    closed: "cerrada",
  }[sessionStatus];

  const audioActive = sessionStatus === "active" && hasAudioInput;
  const elapsedLabel = formatDuration(elapsedMs);
  // Auto-scroll transcript and alerts to bottom
  useEffect(() => {
    if (transcriptScrollRef.current) {
      transcriptScrollRef.current.scrollTop = transcriptScrollRef.current.scrollHeight;
    }
  }, [transcript, interimText]);

  useEffect(() => {
    if (alertsScrollRef.current) {
      alertsScrollRef.current.scrollTop = alertsScrollRef.current.scrollHeight;
    }
  }, [alerts]);

  // ─────────────────────────────────────────────────────────────────────────
  // SESSION MANAGEMENT
  // ─────────────────────────────────────────────────────────────────────────

  async function startSession() {
    try {
      const response = await fetch(`${API_BASE}/api/session/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          meeting_type: meetingType,
          date: new Date().toISOString(),
          participants: participants.filter((p) => p.name),
          voice_mode: voiceMode,
          executive_profile: executiveProfile,
          intervention_level: interventionLevel,
          financial_scope: financialScope,
        }),
      });
      if (!response.ok) {
        throw new Error("No se pudo iniciar la sesión con una fuente financiera válida");
      }
      const result = await response.json();
      if (result.data_source) {
        setDataSourceStatus(result.data_source as DataSourceStatus);
      }
      setSessionStatus("active");
      setSessionStartedAtIso(new Date().toISOString());
      setTranscript([]);
      setAlerts([]);
      setResponses([]);
      setDocuments(null);

      // Add VigIA response
      const vigiaMsg: VigIAResponse = {
        tag: "🟢",
        message: "VIGIA conectado.",
        should_respond: true,
        severity: "info",
        category: "session_start",
        sentiment: null,
        voice_intervention: false,
        evidence: [],
      };
      setResponses([vigiaMsg]);

      // Start listening
      await startListening();
    } catch (error) {
      console.error("Error starting session:", error);
    }
  }

  async function pauseSession() {
    try {
      await fetch(`${API_BASE}/api/session/pause`, { method: "POST" });
      setSessionStatus("paused");
      listeningRef.current = false;
      stopGoogleRecorder(true);
      recognitionRef.current?.stop();
      recognitionRef.current = null;
      freezeSessionClock();
      setMicState("pausado");
    } catch (error) {
      console.error("Error pausing session:", error);
    }
  }

  async function resumeSession() {
    try {
      await fetch(`${API_BASE}/api/session/resume`, { method: "POST" });
      setSessionStatus("active");
      await startListening();
    } catch (error) {
      console.error("Error resuming session:", error);
    }
  }

  async function closeSession() {
    const endedAtIso = new Date().toISOString();
    const finalElapsedMs = getCurrentElapsedMs();
    freezeSessionClock();
    listeningRef.current = false;
    stopGoogleRecorder(true);
    recognitionRef.current?.stop();
    recognitionRef.current = null;
    setSessionStatus("closed");
    setInterimText("");
    setMicState("cerrado");
    setSessionEndedAtIso(endedAtIso);

    try {
      const response = await fetch(`${API_BASE}/api/end-session`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          meeting_type: meetingType,
          transcript,
          alerts: alerts.map((a) => ({
            message: a.message,
            severity: a.severity,
            category: a.category,
          })),
          session_started_at: sessionStartedAtIsoRef.current,
          session_ended_at: endedAtIso,
          duration_seconds: Math.round(finalElapsedMs / 1000),
        }),
      });
      const result = await response.json();
      setDocuments(result.documents);
    } catch {
      setDocuments({
        executive_minutes: "# Error\n\nNo se pudieron generar los documentos.",
        financial_summary: "",
        sentiment_report: "",
        structured_transcript: "",
      });
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  // LISTENING & TRANSCRIPTION
  // ─────────────────────────────────────────────────────────────────────────

  async function startListening() {
    listeningRef.current = true;
    const existingLiveSocket = liveSocketRef.current;
    if (
      existingLiveSocket
      && (existingLiveSocket.readyState === WebSocket.CONNECTING || existingLiveSocket.readyState === WebSocket.OPEN)
    ) {
      setSttProvider("Gemini Live · audio nativo");
      setMicState("escuchando en tiempo real");
      return;
    }
    try {
      const stream = await navigator.mediaDevices?.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
        },
      });
      if (!stream) {
        listeningRef.current = false;
        setMicState("micrófono no disponible");
        return;
      }
      mediaStreamRef.current = stream;
      startSessionClock({ reset: true });
      startAudioMeter(stream);

      if (typeof WebSocket !== "undefined" && window.AudioContext) {
        setMicState("conectando voz en tiempo real");
        setSttProvider("Gemini Live");
        startGeminiLive(stream);
        return;
      }

      if (speechSupported) {
        setMicState("micrófono activo");
        setSttProvider("Reconocimiento del navegador");
        startRecognition("Navegador STT");
        return;
      }

      setMicState("voz no soportada");
      listeningRef.current = false;
    } catch {
      listeningRef.current = false;
      setMicState("permiso denegado");
    }
  }

  function handleStreamTranscript(data: StreamTranscriptMessage, shouldAnalyze = true) {
    const transcriptText = data.text.trim();
    if (!transcriptText) return;

    clearRecorderTimer();
    chunkHadAudioRef.current = false;

    if (!data.is_final) {
      setInterimText(transcriptText);
      setMicState("transcribiendo en vivo");
      setUnderstandingStatus("Escuchando tu voz…");
      setUnderstandingTone("listening");
      return;
    }

    const assistantSpeech = lastAssistantSpeechRef.current;
    if (
      assistantSpeech
      && Date.now() < assistantSpeech.expiresAt
      && speechWordOverlap(transcriptText, assistantSpeech.text) >= 0.7
    ) {
      setInterimText("");
      setMicState("eco de VigIA descartado");
      return;
    }

    const normalized = transcriptText.toLocaleLowerCase("es-PE");
    if (normalized === lastFinalTextRef.current) return;

    lastFinalTextRef.current = normalized;
    setInterimText("");
    setMicState("frase transcrita");
    setLastHeardText(transcriptText);
    setLastSpeechConfidence(data.confidence);
    setUnderstandingStatus("Frase recibida · contrastando con los datos");
    setUnderstandingTone("processing");

    appendTranscriptLine(transcriptText, "Voz");
    if (shouldAnalyze) queueTextForAnalysis(transcriptText);
  }

  function appendTranscriptLine(spokenText: string, transcriptRole: string) {
    const timestamp = new Date().toLocaleTimeString("es-PE", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });

    setTranscript((current) => [
      ...current,
      {
        speaker: "Sala",
        role: transcriptRole,
        text: spokenText,
        timestamp,
      },
    ]);
  }

  function queueTextForAnalysis(spokenText: string) {
    pendingTextsRef.current.push(spokenText);

    // Reset silence timer
    if (silenceTimerRef.current) {
      clearTimeout(silenceTimerRef.current);
    }

    // Start new silence timer
    silenceTimerRef.current = window.setTimeout(() => {
      // Process all accumulated texts as one batch
      const fullText = pendingTextsRef.current.join(" ");
      pendingTextsRef.current = [];

      if (fullText.trim()) {
        void analyzeWithVigia(fullText);
      }
    }, ANALYSIS_DELAY);
  }

  async function analyzeWithVigia(spokenText: string) {
    // Show processing state
    setMicState("analizando...");

    // Analyze with VigIA
    try {
      const analyzeResponse = await fetch(`${API_BASE}/api/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ speaker: "Sala", role: "Voz", text: spokenText }),
      });
      if (!analyzeResponse.ok) {
        throw new Error("La fuente financiera no está disponible");
      }
      const result = (await analyzeResponse.json()) as VigIAResponse;
      const directVoiceRequest = isDirectVoiceRequest(spokenText);
      const isFactCheckAlert =
        result.should_respond
        && (result.severity === "warning" || result.severity === "critical");
      if (result.source_status) {
        setDataSourceStatus(result.source_status);
      }

      // Update sentiment
      if (result.sentiment) {
        setCurrentSentiment(result.sentiment);
      }

      if (directVoiceRequest && !isFactCheckAlert) {
        setUnderstandingStatus("Pregunta detectada · consultando seguimiento_financiero");
        setUnderstandingTone("processing");
      } else if (result.category === "claim_without_scope") {
        setUnderstandingStatus("Entendido · menciona la cuenta para poder verificarlo");
        setUnderstandingTone("warning");
      } else if (!result.should_respond) {
        setUnderstandingStatus("Entendido · sin contradicción financiera verificable");
        setUnderstandingTone("understood");
      } else if (result.severity === "warning" || result.severity === "critical") {
        setUnderstandingStatus("Contradicción detectada · intervención preparada");
        setUnderstandingTone("warning");
      } else {
        setUnderstandingStatus("Entendido · dato contrastado correctamente");
        setUnderstandingTone("understood");
      }

      let responseWasSpoken = false;

      if (result.should_respond && (!directVoiceRequest || isFactCheckAlert)) {
        // Add to responses
        setResponses((current) => [...current, result]);

        if (result.severity === "warning" || result.severity === "critical") {
          setAlerts((current) => [
            ...current,
            {
              should_respond: result.should_respond,
              severity: result.severity,
              category: result.category,
              message: result.message,
              tag: result.tag,
              sentiment: result.sentiment,
              voice_intervention: result.voice_intervention,
              evidence: result.evidence,
            },
          ]);
        }

        // Voice intervention is decided by the backend after checking the data.
        if (voiceMode && result.voice_intervention) {
          const message = result.message;
          setMicState("interviniendo por voz");
          await speakText(message);
          responseWasSpoken = true;
        }
      }

      // Spoken questions used to stop at the fact checker. Route direct requests
      // to the grounded financial Q&A when there is no verified alert to speak.
      if (directVoiceRequest && !responseWasSpoken) {
        if (isFactCheckAlert && result.message) {
          if (voiceMode && interventionLevel !== "manual") {
            setMicState("respondiendo consulta");
            await speakText(result.message);
          }
        } else {
          await answerSpokenQuestion(spokenText);
        }
      }

      setMicState("escuchando");
    } catch (error) {
      console.error("Error analyzing:", error);
      setMicState("error de análisis");
      setUnderstandingStatus("Escuché la frase, pero no pude contrastarla");
      setUnderstandingTone("error");
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  // VOICE & TTS
  // ─────────────────────────────────────────────────────────────────────────

  async function speakText(text: string) {
    // Add to queue (only keep last 2 items to avoid long queues)
    if (ttsQueueRef.current.length >= 2) {
      ttsQueueRef.current = ttsQueueRef.current.slice(-1);
    }
    ttsQueueRef.current.push(text);

    // If not already playing, start processing queue
    if (!ttsPlayingRef.current) {
      await processTTSQueue();
    }
  }

  async function processTTSQueue() {
    if (ttsPlayingRef.current || ttsQueueRef.current.length === 0) return;

    ttsPlayingRef.current = true;
    assistantAudioActiveRef.current = true;
    setSpeaking(true);
    setVoiceOutputStatus("Preparando respuesta…");

    const fallbackRecorder = mediaRecorderRef.current;
    const recorderPausedForSpeech = fallbackRecorder?.state === "recording";
    if (recorderPausedForSpeech) fallbackRecorder.pause();

    while (ttsQueueRef.current.length > 0) {
      const text = prepareTextForSpeech(ttsQueueRef.current.shift()!);
      if (!text) continue;
      lastAssistantSpeechRef.current = {
        text,
        expiresAt: Date.now() + Math.max(12_000, text.length * 85),
      };

      // Stop any currently playing audio
      if (currentAudioRef.current) {
        currentAudioRef.current.pause();
        currentAudioRef.current = null;
      }

      try {
        // Truncate text for faster TTS
        const truncatedText = text.length > 850 ? text.substring(0, 850) + "..." : text;

        const response = await fetch(`${API_BASE}/api/tts`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: truncatedText, speaking_rate: 1.0 }),
        });

        if (!response.ok) throw new Error("TTS failed");

        const result = await response.json();
        const audio = new Audio(`data:${result.content_type};base64,${result.audio}`);
        currentAudioRef.current = audio;
        setVoiceOutputStatus("VigIA está respondiendo");

        await new Promise<void>((resolve) => {
          audio.onended = () => {
            currentAudioRef.current = null;
            resolve();
          };
          audio.onerror = () => {
            currentAudioRef.current = null;
            // Fallback to browser TTS
            setVoiceOutputStatus("Respuesta por voz del navegador");
            speakWithBrowserTTS(truncatedText).then(resolve);
          };
          audio.play().catch(() => {
            currentAudioRef.current = null;
            // Fallback to browser TTS
            setVoiceOutputStatus("Respuesta por voz del navegador");
            speakWithBrowserTTS(truncatedText).then(resolve);
          });
        });
      } catch {
        // Fallback to browser TTS
        setVoiceOutputStatus("Respuesta por voz del navegador");
        await speakWithBrowserTTS(text);
      }
    }

    ttsPlayingRef.current = false;
    window.setTimeout(() => {
      assistantAudioActiveRef.current = false;
      if (recorderPausedForSpeech) resumeRecorderAfterSpeech(fallbackRecorder, listeningRef.current);
    }, 350);
    setSpeaking(false);
    setVoiceOutputStatus("Voz lista");
  }

  function speakWithBrowserTTS(text: string): Promise<void> {
    return new Promise((resolve) => {
      if (!window.speechSynthesis) {
        resolve();
        return;
      }
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = "es-ES";
      utterance.rate = 0.95;
      utterance.onend = () => resolve();
      utterance.onerror = () => resolve();
      window.speechSynthesis.speak(utterance);
    });
  }

  function stopSpeaking() {
    // Stop current audio
    if (currentAudioRef.current) {
      currentAudioRef.current.pause();
      currentAudioRef.current = null;
    }

    // Clear queue
    ttsQueueRef.current = [];
    ttsPlayingRef.current = false;

    // Stop browser TTS
    window.speechSynthesis?.cancel();
    setSpeaking(false);
    setVoiceOutputStatus("Voz detenida");
  }

  // ─────────────────────────────────────────────────────────────────────────
  // ASK VIGIA
  // ─────────────────────────────────────────────────────────────────────────

  async function requestVigiaAnswer(userQuestion: string) {
    const response = await fetch(`${API_BASE}/api/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: userQuestion }),
    });
    if (!response.ok) throw new Error("Financial source unavailable");

    return (await response.json()) as {
      answer: string;
      data_source?: DataSourceStatus;
    };
  }

  async function answerSpokenQuestion(userQuestion: string) {
    setUnderstandingStatus("Pregunta directa · preparando respuesta ejecutiva");
    setUnderstandingTone("processing");
    const result = await requestVigiaAnswer(userQuestion);
    if (result.data_source) setDataSourceStatus(result.data_source);

    setVigiaResponse(result.answer);
    setResponses((current) => [
      ...current,
      {
        tag: "CONSULTA EJECUTIVA",
        message: result.answer,
        should_respond: true,
        severity: "info",
        category: "voice_question",
        sentiment: null,
        voice_intervention: voiceMode && interventionLevel !== "manual",
        evidence: [],
        source_status: result.data_source,
      },
    ]);
    setUnderstandingStatus("Pregunta entendida · respuesta generada con seguimiento_financiero");
    setUnderstandingTone("understood");

    if (result.answer && voiceMode && interventionLevel !== "manual") {
      setMicState("respondiendo consulta");
      await speakText(result.answer);
    }
  }

  async function askVigia() {
    if (!question.trim()) return;

    const userQuestion = question.trim();
    setQuestion("");
    setVigiaResponse("Procesando...");

    try {
      const result = await requestVigiaAnswer(userQuestion);
      if (result.data_source) setDataSourceStatus(result.data_source);
      setVigiaResponse(result.answer);

      if (result.answer && voiceMode && interventionLevel !== "manual") {
        await speakText(result.answer);
      }
    } catch {
      setVigiaResponse("Error al procesar la pregunta.");
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  // SESSION CLOCK
  // ─────────────────────────────────────────────────────────────────────────

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

  function freezeSessionClock() {
    if (activeStartedAtRef.current !== null) {
      accumulatedMsRef.current += Date.now() - activeStartedAtRef.current;
      activeStartedAtRef.current = null;
    }
    setElapsedMs(accumulatedMsRef.current);
    stopSessionTicker();
  }

  function getCurrentElapsedMs() {
    if (activeStartedAtRef.current === null) {
      return accumulatedMsRef.current;
    }
    return accumulatedMsRef.current + Date.now() - activeStartedAtRef.current;
  }

  // ─────────────────────────────────────────────────────────────────────────
  // AUDIO METER
  // ─────────────────────────────────────────────────────────────────────────

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

  // ─────────────────────────────────────────────────────────────────────────
  // GEMINI LIVE · NATIVE AUDIO
  // ─────────────────────────────────────────────────────────────────────────

  function getLiveMeetingSocketUrl() {
    const url = new URL(API_BASE);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.pathname = "/api/live-meeting";
    url.search = "";
    return url.toString();
  }

  function startGeminiLive(stream: MediaStream, reconnectAttempt = 0) {
    const existingSocket = liveSocketRef.current;
    if (
      existingSocket
      && (existingSocket.readyState === WebSocket.CONNECTING || existingSocket.readyState === WebSocket.OPEN)
    ) {
      setMicState("voz en tiempo real ya conectada");
      return;
    }

    liveClosingRef.current = false;
    liveFallbackStartedRef.current = false;
    const socket = new WebSocket(getLiveMeetingSocketUrl());
    socket.binaryType = "arraybuffer";
    liveSocketRef.current = socket;

    const inputContext = new AudioContext({ sampleRate: 16_000 });
    const playbackContext = new AudioContext({ sampleRate: 24_000 });
    liveInputContextRef.current = inputContext;
    livePlaybackContextRef.current = playbackContext;
    void inputContext.resume();
    void playbackContext.resume();

    let connectionReady = false;

    const reconnectLive = (reason: string) => {
      // Ignore failures from an old socket after a pause/resume cycle. Its
      // delayed close must never tear down the new Live connection.
      if (liveSocketRef.current !== socket) return;
      if (liveFallbackStartedRef.current || !listeningRef.current) return;
      liveFallbackStartedRef.current = true;
      const nextAttempt = connectionReady ? 0 : reconnectAttempt + 1;
      const retryDelay = Math.min(3000, 350 * (2 ** Math.min(nextAttempt, 4)));
      setSttProvider("Gemini Live · reconectando");
      setMicState(`${reason}; reconectando Live`);
      setUnderstandingStatus("Reconectando la conversación en tiempo real…");
      setUnderstandingTone("warning");
      stopGeminiLiveTransport();
      liveReconnectTimerRef.current = window.setTimeout(() => {
        liveReconnectTimerRef.current = null;
        if (listeningRef.current && liveSocketRef.current === null) {
          startGeminiLive(stream, nextAttempt);
        }
      }, retryDelay);
    };

    socket.onopen = () => {
      if (!listeningRef.current || liveSocketRef.current !== socket) {
        socket.close();
        void inputContext.close();
        void playbackContext.close();
        return;
      }
      try {
        const source = inputContext.createMediaStreamSource(stream);
        // 32 ms remains realtime while working reliably across Chromium audio devices.
        const processor = inputContext.createScriptProcessor(512, 1, 1);
        const mutedOutput = inputContext.createGain();
        mutedOutput.gain.value = 0;
        processor.onaudioprocess = (event) => {
          if (!listeningRef.current || socket.readyState !== WebSocket.OPEN) return;
          // Native Live remains full duplex. Browser acoustic echo cancellation
          // removes the loudspeaker signal, while the user's voice continues to
          // reach Gemini so it can interrupt the current response. Only the
          // legacy TTS fallback is muted while its audio element is active.
          if (assistantAudioActiveRef.current) return;
          const input = event.inputBuffer.getChannelData(0);
          const resampled = resampleAudio(input, inputContext.sampleRate, 16_000);
          socket.send(floatAudioToPcm16(resampled));
        };
        source.connect(processor);
        processor.connect(mutedOutput);
        mutedOutput.connect(inputContext.destination);
        liveInputSourceRef.current = source;
        liveProcessorRef.current = processor;
        setMicState("negociando sesión Gemini Live");
      } catch {
        reconnectLive("no se pudo preparar audio PCM");
      }
    };

    socket.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        if (livePlaybackModeRef.current === "native") playLivePcm(event.data);
        return;
      }
      if (event.data instanceof Blob) {
        if (livePlaybackModeRef.current === "native") {
          void event.data.arrayBuffer().then(playLivePcm);
        }
        return;
      }

      let message: LiveMeetingMessage;
      try {
        message = JSON.parse(String(event.data)) as LiveMeetingMessage;
      } catch {
        setMicState("evento de voz inválido");
        return;
      }

      if (message.type === "ready") {
        connectionReady = true;
        liveFallbackStartedRef.current = false;
        livePlaybackModeRef.current = message.playback ?? "tts";
        setSttProvider(
          livePlaybackModeRef.current === "tts"
            ? "Gemini Live · voz Gemini TTS"
            : "Gemini Live · audio nativo",
        );
        setMicState("escuchando en tiempo real");
        setUnderstandingStatus("VigIA escucha la reunión y responderá cuando sea necesario");
        setUnderstandingTone("understood");
        return;
      }
      if (message.type === "error") {
        reconnectLive(message.message);
        return;
      }
      if (message.type === "input_transcript") {
        handleStreamTranscript(
          {
            type: "transcript",
            text: message.text,
            is_final: message.is_final,
            confidence: message.confidence,
          },
          false,
        );
        return;
      }
      if (message.type === "output_transcript") {
        if (!message.is_final) {
          setVoiceOutputStatus("VigIA está respondiendo");
          return;
        }
        const answer = message.text.trim();
        lastAssistantSpeechRef.current = {
          text: answer,
          expiresAt: Date.now() + Math.max(12_000, answer.length * 85),
        };
        if (normalizeSpokenText(answer).replace(/[.!,]+$/g, "") !== "vigia conectado") {
          setVigiaResponse(answer);
          setResponses((current) => [
            ...current,
            {
              tag: "VIGIA EN VIVO",
              message: answer,
              should_respond: true,
              severity: "info",
              category: "live_assistant",
              sentiment: null,
              voice_intervention: true,
              evidence: [],
            },
          ]);
        }
        if (livePlaybackModeRef.current === "tts" && answer) {
          void speakText(answer);
        }
        return;
      }
      if (message.type === "analysis") {
        const result = message.result;
        if (result.source_status) setDataSourceStatus(result.source_status);
        if (result.sentiment) setCurrentSentiment(result.sentiment);
        if (result.severity === "warning" || result.severity === "critical") {
          setAlerts((current) => [...current, result]);
          setUnderstandingStatus("Contradicción verificada · VigIA intervendrá");
          setUnderstandingTone("warning");
        } else {
          setUnderstandingStatus("Entendido · monitoreo financiero activo");
          setUnderstandingTone("understood");
        }
        return;
      }
      if (message.type === "tool") {
        setMicState("consultando seguimiento_financiero");
        setUnderstandingStatus("Consulta ejecutiva · verificando datos oficiales");
        setUnderstandingTone("processing");
        return;
      }
      if (message.type === "interrupted") {
        stopLivePlayback();
        setMicState("interrumpida · escuchando");
        return;
      }
      if (message.type === "reconnecting") {
        setMicState("renovando sesión de voz");
        return;
      }
      if (message.type === "assistant_turn_complete") {
        if (livePlaybackSourcesRef.current.size === 0 && !ttsPlayingRef.current) {
          setSpeaking(false);
          setVoiceOutputStatus("Voz lista");
          setMicState("escuchando en tiempo real");
        }
      }
    };

    socket.onerror = () => reconnectLive("error Gemini Live");
    socket.onclose = () => {
      if (liveSocketRef.current !== socket) return;
      if (!liveClosingRef.current) {
        reconnectLive("Gemini Live desconectado");
      } else {
        liveSocketRef.current = null;
      }
    };
  }

  function stopGeminiLiveTransport() {
    if (liveReconnectTimerRef.current !== null) {
      window.clearTimeout(liveReconnectTimerRef.current);
      liveReconnectTimerRef.current = null;
    }
    liveClosingRef.current = true;
    liveProcessorRef.current?.disconnect();
    if (liveProcessorRef.current) liveProcessorRef.current.onaudioprocess = null;
    liveProcessorRef.current = null;
    liveInputSourceRef.current?.disconnect();
    liveInputSourceRef.current = null;
    liveInputContextRef.current?.close().catch(() => undefined);
    liveInputContextRef.current = null;

    const socket = liveSocketRef.current;
    if (socket) {
      if (socket.readyState === WebSocket.OPEN) socket.send("stop");
      socket.close();
    }
    liveSocketRef.current = null;
    stopLivePlayback();
    livePlaybackContextRef.current?.close().catch(() => undefined);
    livePlaybackContextRef.current = null;
  }

  function playLivePcm(buffer: ArrayBuffer) {
    const context = livePlaybackContextRef.current;
    if (!context || buffer.byteLength < 2) return;
    void context.resume();
    const pcm = new Int16Array(buffer);
    const audioBuffer = context.createBuffer(1, pcm.length, 24_000);
    const channel = audioBuffer.getChannelData(0);
    for (let index = 0; index < pcm.length; index += 1) {
      channel[index] = pcm[index] / 32768;
    }
    const source = context.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(context.destination);
    const startAt = Math.max(context.currentTime + 0.008, liveNextPlaybackAtRef.current);
    liveNextPlaybackAtRef.current = startAt + audioBuffer.duration;
    livePlaybackSourcesRef.current.add(source);
    source.onended = () => {
      livePlaybackSourcesRef.current.delete(source);
      if (livePlaybackSourcesRef.current.size === 0) {
        setSpeaking(false);
        setVoiceOutputStatus("Voz lista");
        setMicState("escuchando en tiempo real");
      }
    };
    setSpeaking(true);
    setVoiceOutputStatus("VigIA está respondiendo");
    setMicState("VigIA interviniendo");
    source.start(startAt);
  }

  function stopLivePlayback() {
    for (const source of livePlaybackSourcesRef.current) {
      try {
        source.stop();
      } catch {
        // Already stopped.
      }
    }
    livePlaybackSourcesRef.current.clear();
    liveNextPlaybackAtRef.current = 0;
    setSpeaking(false);
    setVoiceOutputStatus("Voz lista");
  }

  function resampleAudio(input: Float32Array, sourceRate: number, targetRate: number) {
    if (sourceRate === targetRate) return input;
    const ratio = sourceRate / targetRate;
    const outputLength = Math.max(1, Math.round(input.length / ratio));
    const output = new Float32Array(outputLength);
    for (let index = 0; index < outputLength; index += 1) {
      const position = index * ratio;
      const left = Math.floor(position);
      const right = Math.min(input.length - 1, left + 1);
      const weight = position - left;
      output[index] = input[left] * (1 - weight) + input[right] * weight;
    }
    return output;
  }

  function floatAudioToPcm16(input: Float32Array) {
    const output = new Int16Array(input.length);
    for (let index = 0; index < input.length; index += 1) {
      const sample = Math.max(-1, Math.min(1, input[index]));
      output[index] = sample < 0 ? sample * 32768 : sample * 32767;
    }
    return output.buffer;
  }

  // ─────────────────────────────────────────────────────────────────────────
  // GOOGLE RECORDER
  // ─────────────────────────────────────────────────────────────────────────

  function getStreamingSocketUrl(mimeType: string) {
    const url = new URL(API_BASE);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.pathname = "/api/transcribe-stream";
    url.searchParams.set("mime_type", mimeType);
    return url.toString();
  }

  function getRecorderMimeType() {
    const isFirefox = navigator.userAgent.toLowerCase().includes("firefox");
    const supportedTypes = isFirefox
      ? [
          "audio/ogg;codecs=opus",
          "audio/ogg",
          "audio/webm;codecs=opus",
          "audio/webm",
        ]
      : [
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
        startRecognitionFallback("formato de audio no soportado");
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
              scheduleStreamingTranscriptWatchdog();
            }
          })
          .catch(() => setMicState("audio no enviado"));
      };

      recorder.onerror = () => {
        setMicState("error capturando audio");
        startRecognitionFallback("error capturando audio");
      };

      try {
        recorder.start(400);
        mediaRecorderRef.current = recorder;
        setMicState("stream Google activo");
      } catch {
        setMicState("grabación no iniciada");
        startRecognitionFallback("grabación no iniciada");
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
        startRecognitionFallback(data.message);
        return;
      }

      handleStreamTranscript(data);
    };

    socket.onerror = () => {
      setMicState("error stream Google");
      stopStreamingTransport();
      startRecognitionFallback("error stream Google");
    };

    socket.onclose = () => {
      streamSocketRef.current = null;
      if (!streamClosingRef.current && listeningRef.current) {
        setMicState("stream Google desconectado");
        startRecognitionFallback("stream Google desconectado");
      }
    };
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

  function scheduleStreamingTranscriptWatchdog() {
    if (!chunkHadAudioRef.current || recorderTimerRef.current !== null) return;

    recorderTimerRef.current = window.setTimeout(() => {
      recorderTimerRef.current = null;
      if (!listeningRef.current || !chunkHadAudioRef.current) return;

      chunkHadAudioRef.current = false;
      setUnderstandingStatus("Voz detectada · activando reconocimiento alternativo");
      setUnderstandingTone("warning");
      stopStreamingTransport();
      startRecognitionFallback("Google STT no devolvió texto");
    }, 4500);
  }

  function stopGoogleRecorder(stopTracks: boolean) {
    stopGeminiLiveTransport();
    stopStreamingTransport();

    if (stopTracks) {
      stopAudioMeter();
      mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
      mediaStreamRef.current = null;
    }
  }

  function clearRecorderTimer() {
    if (recorderTimerRef.current !== null) {
      window.clearTimeout(recorderTimerRef.current);
      recorderTimerRef.current = null;
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  // BROWSER SPEECH RECOGNITION FALLBACK
  // ─────────────────────────────────────────────────────────────────────────

  function startRecognitionFallback(reason?: string) {
    if (!speechSupported) {
      setSttProvider("Google STT en vivo");
      setMicState(reason ? `${reason}; sin respaldo en Firefox` : "STT en vivo no disponible");
      return;
    }
    if (recognitionRef.current) return;
    setSttProvider("Respaldo navegador");
    setMicState(reason ? "respaldo de voz activo" : "reconocimiento alternativo activo");
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
      if (assistantAudioActiveRef.current) return;
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

      const interimTranscript = interim.trim();
      setInterimText(interimTranscript);
      if (interimTranscript) {
        setUnderstandingStatus("Escuchando tu voz…");
        setUnderstandingTone("listening");
      }

      if (finalText.trim()) {
        const spokenText = finalText.trim();
        const normalized = spokenText.toLocaleLowerCase("es-PE");
        if (normalized === lastFinalTextRef.current) return;
        lastFinalTextRef.current = normalized;
        setInterimText("");
        setLastHeardText(spokenText);
        setLastSpeechConfidence(null);
        setUnderstandingStatus("Frase recibida · contrastando con los datos");
        setUnderstandingTone("processing");
        appendTranscriptLine(spokenText, transcriptRole);
        queueTextForAnalysis(spokenText);
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

  // ─────────────────────────────────────────────────────────────────────────
  // PARTICIPANTS MANAGEMENT
  // ─────────────────────────────────────────────────────────────────────────

  function addParticipant() {
    setParticipants([...participants, { name: "", role: "" }]);
  }

  function updateParticipant(index: number, field: keyof Participant, value: string) {
    const updated = [...participants];
    updated[index] = { ...updated[index], [field]: value };
    setParticipants(updated);
  }

  function removeParticipant(index: number) {
    if (participants.length > 1) {
      setParticipants(participants.filter((_, i) => i !== index));
    }
  }

  function resetMeeting() {
    setSessionStatus("idle");
    setSessionStartedAtIso(null);
    sessionStartedAtIsoRef.current = null;
    setSessionEndedAtIso(null);
    setElapsedMs(0);
    accumulatedMsRef.current = 0;
    setTranscript([]);
    setAlerts([]);
    setResponses([]);
    setDocuments(null);
    setQuestion("");
    setVigiaResponse("");
    setLastHeardText("");
    setLastSpeechConfidence(null);
    setUnderstandingStatus("Esperando la primera frase");
    setUnderstandingTone("idle");
    setVoiceOutputStatus("Voz lista");
    setCurrentSentiment("😐");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  const meetingStep = sessionStatus === "idle" ? 1 : sessionStatus === "closed" ? 3 : 2;
  const liveSignalEnergy = audioActive ? Math.min(1, Math.max(audioLevel, audioPeak * 0.7)) : 0;
  const liveSignalStatus = sessionStatus === "paused"
    ? "muted"
    : speaking
      ? "assistant_speaking"
      : Boolean(interimText)
      ? "user_speaking"
      : "connected";
  const financialScopeLabel = financialScope.campaigns.length
    ? financialScope.campaigns.length === 1 ? financialScope.campaigns[0] : `${financialScope.campaigns.length} campañas`
    : financialScope.accounts.length
      ? financialScope.accounts.length === 1 ? financialScope.accounts[0] : `${financialScope.accounts.length} cuentas`
      : "Todo SIFO";

  // ─────────────────────────────────────────────────────────────────────────
  // RENDER
  // ─────────────────────────────────────────────────────────────────────────

  return (
    <main className="shell">
      <header className="topbar">
        <div className="page-heading">
          <p className="eyebrow">Inteligencia ejecutiva en tiempo real</p>
          <h1>Reunión asistida</h1>
          <span>Un solo flujo para preparar, escuchar, contrastar y documentar cada decisión.</span>
        </div>
        <div className="meeting-source-badge">
          <Database size={17} />
          <div>
            <small>Fuente oficial conectada</small>
            <strong>{snapshot?.campaigns.length ?? 0} cuentas · {dataSourceStatus?.period ?? "cargando"}</strong>
          </div>
        </div>
      </header>

      <nav className="meeting-progress" aria-label="Progreso de la reunión">
        <div className={meetingStep === 1 ? "active" : "complete"} aria-current={meetingStep === 1 ? "step" : undefined}>
          <span><Settings size={17} /></span>
          <div><small>Paso 1</small><strong>Preparar</strong></div>
        </div>
        <i aria-hidden="true" className={meetingStep > 1 ? "complete" : ""} />
        <div className={meetingStep === 2 ? "active" : meetingStep > 2 ? "complete" : ""} aria-current={meetingStep === 2 ? "step" : undefined}>
          <span><Mic size={17} /></span>
          <div><small>Paso 2</small><strong>Reunión en vivo</strong></div>
        </div>
        <i aria-hidden="true" className={meetingStep > 2 ? "complete" : ""} />
        <div className={meetingStep === 3 ? "active" : ""} aria-current={meetingStep === 3 ? "step" : undefined}>
          <span><FileText size={17} /></span>
          <div><small>Paso 3</small><strong>Resumen y acuerdos</strong></div>
        </div>
      </nav>

      <section className="status-strip" aria-label="Estado del sistema">
          <StatusPill label="API" value={apiState} tone={apiState === "conectado" ? "good" : "warn"} />
          <StatusPill label="Sesión" value={sessionLabel} tone={sessionStatus === "active" ? "good" : "muted"} />
          <StatusPill label="Tiempo" value={elapsedLabel} tone={sessionStatus === "active" ? "good" : "muted"} />
          <StatusPill label="Alertas" value={String(alerts.length)} tone={criticalCount > 0 ? "bad" : "good"} />
          <StatusPill label="Perfil" value={profileShortLabel(executiveProfile)} tone="muted" />
          <StatusPill label="Alcance" value={financialScopeLabel} tone="muted" />
          <StatusPill label="Sentimiento" value={currentSentiment} tone="muted" />
      </section>

      {/* Panel 1: Meeting Setup */}
      {sessionStatus === "idle" && (
        <section className="setup-panel">
          <SectionTitle icon={<Settings size={18} />} title="Configura la próxima reunión" badge="Paso 1 de 3" />
          <p className="panel-intro">Define el contexto de trabajo para que VigIA sepa cuándo intervenir y con qué criterio ejecutivo.</p>

          <div className="setup-grid">
            <label>
              Tipo de reunión
              <select value={meetingType} onChange={(e) => setMeetingType(e.target.value)}>
                <option value="Comite ejecutivo">Comité ejecutivo</option>
                <option value="Revisión de operaciones">Revisión de operaciones</option>
                <option value="Revisión financiera">Revisión financiera</option>
                <option value="Reunión de campaña">Reunión de campaña</option>
                <option value="Otro">Otro</option>
              </select>
            </label>

            <label>
              Perfil ejecutivo
              <select
                value={executiveProfile}
                onChange={(event) => setExecutiveProfile(event.target.value as ExecutiveProfile)}
              >
                <option value="balanced">Finanzas + Operaciones</option>
                <option value="cfo">Director Financiero</option>
                <option value="coo">Director de Operaciones</option>
              </select>
            </label>

            <label>
              Intervención por voz
              <select
                value={interventionLevel}
                onChange={(event) => setInterventionLevel(event.target.value as InterventionLevel)}
              >
                <option value="warning">Preguntas directas + contradicciones</option>
                <option value="critical">Preguntas directas + riesgos críticos</option>
                <option value="manual">Solo respuestas en pantalla</option>
              </select>
            </label>

            {snapshot?.scope_options && <FinancialScopeSelector options={snapshot.scope_options} value={financialScope} onChange={setFinancialScope} />}

            <div className="participants-section">
              <label>
                <Users size={16} />
                Participantes
              </label>
              {participants.map((p, i) => (
                <div key={i} className="participant-row">
                  <input
                    placeholder="Nombre"
                    value={p.name}
                    onChange={(e) => updateParticipant(i, "name", e.target.value)}
                  />
                  <input
                    placeholder="Rol"
                    value={p.role}
                    onChange={(e) => updateParticipant(i, "role", e.target.value)}
                  />
                  {participants.length > 1 && (
                    <button className="danger small" onClick={() => removeParticipant(i)}>
                      ×
                    </button>
                  )}
                </div>
              ))}
              <button className="secondary" onClick={addParticipant}>
                + Agregar participante
              </button>
            </div>

            <label className="voice-toggle">
              <input
                type="checkbox"
                checked={voiceMode}
                onChange={(e) => setVoiceMode(e.target.checked)}
              />
              <span><strong>Respuestas por voz</strong><small>VigIA contestará preguntas directas y también intervendrá ante riesgos del nivel configurado.</small></span>
            </label>

            <button className="primary large" onClick={startSession}>
              <Play size={20} />
              Iniciar reunión asistida
            </button>
          </div>
        </section>
      )}

      {/* Main Stage - Active meeting */}
      {sessionStatus !== "idle" && sessionStatus !== "closed" && (
        <>
          <section className={`room-stage ${sessionStatus} ${audioActive ? "audio-active" : "audio-idle"}`}>
            <div className="stage-main">
              <div className="integrated-live-grid">
                <div className="integrated-orb-column">
                  <div
                    className={`voice-orb meeting-orb ${liveSignalStatus}`}
                    style={{
                      "--orb-scale": String(1 + liveSignalEnergy * 0.1),
                      "--orb-glow": `${34 + liveSignalEnergy * 42}px`,
                    } as CSSProperties}
                  >
                    <div className="orb-aura" />
                    <div className="orb-sweep" />
                    <div className="orb-ripple ripple-one" />
                    <div className="orb-ripple ripple-two" />
                    <div className="orb-ring ring-outer" />
                    <div className="orb-ring ring-middle" />
                    <div className="orb-ring ring-inner" />
                    <div className="orb-inner">
                      <span className="orb-icon">{sessionStatus === "paused" ? <MicOff size={24} /> : <Mic size={24} />}</span>
                    </div>
                    <div className="orb-wave" aria-hidden="true">
                      {Array.from({ length: 15 }, (_, index) => <i key={index} />)}
                    </div>
                  </div>
                  <div className="integrated-orb-state">
                    <span className={sessionStatus === "active" ? "live" : ""} />
                    {sessionStatus === "paused" ? "Escucha en pausa" : speaking ? "VigIA respondiendo" : "Escuchando la reunión"}
                  </div>
                </div>
                <div className="live-session-content">
                  <div className="stage-heading">
                    <div className="live-mark">
                      {sessionStatus === "active" ? <Radio size={24} /> : <MicOff size={24} />}
                    </div>
                    <div>
                      <span>{sttProvider} · {micState}</span>
                      <h2>{sessionStatus === "active" ? "VigIA está escuchando" : "Reunión pausada"}</h2>
                      <small className={`voice-output-status ${speaking ? "active" : ""}`}>
                        {voiceMode ? <Volume2 size={13} /> : <VolumeX size={13} />}
                        {voiceMode ? voiceOutputStatus : "Respuestas de voz desactivadas"}
                      </small>
                    </div>
                  </div>

                  <div className={`live-caption ${interimText || lastHeardText ? "active" : ""} ${understandingTone}`}>
                    <Radio size={17} />
                    <div>
                      <small>{understandingStatus}</small>
                      <p>{interimText || lastHeardText || "Esperando la siguiente intervención de la sala…"}</p>
                      {!interimText && lastSpeechConfidence !== null ? (
                        <span className={lastSpeechConfidence < 0.65 ? "low" : ""}>
                          Precisión de escucha {Math.round(lastSpeechConfidence * 100)}%
                        </span>
                      ) : null}
                    </div>
                  </div>

                  {sessionStartedAtIso ? (
                    <div className="session-timing">
                      <Clock3 size={16} />
                      <span>Inicio {formatClock(sessionStartedAtIso)}</span>
                      <span>Duración {elapsedLabel}</span>
                    </div>
                  ) : null}

                  <div className="session-actions">
                    {sessionStatus === "active" ? (
                      <button onClick={pauseSession}>
                        <Pause size={17} />
                        Pausar
                      </button>
                    ) : null}
                    {sessionStatus === "paused" ? (
                      <button className="primary" onClick={resumeSession}>
                        <Play size={17} />
                        Reanudar
                      </button>
                    ) : null}
                    <button className="danger" onClick={closeSession}>
                      <FileText size={17} />
                      Cerrar y generar resumen
                    </button>
                  </div>
                </div>
              </div>
            </div>

            <aside className="intervention-panel">
              <SectionTitle
                icon={<MessageSquare size={18} />}
                title="Preguntar a VigIA"
                badge="datos oficiales"
              />
              <div className="ask-input-row">
                <input
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  placeholder="Ej: ¿Cuál es el margen de CLARO PERÚ?"
                  onKeyDown={(e) => e.key === "Enter" && askVigia()}
                />
                <button className="primary" onClick={askVigia} disabled={!question.trim()}>
                  <Send size={16} />
                </button>
              </div>
              {vigiaResponse && (
                <div className="vigia-response">
                  <p>{vigiaResponse}</p>
                </div>
              )}
            </aside>
          </section>

          {/* Panel 3: AI Responses */}
          <section className="live-layout">
            <section className="panel transcript-panel">
              <SectionTitle icon={<Mic size={18} />} title="Transcripción" badge={`${transcript.length}`} />
              <div className="scroll" ref={transcriptScrollRef}>
                {transcript.length === 0 ? <Empty text="Sin frases registradas." /> : null}
                {transcript.length > 10 && (
                  <div className="transcript-older">
                    + {transcript.length - 10} frases anteriores
                  </div>
                )}
                {transcript.slice(-10).map((line, index) => (
                  <article className="line" key={`${line.timestamp}-${index}`}>
                    <div>
                      <strong>{line.speaker}</strong>
                      <span>{line.timestamp}</span>
                    </div>
                    <small>{line.role}</small>
                    <p>{line.text}</p>
                  </article>
                ))}
                {interimText ? (
                  <article className="line interim-line">
                    <div>
                      <strong>Sala</strong>
                      <span>en vivo</span>
                    </div>
                    <small>Transcribiendo</small>
                    <p>{interimText}</p>
                  </article>
                ) : null}
              </div>
            </section>

            <section className="panel vigia-panel">
              <SectionTitle
                icon={<AlertTriangle size={18} />}
                title="Respuestas VigIA"
                badge={responses.length === 0 ? "sin respuesta" : `${responses.length}`}
              />
              <div className="scroll" ref={alertsScrollRef}>
                {responses.length === 0 ? <Empty text="VigIA escuchando..." /> : null}
                {responses.length > 5 && (
                  <div className="transcript-older">
                    + {responses.length - 5} respuestas anteriores
                  </div>
                )}
                {responses.slice(-5).map((response, index) => (
                  <article className={`response-card ${response.severity}`} key={`response-${index}`}>
                    <div className="response-header">
                      <span className="response-tag">{response.tag}</span>
                      {response.sentiment && (
                        <span className="response-sentiment">{response.sentiment}</span>
                      )}
                    </div>
                    <p>{response.message}</p>
                    {response.recommended_actions && response.recommended_actions.length > 0 && (
                      <div className="action-list">
                        {response.recommended_actions.slice(0, 3).map((action) => (
                          <small key={action}>{action}</small>
                        ))}
                      </div>
                    )}
                    {response.evidence.length > 0 && (
                      <div className="evidence-list">
                        {response.evidence.map((evidence) => (
                          <small key={`${evidence.campaign}-${evidence.metric}`}>
                            <span>{evidence.campaign}</span>
                            {evidence.metric}: {evidence.actual}{evidence.unit} / meta {evidence.target}{evidence.unit}
                          </small>
                        ))}
                      </div>
                    )}
                    <small className="response-signature">Atentamente, vigia a365.</small>
                  </article>
                ))}
              </div>
            </section>
          </section>

        </>
      )}

      {sessionStatus === "closed" ? (
        <>
          <section className="meeting-summary-hero">
            <div>
              <p className="eyebrow">Reunión finalizada</p>
              <h2>Resumen ejecutivo preparado</h2>
              <span>VigIA consolidó la conversación, las alertas y la evidencia financiera utilizada.</span>
            </div>
            <button className="primary" onClick={resetMeeting}>
              <Play size={17} />
              Preparar una nueva reunión
            </button>
          </section>

          <section className="summary-stats" aria-label="Resumen de la reunión">
            <StatusPill label="Duración" value={elapsedLabel} tone="muted" />
            <StatusPill label="Intervenciones" value={String(responses.length)} tone="good" />
            <StatusPill label="Alertas" value={String(alerts.length)} tone={criticalCount > 0 ? "bad" : "good"} />
            <StatusPill label="Frases registradas" value={String(transcript.length)} tone="muted" />
          </section>

          <section className="documents-panel meeting-documents">
            <SectionTitle
              icon={<FileText size={18} />}
              title="Documentos de cierre"
              badge={documents ? "4 documentos" : "generando"}
            />
            {documents ? (
              <div className="documents-grid">
                <div className="document-card">
                  <h3>📄 Minuta ejecutiva</h3>
                  <pre>{documents.executive_minutes}</pre>
                </div>
                <div className="document-card">
                  <h3>📊 Resumen financiero</h3>
                  <pre>{documents.financial_summary}</pre>
                </div>
                <div className="document-card">
                  <h3>😊 Reporte de sentimiento</h3>
                  <pre>{documents.sentiment_report}</pre>
                </div>
                <div className="document-card">
                  <h3>📝 Transcripción estructurada</h3>
                  <pre>{documents.structured_transcript}</pre>
                </div>
              </div>
            ) : (
              <div className="summary-generating"><Radio size={20} /> Consolidando acuerdos y evidencia…</div>
            )}
          </section>
        </>
      ) : null}

    </main>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// COMPONENTS
// ─────────────────────────────────────────────────────────────────────────────

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

function profileShortLabel(profile: ExecutiveProfile) {
  if (profile === "cfo") return "CFO";
  if (profile === "coo") return "COO";
  return "CFO+COO";
}

function speechWordOverlap(left: string, right: string) {
  const normalizeWords = (value: string) => value
    .toLocaleLowerCase("es-PE")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9\s]/g, " ")
    .split(/\s+/)
    .filter((word) => word.length > 2);
  const leftWords = new Set(normalizeWords(left));
  const rightWords = new Set(normalizeWords(right));
  if (leftWords.size < 4 || rightWords.size < 4) return 0;
  let shared = 0;
  leftWords.forEach((word) => {
    if (rightWords.has(word)) shared += 1;
  });
  return shared / Math.min(leftWords.size, rightWords.size);
}
