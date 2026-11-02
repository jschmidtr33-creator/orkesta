import { useQuery } from "@tanstack/react-query";
import { ArrowDownRight, ArrowUpRight, Download, Minus } from "lucide-react";
import { useMemo, useState } from "react";
import { Alert, Card, CardHeader, cx, PageHeader, Spinner } from "../components/ui";
import { api } from "../lib/api";

type Kpis = {
  contacts_added: number;
  emails_sent: number;
  contacted: number;
  opened: number;
  replied: number;
  reply_rate: number | null;
  bounced: number;
  deals_created: number;
  proposals_sent: number;
  won: number;
  lost: number;
  win_rate: number | null;
  won_value: Record<string, number>;
  activities: number;
};
type Point = { date: string; emails_sent: number; replies: number; contacts_added: number; won: number };
type CampaignRow = {
  id: string;
  name: string;
  status: string;
  emails_sent: number;
  contacted: number;
  opened: number;
  clicked: number;
  replied: number;
  bounced: number;
  open_rate: number | null;
  click_rate: number | null;
  reply_rate: number | null;
};
type TeamRow = {
  user_id: string;
  name: string;
  email: string;
  role: string;
  contacts_added: number;
  emails_sent: number;
  contacted: number;
  replied: number;
  reply_rate: number | null;
  deals_created: number;
  proposals_sent: number;
  won: number;
  win_rate: number | null;
  won_value: Record<string, number>;
  activities: number;
};
type Overview = {
  period: { from: string; to: string; days: number; previous_from: string };
  kpis: Kpis;
  previous: Kpis;
  funnel: { key: string; label: string; value: number }[];
  series: { unit: "day" | "week"; points: Point[] };
  campaigns: CampaignRow[];
  team: TeamRow[];
  pipeline: { id: string; name: string; probability: number; deals: number; value: Record<string, number> }[];
  pipeline_total: Record<string, number>;
  lost_reasons: { reason: string; count: number }[];
  sources: { source: string; count: number }[];
  scope: { user_id: string | null; can_filter: boolean };
};

const SOURCES: Record<string, string> = {
  importacion: "Importación",
  manual: "Manual",
  enriquecimiento: "Enriquecimiento",
  investigacion: "Prospección",
  validacion: "Validación",
};
const PRESETS = [
  { key: "7", label: "7 días" },
  { key: "30", label: "30 días" },
  { key: "90", label: "90 días" },
  { key: "mes", label: "Este mes" },
  { key: "anio", label: "Este año" },
];
const BAR = "#2a78d6"; // serie única: un solo tono

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const n = (v: number) => new Intl.NumberFormat("es").format(v);
const money = (m: Record<string, number>) =>
  Object.entries(m).length
    ? Object.entries(m)
        .map(([cur, v]) => new Intl.NumberFormat("es", { style: "currency", currency: cur, maximumFractionDigits: 0 }).format(v))
        .join(" · ")
    : "—";
const pct = (v: number | null) => (v === null ? "—" : `${v} %`);
const shortDate = (s: string) => new Date(s + "T00:00").toLocaleDateString("es", { day: "numeric", month: "short" });

function range(preset: string): { desde: string; hasta: string } {
  const today = new Date();
  const start = new Date(today);
  if (preset === "mes") start.setDate(1);
  else if (preset === "anio") start.setMonth(0, 1);
  else start.setDate(today.getDate() - (Number(preset) - 1));
  return { desde: iso(start), hasta: iso(today) };
}

