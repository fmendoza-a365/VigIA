import { useState, useRef, useEffect, useCallback } from "react";
import {
  Mic,
  MicOff,
  Volume2,
  VolumeX,
  Play,
  Square,
  Loader2,
  AlertCircle,
  CheckCircle2,
  Radio,
  Sparkles,
  ShieldCheck,
  Database,
} from "lucide-react";
import { API_BASE } from "../config";

function getRecorderMimeType() {
  if (typeof MediaRecorder === "undefined") return "";

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

function getStreamingSocketUrl(mimeType: string) {
  const url = new URL(API_BASE);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = "/api/transcribe-stream";
  url.searchParams.set("mime_type", mimeType || "audio/webm;codecs=opus");
  return url.toString();
}

type JarvisStatus =
  | "idle"
  | "connecting"
  | "connected"
  | "user_speaking"
  | "processing"
  | "assistant_speaking"
  | "muted"
  | "error"
  | "disconnected";

interface TranscriptMessage {
  role: "user" | "assistant";
  text: string;
  timestamp: string;
}

interface ToolActivity {
  name: string;
  args: Record<string, unknown>;
  timestamp: string;
}

interface AudioQueueItem {
  data: string;
  contentType: string;
}

export function JarvisApp() {
  // State
  const [status, setStatus] = useState<JarvisStatus>("idle");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<TranscriptMessage[]>([]);
  const [toolActivity, setToolActivity] = useState<ToolActivity[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isMuted, setIsMuted] = useState(false);
  const [interimText, setInterimText] = useState("");

  // Refs
  const wsRef = useRef<WebSocket | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const transcriptEndRef = useRef<HTMLDivElement | null>(null);
  const silenceTimerRef = useRef<number | null>(null);
  const pendingTextsRef = useRef<string[]>([]);
  const audioQueueRef = useRef<AudioQueueItem[]>([]);
  const audioPlayingRef = useRef(false);
  const currentAudioRef = useRef<HTMLAudioElement | null>(null);
  const streamSendQueueRef = useRef<Promise<void>>(Promise.resolve());
  const voiceOrbRef = useRef<HTMLDivElement | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const visualizerFrameRef = useRef<number | null>(null);

  const SILENCE_DELAY = 1500; // 1.5 seconds of silence before processing

  const stopAudioVisualization = useCallback(() => {
    if (visualizerFrameRef.current !== null) {
      cancelAnimationFrame(visualizerFrameRef.current);
      visualizerFrameRef.current = null;
    }

    if (audioContextRef.current) {
      void audioContextRef.current.close();
      audioContextRef.current = null;
    }

    voiceOrbRef.current?.style.removeProperty("--orb-scale");
    voiceOrbRef.current?.style.removeProperty("--orb-glow");
  }, []);

  const startAudioVisualization = useCallback((stream: MediaStream) => {
    if (typeof AudioContext === "undefined") return;

    stopAudioVisualization();
    const audioContext = new AudioContext();
    const source = audioContext.createMediaStreamSource(stream);
    const analyser = audioContext.createAnalyser();
    analyser.fftSize = 256;
    analyser.smoothingTimeConstant = 0.82;
    const samples = new Uint8Array(analyser.frequencyBinCount);
    let smoothedLevel = 0;

    source.connect(analyser);
    audioContextRef.current = audioContext;

    const renderSignal = () => {
      analyser.getByteTimeDomainData(samples);
      let energy = 0;
      for (const sample of samples) {
        const normalized = (sample - 128) / 128;
        energy += normalized * normalized;
      }

      const rms = Math.sqrt(energy / samples.length);
      const targetLevel = Math.min(1, rms * 5.5);
      smoothedLevel = smoothedLevel * 0.76 + targetLevel * 0.24;

      const orb = voiceOrbRef.current;
      if (orb) {
        orb.style.setProperty("--orb-scale", (1 + smoothedLevel * 0.1).toFixed(3));
        orb.style.setProperty("--orb-glow", `${34 + smoothedLevel * 42}px`);
      }

      visualizerFrameRef.current = requestAnimationFrame(renderSignal);
    };

    renderSignal();
  }, [stopAudioVisualization]);

  // Auto-scroll transcript
  useEffect(() => {
    if (transcriptEndRef.current) {
      transcriptEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [transcript]);

  // Start session
  const startSession = useCallback(async () => {
    try {
      setStatus("connecting");
      setError(null);

      // Request microphone permission
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
        },
      });

      mediaStreamRef.current = stream;
      startAudioVisualization(stream);

      // Start session via API
      const response = await fetch(`${API_BASE}/api/vigia/session/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });

      if (!response.ok) {
        throw new Error("Failed to start session");
      }

      const data = await response.json();
      setSessionId(data.session_id);

      // Connect WebSocket for real-time transcription
      const mimeType = getRecorderMimeType();
      const wsUrl = getStreamingSocketUrl(mimeType);
      const ws = new WebSocket(wsUrl);
      streamSendQueueRef.current = Promise.resolve();

      ws.onopen = () => {
        setStatus("connected");
        startAudioCapture(stream, ws, mimeType);
      };

      ws.onmessage = (event) => {
        const message = JSON.parse(event.data);
        handleWebSocketMessage(message, data.session_id);
      };

      ws.onerror = () => {
        setError("Error de conexión");
        setStatus("error");
      };

      ws.onclose = () => {
        setStatus("disconnected");
      };

      wsRef.current = ws;

      // Play welcome audio
      if (data.audio) {
        playAudio({ data: data.audio, contentType: data.content_type ?? "audio/mp3" });
      }

    } catch (err) {
      console.error("Error starting session:", err);
      if (err instanceof DOMException && err.name === "NotAllowedError") {
        setError("Permiso de micrófono denegado. Revisa los permisos de tu navegador.");
      } else {
        setError("Error al iniciar sesión");
      }
      mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
      mediaStreamRef.current = null;
      stopAudioVisualization();
      setStatus("error");
    }
  }, [startAudioVisualization, stopAudioVisualization]);

  // Stop session
  const stopSession = useCallback(async () => {
    try {
      // Stop audio capture
      if (mediaRecorderRef.current) {
        mediaRecorderRef.current.stop();
        mediaRecorderRef.current = null;
      }

      if (mediaStreamRef.current) {
        mediaStreamRef.current.getTracks().forEach((track) => track.stop());
        mediaStreamRef.current = null;
      }
      stopAudioVisualization();

      // Stop current audio
      if (currentAudioRef.current) {
        currentAudioRef.current.pause();
        currentAudioRef.current = null;
      }

      // Clear queues
      audioQueueRef.current = [];
      audioPlayingRef.current = false;
      pendingTextsRef.current = [];
      streamSendQueueRef.current = Promise.resolve();

      // Close WebSocket
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }

      // Stop session via API
      if (sessionId) {
        await fetch(`${API_BASE}/api/vigia/session/${sessionId}/stop`, {
          method: "POST",
        });
      }

      setSessionId(null);
      setStatus("idle");
      setInterimText("");

    } catch (err) {
      console.error("Error stopping session:", err);
    }
  }, [sessionId, stopAudioVisualization]);

  useEffect(() => () => stopAudioVisualization(), [stopAudioVisualization]);

  // Start audio capture
  const startAudioCapture = useCallback((stream: MediaStream, ws: WebSocket, mimeType: string) => {
    let recorder: MediaRecorder;
    try {
      recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
    } catch {
      setError("Formato de audio no soportado por este navegador.");
      setStatus("error");
      ws.close();
      return;
    }

    recorder.ondataavailable = (event) => {
      if (event.data.size > 0 && ws.readyState === WebSocket.OPEN) {
        // Blob.arrayBuffer() is asynchronous. Serialize conversions so WebM
        // fragments always reach Google in container order (the first one
        // carries the stream header).
        streamSendQueueRef.current = streamSendQueueRef.current
          .then(async () => {
            const buffer = await event.data.arrayBuffer();
            if (ws.readyState === WebSocket.OPEN) {
              ws.send(buffer);
            }
          })
          .catch(() => {
            setError("No se pudo enviar el audio al transcriptor.");
            setStatus("error");
          });
      }
    };

    recorder.start(400); // Stable ordered fragments with low meeting latency.
    mediaRecorderRef.current = recorder;
  }, []);

  // Handle WebSocket messages (from STT)
  const handleWebSocketMessage = useCallback((message: Record<string, unknown>, jarvisSessionId: string) => {
    if (message.type === "ready") {
      console.log("STT WebSocket ready");
      return;
    }

    if (message.type === "error") {
      setError(message.message as string);
      return;
    }

    if (message.type === "transcript") {
      const text = (message.text as string).trim();
      if (!text) return;

      // The legacy room keeps its recorder open while playing an answer. Drop
      // any transcription produced in that window so VigIA never analyzes its
      // own loudspeaker output as a new participant turn.
      if (audioPlayingRef.current) {
        setInterimText("");
        return;
      }

      if (!message.is_final) {
        // Interim result - show what user is saying
        setInterimText(text);
        setStatus("user_speaking");
        return;
      }

      // Final result - add to pending texts
      setInterimText("");
      pendingTextsRef.current.push(text);

      // Reset silence timer
      if (silenceTimerRef.current) {
        clearTimeout(silenceTimerRef.current);
      }

      // Start new silence timer
      silenceTimerRef.current = window.setTimeout(() => {
        // Process all accumulated texts
        const fullText = pendingTextsRef.current.join(" ");
        pendingTextsRef.current = [];

        if (fullText.trim()) {
          processWithJarvis(fullText, jarvisSessionId);
        }
      }, SILENCE_DELAY);
    }
  }, []);

  // Process with VigIA's deterministic financial fact-check engine.
  const processWithJarvis = useCallback(async (text: string, jarvisSessionId: string) => {
    try {
      setStatus("processing");

      // Add user message to transcript
      const userTimestamp = new Date().toLocaleTimeString("es-PE", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });

      setTranscript((prev) => [
        ...prev,
        { role: "user", text, timestamp: userTimestamp },
      ]);

      // Send to VigIA API
      const response = await fetch(`${API_BASE}/api/vigia/session/${jarvisSessionId}/message`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });

      if (!response.ok) {
        throw new Error("Failed to get response from VigIA");
      }

      const data = await response.json();

      if (data.response) {
        const assistantTimestamp = new Date().toLocaleTimeString("es-PE", {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        });

        setTranscript((prev) => [
          ...prev,
          { role: "assistant", text: data.response, timestamp: assistantTimestamp },
        ]);
      }

      // Play audio response
      if (data.audio) {
        addToAudioQueue(data.audio, data.content_type);
      }

      setStatus("connected");

    } catch (err) {
      console.error("Error processing with VigIA:", err);
      setError("Error al procesar mensaje");
      setStatus("connected");
    }
  }, []);

  // Audio queue management
  const addToAudioQueue = useCallback((audioBase64: string, contentType = "audio/mp3") => {
    audioQueueRef.current.push({ data: audioBase64, contentType });
    if (!audioPlayingRef.current) {
      processAudioQueue();
    }
  }, []);

  const processAudioQueue = useCallback(async () => {
    if (audioPlayingRef.current || audioQueueRef.current.length === 0) return;

    audioPlayingRef.current = true;
    setStatus("assistant_speaking");

    while (audioQueueRef.current.length > 0) {
      const audioItem = audioQueueRef.current.shift()!;

      // Stop any currently playing audio
      if (currentAudioRef.current) {
        currentAudioRef.current.pause();
        currentAudioRef.current = null;
      }

      await playAudio(audioItem);
    }

    audioPlayingRef.current = false;
    setStatus("connected");
  }, []);

  const playAudio = useCallback((audioItem: AudioQueueItem): Promise<void> => {
    return new Promise((resolve) => {
      const audio = new Audio(`data:${audioItem.contentType};base64,${audioItem.data}`);
      currentAudioRef.current = audio;

      audio.onended = () => {
        currentAudioRef.current = null;
        resolve();
      };

      audio.onerror = () => {
        currentAudioRef.current = null;
        resolve();
      };

      audio.play().catch(() => {
        currentAudioRef.current = null;
        resolve();
      });
    });
  }, []);

  // Toggle mute
  const toggleMute = useCallback(() => {
    setIsMuted(!isMuted);
    // Stop/start audio capture
    if (mediaRecorderRef.current) {
      if (isMuted) {
        mediaRecorderRef.current.resume();
        setStatus("connected");
      } else {
        mediaRecorderRef.current.pause();
        setStatus("muted");
      }
    }
  }, [isMuted]);

  // Interrupt
  const interrupt = useCallback(() => {
    // Stop current audio
    if (currentAudioRef.current) {
      currentAudioRef.current.pause();
      currentAudioRef.current = null;
    }

    // Clear audio queue
    audioQueueRef.current = [];
    audioPlayingRef.current = false;

    setStatus("connected");
  }, []);

  // Get status display
  const getStatusDisplay = () => {
    switch (status) {
      case "idle":
        return { icon: <Mic size={24} />, text: "Listo", color: "#64748b" };
      case "connecting":
        return { icon: <Loader2 size={24} className="animate-spin" />, text: "Conectando...", color: "#3b82f6" };
      case "connected":
        return { icon: <Radio size={24} />, text: "Conectado", color: "#22c55e" };
      case "user_speaking":
        return { icon: <Mic size={24} />, text: "Escuchando...", color: "#3b82f6" };
      case "processing":
        return { icon: <Loader2 size={24} className="animate-spin" />, text: "Procesando...", color: "#f59e0b" };
      case "assistant_speaking":
        return { icon: <Volume2 size={24} />, text: "Hablando...", color: "#8b5cf6" };
      case "muted":
        return { icon: <MicOff size={24} />, text: "Silenciado", color: "#ef4444" };
      case "error":
        return { icon: <AlertCircle size={24} />, text: "Error", color: "#ef4444" };
      case "disconnected":
        return { icon: <VolumeX size={24} />, text: "Desconectado", color: "#64748b" };
      default:
        return { icon: <Mic size={24} />, text: "Desconocido", color: "#64748b" };
    }
  };

  const statusDisplay = getStatusDisplay();
  const statusDescription = {
    idle: "Inicia la conversación cuando el comité esté listo.",
    connecting: "Preparando audio y conexión segura…",
    connected: "Audio conectado. VigIA permanece atento y en silencio.",
    user_speaking: "Capturando la conversación y construyendo contexto.",
    processing: "Contrastando la afirmación con los datos oficiales.",
    assistant_speaking: "VigIA está presentando una intervención verificada.",
    muted: "El micrófono está en pausa.",
    error: "La escucha necesita atención antes de continuar.",
    disconnected: "La conexión de audio finalizó.",
  }[status];

  return (
    <div className="jarvis-container">
      <header className="jarvis-header">
        <div className="page-heading">
          <p className="eyebrow">Conversación aumentada</p>
          <h1>Sala de voz IA</h1>
          <span>Escucha continua, contraste silencioso e intervención únicamente con evidencia.</span>
        </div>
        <div className="jarvis-status-indicator">
          <span className="status-dot" style={{ backgroundColor: statusDisplay.color }} />
          <span className="status-text">{statusDisplay.text}</span>
        </div>
      </header>

      <main className="jarvis-main">
        <section className="voice-workspace">
          <div className={`voice-stage-card ${status}`}>
            <div className="voice-stage-kicker"><Sparkles size={15} /> Agente ejecutivo activo</div>

            <div ref={voiceOrbRef} className={`voice-orb ${status}`}>
              <div className="orb-aura" />
              <div className="orb-sweep" />
              <div className="orb-ripple ripple-one" />
              <div className="orb-ripple ripple-two" />
              <div className="orb-ring ring-outer" />
              <div className="orb-ring ring-middle" />
              <div className="orb-ring ring-inner" />
              <div className="orb-inner">
                <span className="orb-icon">{statusDisplay.icon}</span>
              </div>
              <div className="orb-wave" aria-hidden="true">
                {Array.from({ length: 15 }, (_, index) => <i key={index} />)}
              </div>
            </div>

            <div className="voice-state-copy">
              <h2>{statusDisplay.text}</h2>
              <p>{statusDescription}</p>
            </div>

            {interimText ? (
              <div className="jarvis-interim">
                <small>Escuchando ahora</small>
                <p>“{interimText}”</p>
              </div>
            ) : (
              <div className="voice-placeholder">
                <Radio size={15} />
                <span>{status === "idle" ? "La transcripción aparecerá al iniciar" : "Esperando una frase completa"}</span>
              </div>
            )}

            <div className="jarvis-controls">
              {status === "idle" ? (
                <button className="jarvis-btn primary" onClick={startSession}>
                  <Play size={19} />
                  Iniciar escucha
                </button>
              ) : (
                <>
                  <button
                    className={`jarvis-btn ${isMuted ? "warning" : "secondary"}`}
                    onClick={toggleMute}
                  >
                    {isMuted ? <MicOff size={19} /> : <Mic size={19} />}
                    {isMuted ? "Activar micrófono" : "Pausar micrófono"}
                  </button>
                  {status === "assistant_speaking" ? (
                    <button className="jarvis-btn secondary" onClick={interrupt}>
                      <VolumeX size={19} />
                      Interrumpir voz
                    </button>
                  ) : null}
                  <button className="jarvis-btn danger" onClick={stopSession}>
                    <Square size={18} />
                    Finalizar sesión
                  </button>
                </>
              )}
            </div>

            {error ? (
              <div className="jarvis-error">
                <AlertCircle size={17} />
                <span>{error}</span>
                <button onClick={() => setError(null)} aria-label="Cerrar aviso">×</button>
              </div>
            ) : null}

            <div className="voice-trust-row">
              <span><ShieldCheck size={15} /> Sesión protegida</span>
              <span><Database size={15} /> Fuente oficial</span>
            </div>
          </div>

          <aside className="jarvis-transcript">
            <div className="transcript-heading">
              <div><small>Registro en vivo</small><h3>Transcripción</h3></div>
              <span>{transcript.length} mensajes</span>
            </div>
            <div className="transcript-messages">
              {transcript.length === 0 ? (
                <div className="transcript-empty">
                  <Radio size={24} />
                  <strong>Aún no hay conversación</strong>
                  <span>{status === "idle" ? "Inicia la escucha para comenzar." : "VigIA está atento a la sala."}</span>
                </div>
              ) : transcript.map((msg, index) => (
                <article key={index} className={`transcript-message ${msg.role}`}>
                  <div className="message-header">
                    <span className="message-role">{msg.role === "user" ? "Reunión" : "VigIA"}</span>
                    <span className="message-time">{msg.timestamp}</span>
                  </div>
                  <p className="message-text">{msg.text}</p>
                </article>
              ))}
              <div ref={transcriptEndRef} />
            </div>
          </aside>
        </section>

        {toolActivity.length > 0 ? (
          <section className="jarvis-tools">
            <div className="transcript-heading"><div><small>Trazabilidad</small><h3>Actividad del agente</h3></div></div>
            <div className="tool-list">
              {toolActivity.map((tool, index) => (
                <div key={index} className="tool-item">
                  <CheckCircle2 size={14} />
                  <span className="tool-name">{tool.name}</span>
                  <span className="tool-time">{tool.timestamp}</span>
                </div>
              ))}
            </div>
          </section>
        ) : null}
      </main>
    </div>
  );
}
