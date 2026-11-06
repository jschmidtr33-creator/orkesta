import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ChevronRight, Eye, Search } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Alert, Avatar, Badge, Card, CardHeader, cx, PageHeader, Spinner } from "../components/ui";
import { api } from "../lib/api";
import { useSession } from "../lib/session";
import { CampaignsTable, Funnel, HBars, Kpi, money, n, pct, PRESETS, range, type CampaignRow, type Kpis } from "./reports";

type Metrics = {
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
  positive: number;
  searches: number;
  active_campaigns: number;
  open_deals: number;
  open_value: Record<string, number>;
  last_action_at: string | null;
};
type Person = Metrics & {
  user_id: string;
  name: string;
  email: string;
  job_title: string;
  avatar_url: string | null;
  role: string;
  active: boolean;
  last_login_at: string | null;
};
type TeamOverview = { people: Person[]; totals: Partial<Metrics> & { reply_rate: number | null } };
type Detail = {
  person: { user_id: string; name: string; email: string; job_title: string };
  metrics: Metrics;
  report: {
    kpis: Kpis;
    previous: Kpis;
    funnel: { key: string; label: string; value: number }[];
    campaigns: CampaignRow[];
    pipeline: { id: string; name: string; probability: number; deals: number; value: Record<string, number> }[];
    pipeline_total: Record<string, number>;
  };
  deals: { id: string; title: string; value: number; currency: string; stage: string; updated_at: string }[];
  searches: { id: string; summary: string; status: string; found: number; saved_name: string | null; at: string }[];
  timeline: { id: string; at: string; label: string; action: string }[];
};

const ROLE: Record<string, string> = { admin: "Administrador", supervisor: "Jefatura", vendedor: "Vendedor" };

