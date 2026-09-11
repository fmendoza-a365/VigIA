import {
  ArrowRight,
  BarChart3,
  Bot,
  CheckCircle2,
  Database,
  Radio,
  ShieldCheck,
  Sparkles,
} from "lucide-react";

type LandingPageProps = {
  authenticated: boolean;
  onNavigate: (path: string) => void;
};

export function LandingPage({ authenticated, onNavigate }: LandingPageProps) {
  const accessPath = authenticated ? "/app" : "/login";

  return (
    <main className="landing-page">
      <nav className="landing-nav">
        <button className="landing-brand" onClick={() => onNavigate("/")}>
          <span className="brand-glyph"><Radio size={20} /></span>
          <span>VigIA <small>A365</small></span>
        </button>
        <div className="landing-nav-links">
          <a href="#capacidades">Capacidades</a>
          <a href="#flujo">Cómo funciona</a>
          <button className="button-ghost" onClick={() => onNavigate(accessPath)}>
            {authenticated ? "Ir al panel" : "Iniciar sesión"}
          </button>
        </div>
      </nav>

      <section className="landing-hero">
        <div className="hero-copy">
          <div className="hero-kicker"><span /> Inteligencia ejecutiva en tiempo real</div>
          <h1>Una reunión con memoria, contexto y criterio financiero.</h1>
          <p>
            VigIA escucha conversaciones ejecutivas, contrasta afirmaciones con
            Seguimiento Financiero e interviene únicamente cuando la evidencia lo exige.
          </p>
          <div className="hero-actions">
            <button className="button-primary" onClick={() => onNavigate(accessPath)}>
              {authenticated ? "Abrir workspace" : "Entrar a VigIA"}
              <ArrowRight size={18} />
            </button>
            <button className="button-secondary" onClick={() => document.querySelector("#capacidades")?.scrollIntoView({ behavior: "smooth" })}>
              Ver capacidades
            </button>
          </div>
          <div className="hero-trust-row">
            <span><ShieldCheck size={16} /> Acceso protegido</span>
            <span><Database size={16} /> Datos oficiales</span>
            <span><CheckCircle2 size={16} /> Intervenciones trazables</span>
          </div>
        </div>

        <div className="hero-console" aria-label="Vista previa de VigIA">
          <div className="console-topbar">
            <span className="console-brand"><Bot size={17} /> Sala ejecutiva</span>
            <span className="live-chip"><i /> En vivo</span>
          </div>
          <div className="console-body">
            <div className="console-orb">
              <span className="console-ring ring-one" />
              <span className="console-ring ring-two" />
              <span className="console-core"><Radio size={25} /></span>
            </div>
            <div className="console-state">
              <small>Estado del agente</small>
              <strong>Escuchando la reunión</strong>
              <div className="mini-wave" aria-hidden="true">
                {Array.from({ length: 12 }, (_, index) => <i key={index} />)}
              </div>
            </div>
            <div className="console-insight">
              <div><Sparkles size={16} /><span>Contraste detectado</span></div>
              <p>“El margen informado no coincide con el cierre financiero.”</p>
              <small>Fuente: seguimiento_financiero · Evidencia validada</small>
            </div>
          </div>
        </div>
      </section>

      <section className="landing-metrics" aria-label="Principios del producto">
        <div><strong>01</strong><span>Escucha continua, intervención selectiva</span></div>
        <div><strong>02</strong><span>Contexto CFO + COO en una sola vista</span></div>
        <div><strong>03</strong><span>Decisiones respaldadas por evidencia</span></div>
      </section>

      <section className="capabilities-section" id="capacidades">
        <div className="section-heading-wide">
          <span>Capacidades</span>
          <h2>Diseñado para elevar la conversación, no para interrumpirla.</h2>
          <p>Una capa de inteligencia sobria que acompaña al comité antes, durante y después de cada reunión.</p>
        </div>
        <div className="capability-grid">
          <article>
            <span className="capability-icon"><Radio size={22} /></span>
            <small>Durante</small>
            <h3>Escucha contextual</h3>
            <p>Transcripción continua con detección de cuentas, métricas, riesgos y compromisos.</p>
          </article>
          <article>
            <span className="capability-icon"><Database size={22} /></span>
            <small>Validación</small>
            <h3>Datos reales</h3>
            <p>Contrasta cada afirmación contra el último período disponible del sistema financiero.</p>
          </article>
          <article>
            <span className="capability-icon"><BarChart3 size={22} /></span>
            <small>Decisión</small>
            <h3>Criterio ejecutivo</h3>
            <p>Adapta sus recomendaciones al perfil financiero, operativo o combinado.</p>
          </article>
        </div>
      </section>

      <section className="workflow-section" id="flujo">
        <div className="workflow-copy">
          <span>Flujo de trabajo</span>
          <h2>De la conversación a una decisión verificable.</h2>
        </div>
        <ol className="workflow-list">
          <li><b>1</b><div><strong>Conecta la reunión</strong><span>Define participantes, perfil y nivel de intervención.</span></div></li>
          <li><b>2</b><div><strong>Escucha y comprende</strong><span>VigIA separa conversación normal de afirmaciones verificables.</span></div></li>
          <li><b>3</b><div><strong>Contrasta y actúa</strong><span>Si existe una contradicción material, presenta evidencia y próximos pasos.</span></div></li>
        </ol>
      </section>

      <footer className="landing-footer">
        <div className="landing-brand"><span className="brand-glyph"><Radio size={18} /></span><span>VigIA A365</span></div>
        <p>Inteligencia ejecutiva conectada a tus datos.</p>
        <button onClick={() => onNavigate(accessPath)}>Acceder <ArrowRight size={15} /></button>
      </footer>
    </main>
  );
}
