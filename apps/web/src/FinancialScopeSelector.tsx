import { Check, ChevronDown, Search, ShieldCheck, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import type { FinancialScopeOptions, FinancialScopeSelection } from "./types";

type ScopeOption = { value: string; label: string; detail?: string };

type MultiScopeFieldProps = {
  id: "accounts" | "campaigns";
  label: string;
  options: ScopeOption[];
  value: string[];
  openField: string | null;
  setOpenField: (value: string | null) => void;
  onChange: (value: string[]) => void;
};

const effectiveValues = (options: ScopeOption[], value: string[]) => {
  const allowed = new Set(options.map((item) => item.value));
  const selected = Array.from(new Set(value.filter((item) => allowed.has(item))));
  return selected.length ? selected : options.map((item) => item.value);
};

function MultiScopeField({ id, label, options, value, openField, setOpenField, onChange }: MultiScopeFieldProps) {
  const [search, setSearch] = useState("");
  const open = openField === id;
  const selected = effectiveValues(options, value);
  const selectedSet = new Set(selected);
  const allSelected = options.length > 0 && selected.length === options.length;
  const visible = useMemo(() => {
    const term = search.trim().toLocaleLowerCase("es-PE");
    return term
      ? options.filter((item) => `${item.label} ${item.detail || ""}`.toLocaleLowerCase("es-PE").includes(term))
      : options;
  }, [options, search]);
  const summary = !options.length
    ? "Sin opciones"
    : allSelected
    ? "Todas"
    : selected.length === 1
      ? options.find((item) => item.value === selected[0])?.label || selected[0]
      : `${selected.length} de ${options.length}`;

  const commit = (next: string[]) => onChange(next.length === options.length ? [] : next);
  const toggle = (item: string) => {
    if (selectedSet.has(item) && selected.length === 1) return;
    commit(selectedSet.has(item) ? selected.filter((value) => value !== item) : [...selected, item]);
  };

  return <div className={`scope-multi-field ${open ? "open" : ""}`}>
    <span>{label}</span>
    <button type="button" className="scope-multi-trigger" onClick={() => { setOpenField(open ? null : id); setSearch(""); }} aria-expanded={open}>
      <strong>{summary}</strong><ChevronDown size={15}/>
    </button>
    {open && <div className="scope-multi-menu">
      <div className="scope-menu-actions">
        <button type="button" disabled={allSelected} onClick={() => onChange([])}>{allSelected ? "Todas seleccionadas" : "Seleccionar todas"}</button>
        <button type="button" onClick={() => setOpenField(null)}>Cerrar</button>
      </div>
      <label className="scope-menu-search"><Search size={14}/><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder={`Buscar ${label.toLocaleLowerCase("es-PE")}`}/>{search && <button type="button" aria-label="Limpiar búsqueda" onClick={() => setSearch("")}><X size={13}/></button>}</label>
      <small className="scope-menu-help">Desmarca para excluir o usa “Solo” para aislar una opción.</small>
      <div className="scope-menu-options">
        {visible.map((item) => <div className={`scope-menu-option ${selectedSet.has(item.value) ? "selected" : ""}`} key={item.value}>
          <button type="button" className="scope-option-toggle" role="checkbox" aria-checked={selectedSet.has(item.value)} onClick={() => toggle(item.value)}>
            <i>{selectedSet.has(item.value) ? <Check size={12}/> : null}</i>
            <span><strong>{item.label}</strong>{item.detail && <small>{item.detail}</small>}</span>
          </button>
          <button type="button" className="scope-option-only" onClick={() => onChange([item.value])}>Solo</button>
        </div>)}
        {!visible.length && <p>Sin coincidencias.</p>}
      </div>
    </div>}
  </div>;
}

export function FinancialScopeSelector({
  options,
  value,
  onChange,
}: {
  options: FinancialScopeOptions;
  value: FinancialScopeSelection;
  onChange: (value: FinancialScopeSelection) => void;
}) {
  const [openField, setOpenField] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const effectiveAccounts = effectiveValues(options.accounts.map((item) => ({ value: item, label: item })), value.accounts);
  const accountSet = new Set(effectiveAccounts);
  const compatibleCampaigns = options.campaigns.filter((item) => accountSet.has(item.account));
  const scopeLabel = value.campaigns.length
    ? value.campaigns.length === 1 ? value.campaigns[0] : `${value.campaigns.length} campañas`
    : value.accounts.length
      ? value.accounts.length === 1 ? value.accounts[0] : `${value.accounts.length} cuentas`
      : "Todo SIFO";

  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpenField(null);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  return <div className="financial-scope" ref={containerRef}>
    <div className="financial-scope-heading">
      <div><ShieldCheck size={17}/><span><strong>Alcance financiero de VigIA</strong><small>Define qué datos oficiales podrá analizar durante toda la reunión.</small></span></div>
      <em>{scopeLabel}</em>
    </div>
    <div className="financial-scope-fields">
      <MultiScopeField
        id="accounts"
        label="Cuentas"
        options={options.accounts.map((item) => ({ value: item, label: item }))}
        value={value.accounts}
        openField={openField}
        setOpenField={setOpenField}
        onChange={(accounts) => onChange({ accounts, campaigns: [] })}
      />
      <MultiScopeField
        id="campaigns"
        label="Campañas del corte"
        options={compatibleCampaigns.map((item) => ({ value: item.value, label: item.label, detail: `${item.account}${item.operation_type ? ` · ${item.operation_type}` : ""}` }))}
        value={value.campaigns}
        openField={openField}
        setOpenField={setOpenField}
        onChange={(campaigns) => onChange({ ...value, campaigns })}
      />
    </div>
  </div>;
}
