import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Radio } from "lucide-react";
import { App } from "./App";
import { AppShell } from "./AppShell";
import { LandingPage } from "./LandingPage";
import { LoginPage } from "./LoginPage";
import { API_BASE, type AuthUser } from "./config";
import "./styles.css";
import "./design.css";

function Router() {
  const [path, setPath] = useState(window.location.pathname);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [authReady, setAuthReady] = useState(false);

  const navigate = (nextPath: string, replace = false) => {
    if (replace) window.history.replaceState({}, "", nextPath);
    else window.history.pushState({}, "", nextPath);
    setPath(nextPath);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  useEffect(() => {
    const onPopState = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPopState);
    fetch(`${API_BASE}/api/auth/me`, { credentials: "include" })
      .then(async (response) => {
        if (!response.ok) return null;
        const payload = await response.json();
        return payload.user as AuthUser;
      })
      .then((authenticatedUser) => setUser(authenticatedUser))
      .catch(() => setUser(null))
      .finally(() => setAuthReady(true));
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    const isProtected = path === "/app" || path.startsWith("/app/") || path.startsWith("/vigia") || path.startsWith("/jarvis");
    if (authReady && isProtected && !user) navigate("/login", true);
    if (authReady && path === "/login" && user) navigate("/app", true);
    if (authReady && user && (path.startsWith("/app/voice") || path.startsWith("/vigia") || path.startsWith("/jarvis"))) {
      navigate("/app", true);
    }
  }, [authReady, path, user]);

  async function logout() {
    await fetch(`${API_BASE}/api/auth/logout`, { method: "POST", credentials: "include" }).catch(() => undefined);
    setUser(null);
    navigate("/login", true);
  }

  if (path === "/") {
    return <LandingPage authenticated={Boolean(user)} onNavigate={navigate} />;
  }

  if (path === "/login") {
    if (authReady && user) return null;
    return (
      <LoginPage
        onNavigate={navigate}
        onAuthenticated={(authenticatedUser) => {
          setUser(authenticatedUser);
          navigate("/app", true);
        }}
      />
    );
  }

  if (!authReady || !user) {
    return (
      <main className="auth-loading">
        <span className="brand-glyph"><Radio size={22} /></span>
        <strong>Preparando tu workspace</strong>
        <small>Validando acceso seguro…</small>
      </main>
    );
  }

  return (
    <AppShell user={user} onNavigate={navigate} onLogout={logout}>
      <App />
    </AppShell>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Router />
  </React.StrictMode>,
);