function ago(iso: string | null): string {
  if (!iso) return "Sin actividad";
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 60) return minutes <= 1 ? "Hace un momento" : `Hace ${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `Hace ${hours} h`;
  const days = Math.round(hours / 24);
  return days === 1 ? "Ayer" : `Hace ${days} días`;
}

/** Une acciones seguidas iguales («Creó un contacto · 8 veces») para que la línea se lea rápido. */
function grouped(items: Detail["timeline"]) {
  const out: (Detail["timeline"][number] & { times: number; last: string })[] = [];
  for (const a of items) {
    const prev = out[out.length - 1];
    if (prev && prev.action === a.action) {
      prev.times += 1;
      prev.last = a.at;
    } else out.push({ ...a, times: 1, last: a.at });
  }
  return out;
}

const when = (iso: string) => new Date(iso).toLocaleString("es", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

function usePeriod() {
  const [preset, setPreset] = useState("30");
  const dates = range(preset);
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const picker = (
    <div className="mb-4 flex w-fit max-w-full overflow-x-auto rounded-xl border border-ork-line bg-ork-card p-1" role="group" aria-label="Período">
      {PRESETS.map((p) => (
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
  );
  return { params: new URLSearchParams({ ...dates, tz }).toString(), key: [dates, tz], picker };
}

function ReadOnlyNote() {
  return (
    <p className="mb-5 flex items-center gap-2 text-[13px] text-ork-mist">
      <Eye className="size-4 shrink-0" aria-hidden /> Solo lectura: ves el trabajo de tu equipo, pero solo cada persona puede modificar lo suyo.
    </p>
  );
}

export function TeamPage() {
  const { session } = useSession();
  const period = usePeriod();
  const q = useQuery({
    queryKey: ["team-overview", ...period.key],
    queryFn: () => api<TeamOverview>(`/api/reports/team?${period.params}`),
    placeholderData: (prev) => prev,
  });
  const isAdmin = session?.workspace?.role === "admin";
  const d = q.data;
  const t = d?.totals;
  return (
    <div>
      <PageHeader
        title="Mi equipo"
        description={isAdmin ? "Lo que trabaja cada persona de tu empresa: búsquedas, campañas, respuestas, rebotes y pipeline." : "Lo que trabajan las personas a tu cargo: búsquedas, campañas, respuestas, rebotes y pipeline."}
      />
      {period.picker}
      <ReadOnlyNote />
      {q.isLoading ? (
        <Spinner label="Calculando" />
      ) : q.error ? (
        <Alert>{q.error.message}</Alert>
      ) : d && d.people.length === 0 ? (
        <Card>
          <p className="py-6 text-center text-[14px] text-ork-graphite">
            {isAdmin ? "Todavía no hay más personas en tu empresa. Invítalas en Configuración → Equipo." : "Todavía no tienes personas a cargo. La administración de tu empresa te las asigna en Configuración → Equipo."}
          </p>
        </Card>
      ) : d && t ? (
        <div className={cx("flex flex-col gap-5", q.isFetching && "opacity-70")}>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            <Stat label="Búsquedas" value={n(t.searches ?? 0)} />
            <Stat label="Contactos nuevos" value={n(t.contacts_added ?? 0)} />
            <Stat label="Correos enviados" value={n(t.emails_sent ?? 0)} hint={`${n(t.active_campaigns ?? 0)} campañas activas`} />
            <Stat label="Respuestas" value={n(t.replied ?? 0)} hint={`${n(t.positive ?? 0)} positivas · ${pct(t.reply_rate)}`} />
            <Stat label="Rebotes" value={n(t.bounced ?? 0)} tone={(t.bounced ?? 0) > 0 ? "warn" : undefined} />
            <Stat label="Pipeline abierto" value={money(t.open_value ?? {})} hint={`${n(t.open_deals ?? 0)} oportunidades`} />
          </div>
          <Card className="overflow-x-auto p-0">
            <div className="px-5 pt-5">
              <h2 className="text-[15px] font-bold text-ork-ink">Por persona</h2>
              <p className="text-[13px] text-ork-mist">Toca una persona para ver su pipeline, campañas, búsquedas y actividad.</p>
            </div>
            <table className="mt-3 w-full min-w-[1080px] text-[13.5px]">
              <thead>
                <tr className="border-y border-ork-line bg-ork-surface/60 text-left text-[12px] font-semibold uppercase tracking-wide text-ork-mist">
                  <th className="min-w-[220px] px-5 py-2.5">Persona</th>
                  <th className="px-3 py-2.5 text-right">Búsquedas</th>
                  <th className="px-3 py-2.5 text-right">Contactos</th>
                  <th className="px-3 py-2.5 text-right">Enviados</th>
                  <th className="px-3 py-2.5 text-right">Respuestas</th>
                  <th className="px-3 py-2.5 text-right">Rebotes</th>
                  <th className="px-3 py-2.5 text-right">Pipeline abierto</th>
                  <th className="px-3 py-2.5 text-right">Propuestas</th>
                  <th className="px-3 py-2.5 text-right">Ganados</th>
                  <th className="min-w-[150px] px-5 py-2.5">Última actividad</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ork-line">
                {d.people.map((p) => (
                  <tr key={p.user_id} className="hover:bg-ork-surface/60">
                    <td className="px-5 py-3">
                      <Link to={`/equipo/${p.user_id}`} className="ork-focus flex items-center gap-3 rounded-lg">
                        <Avatar name={p.name} email={p.email} src={p.avatar_url} size={34} />
                        <span className="min-w-0">
                          <span className="flex items-center gap-1.5 whitespace-nowrap font-semibold text-ork-ink">
                            {p.name}
                            {!p.active && <Badge>Sin acceso</Badge>}
                          </span>
                          <span className="block truncate text-[12.5px] text-ork-mist">{p.job_title || ROLE[p.role] || p.role}</span>
                        </span>
                        <ChevronRight className="ml-auto size-4 text-ork-mist" aria-hidden />
                      </Link>
                    </td>
                    <td className="px-3 py-3 text-right tabular-nums">{n(p.searches)}</td>
                    <td className="px-3 py-3 text-right tabular-nums">{n(p.contacts_added)}</td>
                    <td className="px-3 py-3 text-right tabular-nums">{n(p.emails_sent)}</td>
                    <td className="px-3 py-3 text-right tabular-nums">
                      {n(p.replied)}
                      <span className="block text-[12px] text-ork-mist">{n(p.positive)} positivas · {pct(p.reply_rate)}</span>
                    </td>
                    <td className={cx("px-3 py-3 text-right tabular-nums", p.bounced > 0 && "text-amber-600")}>{n(p.bounced)}</td>
                    <td className="px-3 py-3 text-right tabular-nums">
                      {money(p.open_value)}
                      <span className="block text-[12px] text-ork-mist">{n(p.open_deals)} oportunidades</span>
                    </td>
                    <td className="px-3 py-3 text-right tabular-nums">{n(p.proposals_sent)}</td>
                    <td className="px-3 py-3 text-right tabular-nums">
                      {n(p.won)}
                      <span className="block text-[12px] text-ork-mist">{money(p.won_value)}</span>
                    </td>
                    <td className="px-5 py-3 text-[13px] text-ork-graphite">
                      {ago(p.last_action_at)}
                      <span className="block text-[12px] text-ork-mist">Ingresó: {p.last_login_at ? ago(p.last_login_at).toLowerCase() : "nunca"}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </div>
      ) : null}
    </div>
  );
}

function Stat({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: "warn" }) {
  return (
    <Card className="p-4">
      <p className="text-[12.5px] font-semibold text-ork-mist">{label}</p>
      <p className={cx("mt-1 text-[22px] font-bold tabular-nums text-ork-ink", tone === "warn" && "text-amber-600")}>{value}</p>
      {hint && <p className="mt-0.5 text-[12px] text-ork-mist">{hint}</p>}
    </Card>
  );
}

export function TeamMemberPage() {
  const { userId = "" } = useParams();
  const period = usePeriod();
  const q = useQuery({
    queryKey: ["team-member", userId, ...period.key],
    queryFn: () => api<Detail>(`/api/reports/team/${userId}?${period.params}`),
    placeholderData: (prev) => prev,
  });
  const d = q.data;
  return (
    <div>
      <Link to="/equipo" className="ork-focus mb-3 inline-flex items-center gap-1.5 rounded-lg text-[13.5px] font-semibold text-ork-blue">
        <ArrowLeft className="size-4" aria-hidden /> Mi equipo
      </Link>
      <PageHeader title={d?.person.name ?? "Cargando…"} description={d ? [d.person.job_title, d.person.email].filter(Boolean).join(" · ") : undefined} />
      {period.picker}
      <ReadOnlyNote />
      {q.isLoading ? (
        <Spinner label="Calculando" />
      ) : q.error ? (
        <Alert>{q.error.message}</Alert>
      ) : d ? (
        <div className={cx("flex flex-col gap-5", q.isFetching && "opacity-70")}>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            <Stat label="Búsquedas" value={n(d.metrics.searches)} />
            <Kpi label="Contactos nuevos" value={n(d.report.kpis.contacts_added)} now={d.report.kpis.contacts_added} before={d.report.previous.contacts_added} />
            <Kpi label="Correos enviados" value={n(d.report.kpis.emails_sent)} now={d.report.kpis.emails_sent} before={d.report.previous.emails_sent} hint={`${d.metrics.active_campaigns} campañas activas`} />
            <Kpi label="Tasa de respuesta" value={pct(d.report.kpis.reply_rate)} now={d.report.kpis.reply_rate} before={d.report.previous.reply_rate} hint={`${n(d.metrics.replied)} respuestas · ${n(d.metrics.positive)} positivas`} points />
            <Stat label="Rebotes" value={n(d.metrics.bounced)} tone={d.metrics.bounced > 0 ? "warn" : undefined} />
            <Stat label="Pipeline abierto" value={money(d.metrics.open_value)} hint={`${n(d.metrics.open_deals)} oportunidades`} />
          </div>
          <div className="grid gap-5 xl:grid-cols-2">
            <Card>
              <CardHeader title="Embudo del período" />
              <Funnel steps={d.report.funnel} />
            </Card>
            <Card>
              <CardHeader title="Pipeline abierto hoy" description={`Por etapa · total ${money(d.report.pipeline_total)}`} />
              <HBars rows={d.report.pipeline.map((s) => ({ label: s.name, value: s.deals, note: money(s.value) }))} empty="No tiene oportunidades abiertas." />
            </Card>
          </div>
          <CampaignsTable rows={d.report.campaigns} />
          <div className="grid gap-5 xl:grid-cols-2">
            <Card>
              <CardHeader title="Oportunidades abiertas" description="Las más recientes primero" />
              {d.deals.length === 0 ? (
                <p className="text-[13.5px] text-ork-mist">No tiene oportunidades abiertas.</p>
              ) : (
                <ul className="divide-y divide-ork-line">
                  {d.deals.map((x) => (
                    <li key={x.id} className="flex items-center justify-between gap-3 py-2.5 text-[13.5px]">
                      <span className="min-w-0">
                        <span className="block truncate font-semibold text-ork-ink">{x.title}</span>
                        <span className="text-[12.5px] text-ork-mist">{x.stage} · actualizada {ago(x.updated_at).toLowerCase()}</span>
                      </span>
                      <span className="shrink-0 tabular-nums text-ork-graphite">{money({ [x.currency]: x.value })}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
            <Card>
              <CardHeader title="Búsquedas recientes" description="Investigaciones de prospectos" />
              {d.searches.length === 0 ? (
                <p className="text-[13.5px] text-ork-mist">Todavía no ha hecho búsquedas.</p>
              ) : (
                <ul className="divide-y divide-ork-line">
                  {d.searches.map((s) => (
                    <li key={s.id} className="flex items-start gap-3 py-2.5 text-[13.5px]">
                      <Search className="mt-0.5 size-4 shrink-0 text-ork-violet" aria-hidden />
                      <span className="min-w-0 flex-1">
                        <span className="block font-semibold text-ork-ink">{s.saved_name || s.summary}</span>
                        <span className="text-[12.5px] text-ork-mist">
                          {when(s.at)} · {n(s.found)} personas encontradas
                        </span>
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
          <Card>
            <CardHeader title="Actividad" description="Lo último que hizo en ORKESTA" />
            {d.timeline.length === 0 ? (
              <p className="text-[13.5px] text-ork-mist">Sin actividad registrada.</p>
            ) : (
              <ol className="relative ml-2 border-l border-ork-line">
                {grouped(d.timeline).map((a) => (
                  <li key={a.id} className="mb-3 ml-4 text-[13.5px]">
                    <span className="absolute -left-[5px] mt-1.5 size-2.5 rounded-full bg-ork-blue" aria-hidden />
                    <span className="text-ork-ink">
                      {a.label.charAt(0).toUpperCase() + a.label.slice(1)}
                      {a.times > 1 && <span className="text-ork-mist"> · {a.times} veces</span>}
                    </span>
                    <span className="block text-[12.5px] text-ork-mist">{a.times > 1 ? `${when(a.last)} – ${when(a.at)}` : when(a.at)}</span>
                  </li>
                ))}
              </ol>
            )}
          </Card>
        </div>
      ) : null}
    </div>
  );
}