export function ReportsPage() {
  const [preset, setPreset] = useState("30");
  const [custom, setCustom] = useState(range("30"));
  const [user, setUser] = useState("");
  const dates = preset === "custom" ? custom : range(preset);
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const q = useQuery({
    queryKey: ["reports", dates, user, tz],
    queryFn: () => api<Overview>(`/api/reports/overview?${new URLSearchParams({ ...dates, tz, ...(user ? { vendedor: user } : {}) })}`),
    placeholderData: (prev) => prev,
  });
  const d = q.data;
  return (
    <div>
      <PageHeader title="Reportes" description="Cómo avanza tu operación comercial: de los contactos nuevos a los negocios ganados, con comparación contra el período anterior." />
      <div className="mb-5 flex flex-wrap items-end gap-3">
        <div className="flex max-w-full overflow-x-auto rounded-xl border border-ork-line bg-ork-card p-1" role="group" aria-label="Período">
          {[...PRESETS, { key: "custom", label: "Personalizado" }].map((p) => (
            <button
              key={p.key}
              type="button"
              onClick={() => setPreset(p.key)}
              aria-pressed={preset === p.key}
              className={cx("shrink-0 whitespace-nowrap rounded-lg px-3 py-1.5 text-[13px] font-semibold", preset === p.key ? "bg-ork-blue text-white" : "text-ork-graphite hover:bg-ork-ink/5")}
            >
              {p.label}
            </button>
          ))}
        </div>
        {preset === "custom" && (
          <div className="flex items-center gap-2 text-[13px]">
            <input type="date" value={custom.desde} max={custom.hasta} onChange={(e) => setCustom({ ...custom, desde: e.target.value })} className="ork-focus h-9 rounded-lg border border-ork-line px-2" aria-label="Desde" />
            <span className="text-ork-mist">a</span>
            <input type="date" value={custom.hasta} min={custom.desde} onChange={(e) => setCustom({ ...custom, hasta: e.target.value })} className="ork-focus h-9 rounded-lg border border-ork-line px-2" aria-label="Hasta" />
          </div>
        )}
        {d?.scope.can_filter && (
          <select value={user} onChange={(e) => setUser(e.target.value)} className="ork-focus h-9 rounded-lg border border-ork-line bg-ork-card px-2 text-[13px]" aria-label="Vendedor">
            <option value="">Todo el equipo</option>
            {(d?.team ?? []).map((t) => (
              <option key={t.user_id} value={t.user_id}>
                {t.name}
              </option>
            ))}
          </select>
        )}
        {d && (
          <span className="text-[12.5px] text-ork-mist">
            {shortDate(d.period.from)} – {shortDate(d.period.to)} · comparado con los {d.period.days} días anteriores
          </span>
        )}
      </div>
      {q.isLoading ? (
        <Spinner label="Calculando" />
      ) : q.error ? (
        <Alert>{q.error.message}</Alert>
      ) : d ? (
        <div className={cx("flex flex-col gap-5", q.isFetching && "opacity-70")}>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
            <Kpi label="Contactos nuevos" value={n(d.kpis.contacts_added)} now={d.kpis.contacts_added} before={d.previous.contacts_added} />
            <Kpi label="Correos enviados" value={n(d.kpis.emails_sent)} now={d.kpis.emails_sent} before={d.previous.emails_sent} />
            <Kpi label="Tasa de respuesta" value={pct(d.kpis.reply_rate)} now={d.kpis.reply_rate} before={d.previous.reply_rate} hint={`${n(d.kpis.replied)} respuestas`} points />
            <Kpi label="Oportunidades" value={n(d.kpis.deals_created)} now={d.kpis.deals_created} before={d.previous.deals_created} />
            <Kpi label="Negocios ganados" value={n(d.kpis.won)} now={d.kpis.won} before={d.previous.won} hint={money(d.kpis.won_value)} />
            <Kpi label="Tasa de cierre" value={pct(d.kpis.win_rate)} now={d.kpis.win_rate} before={d.previous.win_rate} hint={`${d.kpis.won} ganados · ${d.kpis.lost} perdidos`} points />
          </div>

          <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <Card>
              <CardHeader title="Embudo del período" description="Lo que ocurrió en estas fechas en cada etapa, y el paso de una a la siguiente." />
              <Funnel steps={d.funnel} />
            </Card>
            <Card>
              <CardHeader title="Pipeline abierto hoy" description={`Oportunidades abiertas por etapa · total ${money(d.pipeline_total)}`} />
              <HBars rows={d.pipeline.map((s) => ({ label: s.name, value: s.deals, note: `${money(s.value)} · ${s.probability} %` }))} empty="No hay oportunidades abiertas." />
            </Card>
          </div>

          <div className="grid gap-5 lg:grid-cols-2">
            <Card>
              <CardHeader title="Correos enviados" description={d.series.unit === "day" ? "Por día" : "Por semana"} />
              <Columns points={d.series.points} field="emails_sent" unit={d.series.unit} label="correos" />
            </Card>
            <Card>
              <CardHeader title="Respuestas recibidas" description={d.series.unit === "day" ? "Por día" : "Por semana"} />
              <Columns points={d.series.points} field="replies" unit={d.series.unit} label="respuestas" />
            </Card>
            <Card>
              <CardHeader title="Contactos nuevos" description={d.series.unit === "day" ? "Por día" : "Por semana"} />
              <Columns points={d.series.points} field="contacts_added" unit={d.series.unit} label="contactos" />
            </Card>
            <Card>
              <CardHeader title="De dónde llegan los contactos" description="Origen de los contactos nuevos del período" />
              <HBars rows={d.sources.map((s) => ({ label: SOURCES[s.source] ?? s.source, value: s.count }))} empty="Sin contactos nuevos en el período." />
            </Card>
          </div>

          <CampaignsTable rows={d.campaigns} />
          {d.scope.can_filter && !user && <TeamTable rows={d.team} />}

          <Card>
            <CardHeader title="Motivos de pérdida" description="Por qué se perdieron los negocios del período" />
            <HBars rows={d.lost_reasons.map((r) => ({ label: r.reason, value: r.count }))} empty="No se perdieron negocios en el período." />
          </Card>
        </div>
      ) : null}
    </div>
  );
}

