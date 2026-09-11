import { useState, type FormEvent } from "react";
import { ArrowLeft, ArrowRight, Eye, EyeOff, LockKeyhole, Radio, ShieldCheck } from "lucide-react";
import { API_BASE, type AuthUser } from "./config";

type LoginPageProps = {
  onAuthenticated: (user: AuthUser) => void;
  onNavigate: (path: string) => void;
};

export function LoginPage({ onAuthenticated, onNavigate }: LoginPageProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/api/auth/login`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "No pudimos validar tus credenciales.");
      onAuthenticated(payload.user as AuthUser);
    } catch (loginError) {
      setError(loginError instanceof Error ? loginError.message : "No se pudo iniciar sesión.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-page">
      <section className="login-visual">
        <button className="back-link" onClick={() => onNavigate("/")}><ArrowLeft size={16} /> Volver</button>
        <div className="login-visual-copy">
          <div className="landing-brand"><span className="brand-glyph"><Radio size={20} /></span><span>VigIA A365</span></div>
          <h1>El criterio ejecutivo también puede estar conectado.</h1>
          <p>Accede a tu sala de inteligencia financiera y operativa.</p>
        </div>
        <div className="login-signal-card">
          <span className="login-signal-icon"><Radio size={23} /></span>
          <div><small>Estado de plataforma</small><strong>Servicios disponibles</strong></div>
          <i />
        </div>
      </section>

      <section className="login-form-side">
        <form className="login-card" onSubmit={submit}>
          <div className="login-lock"><LockKeyhole size={22} /></div>
          <span className="form-eyebrow">Acceso seguro</span>
          <h2>Bienvenido de nuevo</h2>
          <p>Ingresa con las credenciales asignadas a tu equipo.</p>

          <label>
            Usuario
            <input
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              placeholder="Nombre de usuario"
              required
            />
          </label>
          <label>
            Contraseña
            <span className="password-field">
              <input
                type={showPassword ? "text" : "password"}
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="••••••••••••"
                required
              />
              <button type="button" onClick={() => setShowPassword((visible) => !visible)} aria-label={showPassword ? "Ocultar contraseña" : "Mostrar contraseña"}>
                {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
              </button>
            </span>
          </label>

          {error ? <div className="login-error" role="alert">{error}</div> : null}

          <button className="button-primary login-submit" disabled={submitting}>
            {submitting ? "Validando…" : "Entrar al workspace"}
            {!submitting ? <ArrowRight size={18} /> : null}
          </button>
          <small className="login-security"><ShieldCheck size={15} /> Sesión privada con cookie segura y expiración automática.</small>
        </form>
      </section>
    </main>
  );
}
