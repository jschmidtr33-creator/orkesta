import { useQuery } from "@tanstack/react-query";
import { ArrowRight, CheckCircle2, Circle, Clock3 } from "lucide-react";
import { Link } from "react-router-dom";
import { Badge, Card, CardHeader, PageHeader, Spinner } from "../components/ui";
import { api } from "../lib/api";
import { useSession } from "../lib/session";

type Summary = {
  team: { members: number; pending_invitations: number };
  contacts: number;
  kpis: Record<"contacts_found" | "emails_validated" | "open_opportunities" | "conversion_rate", number | null>;
  integrations: { google_sign_in: boolean };
};

type AuditItem = { id: string; at: string; actor: string; label: string };

const KPIS: { key: keyof Summary["kpis"]; label: string; stage: number }[] = [
  { key: "emails_validated", label: "Correos validados", stage: 2 },
  { key: "open_opportunities", label: "Oportunidades abiertas", stage: 6 },
  { key: "conversion_rate", label: "Tasa de conversión", stage: 6 },
];

export function formatWhen(iso: string): string {
  return new Intl.DateTimeFormat("es", { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso));
}

export function DashboardPage() {
  const { session } = useSession();
  const canSeeAudit = session?.workspace?.role !== "vendedor";
  const summary = useQuery({ queryKey: ["dashboard"], queryFn: () => api<Summary>("/api/dashboard/summary") });
  const audit = useQuery({
    queryKey: ["audit", 6],
    queryFn: () => api<AuditItem[]>("/api/audit?limit=6"),
    enabled: canSeeAudit,
  });
  const firstName = session?.user.full_name.split(" ")[0];

  if (summary.isLoading) return <Spinner />;
  const data = summary.data;
  const steps = [
    {
      done: (data?.team.members ?? 0) > 1 || (data?.team.pending_invitations ?? 0) > 0,
      title: "Invita a tu equipo",
      text: "Administradores, supervisores y vendedores, cada uno con su rol.",
      to: "/configuracion/equipo",
      action: "Invitar",
    },
    {
      done: Boolean(data?.integrations.google_sign_in),
      title: "Inicio de sesión con Google",
      text: data?.integrations.google_sign_in
        ? "Conectado: tu equipo puede entrar con su cuenta de Google Workspace."
        : "Pendiente de conexión: requiere la credencial de Google del servidor.",
      to: "/configuracion/integraciones",
      action: "Ver integraciones",
    },
    {
      done: (data?.contacts ?? 0) > 0,
      title: "Importa tus contactos",
      text: "Excel o CSV: se guardan 8 campos y cada dato conserva su fuente.",
      to: "/contactos/importar",
      action: "Importar",
    },
    { done: false, title: "Conecta tu correo", text: "Llega en la Etapa 5: Gmail, Microsoft 365 o SMTP.", stage: 5 },
  ];

  return (
    <>
      <PageHeader
        title={`Resumen general${firstName ? `, ${firstName}` : ""}`}
        description={`Espacio de trabajo ${session?.workspace?.name}. Los indicadores se calculan solo con datos reales de ORKESTA.`}
      />
      <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
        <Card className="flex flex-col gap-3">
          <p className="text-[13px] font-medium text-ork-mist">Contactos en tu base</p>
          <p className="text-[28px] font-bold leading-none text-ork-ink">{(data?.contacts ?? 0).toLocaleString("es")}</p>
          <Link to="/contactos" className="self-start text-[12.5px] font-semibold text-ork-blue hover:underline">
            Ver contactos
          </Link>
        </Card>
        {KPIS.map(({ key, label, stage }) => {
          const value = data?.kpis[key];
          return (
            <Card key={key} className="flex flex-col gap-3">
              <p className="text-[13px] font-medium text-ork-mist">{label}</p>
              {value === null || value === undefined ? (
                <>
                  <p className="text-[28px] font-bold leading-none text-ork-ink/25">—</p>
                  <span className="self-start">
                    <Badge tone="violet">
                      <Clock3 className="size-3.5" aria-hidden /> Etapa {stage}
                    </Badge>
                  </span>
                </>
              ) : (
                <p className="text-[28px] font-bold leading-none text-ork-ink">{value.toLocaleString("es")}</p>
              )}
            </Card>
          );
        })}
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-[1.25fr_1fr]">
        <Card>
          <CardHeader title="Primeros pasos" description="Lo que ya puedes hacer y lo que viene en las próximas etapas." />
          <ol className="flex flex-col divide-y divide-ork-line">
            {steps.map((step) => (
              <li key={step.title} className="flex items-start gap-3 py-3.5">
                {step.done ? (
                  <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-ok" aria-label="Hecho" />
                ) : (
                  <Circle className="mt-0.5 size-5 shrink-0 text-ork-mist" aria-label="Pendiente" />
                )}
                <div className="min-w-0 flex-1">
                  <p className="text-[14px] font-semibold text-ork-ink">{step.title}</p>
                  <p className="text-[13px] text-ork-mist">{step.text}</p>
                </div>
                {"to" in step && step.to ? (
                  <Link to={step.to} className="ork-focus inline-flex items-center gap-1 rounded-lg text-[13px] font-semibold text-ork-blue hover:underline">
                    {step.action} <ArrowRight className="size-3.5" aria-hidden />
                  </Link>
                ) : (
                  <Badge tone="violet">Etapa {"stage" in step ? step.stage : ""}</Badge>
                )}
              </li>
            ))}
          </ol>
        </Card>
        <Card>
          <CardHeader title="Actividad reciente" description="Registro de auditoría de tu espacio." />
          {!canSeeAudit && <p className="text-[13.5px] text-ork-mist">Visible para administradores y supervisores.</p>}
          {canSeeAudit && audit.isLoading && <Spinner />}
          {canSeeAudit && audit.data && audit.data.length === 0 && (
            <p className="text-[13.5px] text-ork-mist">Sin actividad todavía.</p>
          )}
          <ul className="flex flex-col gap-3">
            {audit.data?.map((item) => (
              <li key={item.id} className="flex gap-3">
                <span className="ork-gradient mt-1.5 size-2 shrink-0 rounded-full" aria-hidden />
                <div>
                  <p className="text-[13.5px] text-ork-ink">
                    <strong>{item.actor}</strong> {item.label}
                  </p>
                  <p className="text-[12px] text-ork-mist">{formatWhen(item.at)}</p>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </>
  );
}