function Kpi({ label, value, now, before, hint, points }: { label: string; value: string; now: number | null; before: number | null; hint?: string; points?: boolean }) {
  let delta: { text: string; dir: "up" | "down" | "flat" } | null = null;
  if (now !== null && before !== null) {
    if (points) {
      const diff = Math.round((now - before) * 10) / 10;
      delta = { text: `${diff > 0 ? "+" : ""}${diff} pts`, dir: diff > 0 ? "up" : diff < 0 ? "down" : "flat" };
    } else if (before > 0) {
      const diff = Math.round(((now - before) / before) * 100);
      delta = { text: `${diff > 0 ? "+" : ""}${diff} %`, dir: diff > 0 ? "up" : diff < 0 ? "down" : "flat" };
    } else if (now > 0) delta = { text: "nuevo", dir: "up" };
  }
  const Icon = delta?.dir === "up" ? ArrowUpRight : delta?.dir === "down" ? ArrowDownRight : Minus;
  return (
    <Card className="py-4">
      <p className="text-[12.5px] font-semibold text-ork-mist">{label}</p>
      <p className="mt-1 text-[24px] font-bold leading-tight text-ork-ink">{value}</p>
      <div className="mt-1 flex flex-wrap items-center gap-x-2 text-[12px]">
        {delta && (
          <span className={cx("inline-flex items-center gap-0.5 font-semibold", delta.dir === "up" ? "text-ok" : delta.dir === "down" ? "text-bad" : "text-ork-mist")}>
            <Icon className="size-3.5" aria-hidden />
            {delta.text}
            <span className="sr-only"> frente al período anterior</span>
          </span>
        )}
        {hint && <span className="text-ork-mist">{hint}</span>}
      </div>
    </Card>
  );
}

