import { useState, type ReactNode } from "react";
import {
  Bot,
  LogOut,
  Menu,
  Radio,
  X,
} from "lucide-react";
import type { AuthUser } from "./config";

type AppShellProps = {
  user: AuthUser;
  onNavigate: (path: string) => void;
  onLogout: () => void;
  children: ReactNode;
};

export function AppShell({ user, onNavigate, onLogout, children }: AppShellProps) {
  const [menuOpen, setMenuOpen] = useState(false);

  const go = (path: string) => {
    setMenuOpen(false);
    onNavigate(path);
  };

  return (
    <div className="product-shell">
      <button
        className="mobile-menu-trigger"
        aria-label="Abrir navegación"
        onClick={() => setMenuOpen(true)}
      >
        <Menu size={20} />
      </button>
      {menuOpen ? <button className="sidebar-backdrop" aria-label="Cerrar navegación" onClick={() => setMenuOpen(false)} /> : null}
      <aside className={`product-sidebar ${menuOpen ? "open" : ""}`}>
        <div className="sidebar-brand">
          <span className="brand-glyph"><Radio size={20} /></span>
          <div>
            <strong>VigIA</strong>
            <small>Executive intelligence</small>
          </div>
          <button className="sidebar-close" onClick={() => setMenuOpen(false)} aria-label="Cerrar">
            <X size={18} />
          </button>
        </div>

        <nav className="sidebar-nav" aria-label="Navegación principal">
          <span className="nav-label">Workspace</span>
          <button className="active" onClick={() => go("/app")}>
            <Bot size={18} />
            Reunión asistida
          </button>
        </nav>

        <div className="sidebar-user">
          <span className="user-avatar">{user.username.slice(0, 2).toUpperCase()}</span>
          <div>
            <strong>{user.username}</strong>
            <small>{user.role}</small>
          </div>
          <button onClick={onLogout} title="Cerrar sesión" aria-label="Cerrar sesión">
            <LogOut size={17} />
          </button>
        </div>
      </aside>

      <div className="product-content">{children}</div>
    </div>
  );
}