function Funnel({ steps }: { steps: { key: string; label: string; value: number }[] }) {
  const max = Math.max(1, ...steps.map((s) => s.value));
  return (
    <ol className="flex flex-col gap-2.5">
      {steps.map((s, i) => {
        const prev = i > 0 ? steps[i - 1].value : null;
        const conv = prev ? Math.round((100 * s.value) / prev) : null;
        return (
          <li key={s.key} className="grid grid-cols-[150px_1fr_auto] items-center gap-3 text-[13px]" title={`${s.label}: ${n(s.value)}`}>
            <span className="text-ork-graphite">{s.label}</span>
            <span className="h-6 rounded-r bg-ork-ink/5">
              <span className="block h-6 rounded-r" style={{ width: `${Math.max(s.value ? 2 : 0, (100 * s.value) / max)}%`, background: BAR }} />
            </span>
            <span className="w-24 text-right">
              <strong className="text-ork-ink">{n(s.value)}</strong>
              {conv !== null && <span className="ml-1.5 text-[11.5px] text-ork-mist">{conv} %</span>}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function HBars({ rows, empty }: { rows: { label: string; value: number; note?: string }[]; empty: string }) {
  if (!rows.length || rows.every((r) => r.value === 0)) return <p className="py-6 text-center text-[13px] text-ork-mist">{empty}</p>;
  const max = Math.max(1, ...rows.map((r) => r.value));
  return (
    <ul className="flex flex-col gap-2.5">
      {rows.map((r) => (
        <li key={r.label} className="text-[13px]" title={`${r.label}: ${n(r.value)}`}>
          <div className="mb-1 flex justify-between gap-3">
            <span className="truncate text-ork-graphite">{r.label}</span>
            <span className="shrink-0">
              <strong className="text-ork-ink">{n(r.value)}</strong>
              {r.note && <span className="ml-1.5 text-[11.5px] text-ork-mist">{r.note}</span>}
            </span>
          </div>
          <div className="h-2 rounded-r bg-ork-ink/5">
            <div className="h-2 rounded-r" style={{ width: `${(100 * r.value) / max}%`, background: BAR }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

/** Columnas de una sola serie con tooltip al pasar el cursor. */
function Columns({ points, field, unit, label }: { points: Point[]; field: keyof Omit<Point, "date">; unit: "day" | "week"; label: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const values = points.map((p) => p[field]);
  const max = Math.max(1, ...values);
  const total = values.reduce((a, b) => a + b, 0);
  const W = 600;
  const H = 160;
  const gap = 2;
  const bw = Math.max(2, W / points.length - gap);
  const ticks = useMemo(() => {
    const step = Math.ceil(points.length / 6);
    return points.map((_, i) => (i % step === 0 ? i : -1)).filter((i) => i >= 0);
  }, [points]);
  if (total === 0) return <p className="py-10 text-center text-[13px] text-ork-mist">Sin {label} en el período.</p>;
  const top = Math.ceil(max / 4) * 4 || 4;
  return (
    <div className="relative">
      <svg viewBox={`0 -14 ${W} ${H + 36}`} className="w-full" role="img" aria-label={`${label} por ${unit === "day" ? "día" : "semana"}: total ${total}`}>
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line x1={0} x2={W} y1={H - f * H} y2={H - f * H} stroke="#e5e7eb" strokeWidth={1} />
            {f > 0 && (
              <text x={2} y={H - f * H - 3} fontSize={10} fill="#6b7280">
                {Math.round(top * f)}
              </text>
            )}
          </g>
        ))}
        {points.map((p, i) => {
          const h = (p[field] / top) * H;
          const x = i * (bw + gap);
          return (
            <g key={p.date} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <rect x={x} y={0} width={bw + gap} height={H} fill="transparent" />
              {h > 0 && <path d={`M${x},${H} v${-Math.max(0, h - 2)} q0,-2 2,-2 h${bw - 4} q2,0 2,2 v${Math.max(0, h - 2)} z`} fill={BAR} opacity={hover === null || hover === i ? 1 : 0.45} />}
            </g>
          );
        })}
        {ticks.map((i) => (
          <text key={i} x={i * (bw + gap) + bw / 2} y={H + 15} fontSize={10} fill="#6b7280" textAnchor="middle">
            {shortDate(points[i].date)}
          </text>
        ))}
      </svg>
      {hover !== null && (
        <div
          className="pointer-events-none absolute -top-2 rounded-lg bg-ork-sidebar px-2.5 py-1.5 text-[12px] text-white shadow"
          style={{ left: `min(calc(${((hover * (bw + gap)) / W) * 100}% ), calc(100% - 140px))` }}
        >
          <p className="font-semibold">
            {unit === "week" ? "Semana del " : ""}
            {shortDate(points[hover].date)}
          </p>
          <p>
            {n(points[hover][field])} {label}
          </p>
        </div>
      )}
      <p className="mt-1 text-right text-[12px] text-ork-mist">
        Total: <strong className="text-ork-ink">{n(total)}</strong>
      </p>
    </div>
  );
}

function csv(name: string, header: string[], rows: (string | number)[][]) {
  const esc = (v: string | number) => `"${String(v).replace(/"/g, '""')}"`;
  const text = "﻿" + [header, ...rows].map((r) => r.map(esc).join(";")).join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}

function CampaignsTable({ rows }: { rows: CampaignRow[] }) {
  return (
    <Card className="overflow-x-auto p-0">
      <div className="flex items-start justify-between gap-3 px-5 pt-5">
        <div>
          <h2 className="text-[15px] font-bold text-ork-ink">Campañas</h2>
          <p className="text-[13px] text-ork-mist">Actividad dentro del período; las tasas son sobre los contactados.</p>
        </div>
        {rows.length > 0 && (
          <button
            type="button"
            onClick={() =>
              csv(
                "campanas.csv",
                ["Campaña", "Enviados", "Contactados", "Apertura %", "Clics %", "Respuesta %", "Respuestas", "Rebotes"],
                rows.map((r) => [r.name, r.emails_sent, r.contacted, r.open_rate ?? "", r.click_rate ?? "", r.reply_rate ?? "", r.replied, r.bounced]),
              )
            }
            className="inline-flex items-center gap-1 rounded-lg border border-ork-line px-2.5 py-1.5 text-[12.5px] font-semibold text-ork-graphite hover:bg-ork-ink/5"
          >
            <Download className="size-3.5" aria-hidden /> CSV
          </button>
        )}
      </div>
      {rows.length === 0 ? (
        <p className="px-5 py-8 text-center text-[13px] text-ork-mist">No se enviaron correos de campañas en el período.</p>
      ) : (
        <table className="mt-3 w-full min-w-[720px] text-left text-[13px]">
          <thead className="border-y border-ork-line text-[11.5px] uppercase tracking-wide text-ork-mist">
            <tr>
              <th className="px-5 py-2 font-semibold">Campaña</th>
              <th className="py-2 text-right font-semibold">Enviados</th>
              <th className="py-2 text-right font-semibold">Contactados</th>
              <th className="py-2 text-right font-semibold">Apertura</th>
              <th className="py-2 text-right font-semibold">Clics</th>
              <th className="py-2 text-right font-semibold">Respuesta</th>
              <th className="px-5 py-2 text-right font-semibold">Rebotes</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ork-line">
            {rows.map((r) => (
              <tr key={r.id}>
                <td className="px-5 py-2.5 font-semibold text-ork-ink">
                  <a href={`/app/campanas/${r.id}`} className="hover:text-ork-blue">
                    {r.name}
                  </a>
                </td>
                <td className="py-2.5 text-right">{n(r.emails_sent)}</td>
                <td className="py-2.5 text-right">{n(r.contacted)}</td>
                <td className="py-2.5 text-right">{pct(r.open_rate)}</td>
                <td className="py-2.5 text-right">{pct(r.click_rate)}</td>
                <td className="py-2.5 text-right font-semibold text-ork-ink">
                  {pct(r.reply_rate)} <span className="font-normal text-ork-mist">({r.replied})</span>
                </td>
                <td className="px-5 py-2.5 text-right">{n(r.bounced)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function TeamTable({ rows }: { rows: TeamRow[] }) {
  return (
    <Card className="overflow-x-auto p-0">
      <div className="flex items-start justify-between gap-3 px-5 pt-5">
        <div>
          <h2 className="text-[15px] font-bold text-ork-ink">Equipo</h2>
          <p className="text-[13px] text-ork-mist">Resultados de cada persona en el período.</p>
        </div>
        <button
          type="button"
          onClick={() =>
            csv(
              "equipo.csv",
              ["Persona", "Contactos nuevos", "Correos", "Respuestas", "Respuesta %", "Actividades", "Oportunidades", "Propuestas", "Ganados", "Cierre %", "Valor ganado"],
              rows.map((r) => [r.name, r.contacts_added, r.emails_sent, r.replied, r.reply_rate ?? "", r.activities, r.deals_created, r.proposals_sent, r.won, r.win_rate ?? "", money(r.won_value)]),
            )
          }
          className="inline-flex items-center gap-1 rounded-lg border border-ork-line px-2.5 py-1.5 text-[12.5px] font-semibold text-ork-graphite hover:bg-ork-ink/5"
        >
          <Download className="size-3.5" aria-hidden /> CSV
        </button>
      </div>
      <table className="mt-3 w-full min-w-[860px] text-left text-[13px]">
        <thead className="border-y border-ork-line text-[11.5px] uppercase tracking-wide text-ork-mist">
          <tr>
            <th className="px-5 py-2 font-semibold">Persona</th>
            <th className="py-2 text-right font-semibold">Contactos</th>
            <th className="py-2 text-right font-semibold">Correos</th>
            <th className="py-2 text-right font-semibold">Respuesta</th>
            <th className="py-2 text-right font-semibold">Actividades</th>
            <th className="py-2 text-right font-semibold">Oportunidades</th>
            <th className="py-2 text-right font-semibold">Propuestas</th>
            <th className="py-2 text-right font-semibold">Ganados</th>
            <th className="px-5 py-2 text-right font-semibold">Valor ganado</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-ork-line">
          {rows.map((r) => (
            <tr key={r.user_id}>
              <td className="px-5 py-2.5">
                <p className="font-semibold text-ork-ink">{r.name}</p>
                <p className="text-[12px] text-ork-mist">{r.email}</p>
              </td>
              <td className="py-2.5 text-right">{n(r.contacts_added)}</td>
              <td className="py-2.5 text-right">{n(r.emails_sent)}</td>
              <td className="py-2.5 text-right">
                {pct(r.reply_rate)} <span className="text-ork-mist">({r.replied})</span>
              </td>
              <td className="py-2.5 text-right">{n(r.activities)}</td>
              <td className="py-2.5 text-right">{n(r.deals_created)}</td>
              <td className="py-2.5 text-right">{n(r.proposals_sent)}</td>
              <td className="py-2.5 text-right">
                {n(r.won)} <span className="text-ork-mist">{r.win_rate !== null ? `· ${r.win_rate} %` : ""}</span>
              </td>
              <td className="px-5 py-2.5 text-right font-semibold text-ork-ink">{money(r.won_value)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
