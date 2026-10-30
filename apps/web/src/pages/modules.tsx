import { Clock3 } from "lucide-react";
import { Badge, Card, PageHeader } from "../components/ui";

type ModuleInfo = { title: string; description: string; stage: number; items: string[] };

export const MODULES: Record<string, ModuleInfo> = {
  prospeccion: {
    title: "Prospección",
    description: "Encuentra empresas y decisores con filtros combinables, como en Apollo o Sales Navigator.",
    stage: 4,
    items: [
      "Filtros por país, ciudad, industria, empresa, cargo, nivel, departamento y tamaño",
      "Agente de investigación de decisores con fuentes públicas citadas",
      "Selección de resultados hacia listas y campañas",
    ],
  },
  contactos: {
    title: "Contactos",
    description: "Un registro único de empresas y personas, con la fuente de cada dato.",
    stage: 1,
    items: [
      "Importación de Excel y CSV con 8 campos: nombre, apellido, ubicación, teléfono, correo, cargo, empresa e industria",
      "Deduplicación y normalización",
      "Estados: enriquecido, validado, en proceso",
    ],
  },
  campanas: {
    title: "Campañas",
    description: "Correos personalizados desde tu propio buzón, con seguimiento real.",
    stage: 5,
    items: ["Conexión con Gmail, Microsoft 365 o SMTP", "Variables como {{nombre}} y {{empresa}}", "Bajas y lista de supresión automáticas"],
  },
  automatizacion: {
    title: "Automatización",
    description: "Secuencias que se detienen solas cuando el contacto responde.",
    stage: 5,
    items: ["Correo inicial y dos seguimientos con intervalos editables", "Parada por respuesta, rebote o baja", "Tareas automáticas en el CRM"],
  },
  crm: {
    title: "CRM",
    description: "Expediente de cada contacto y pipeline comercial configurable.",
    stage: 6,
    items: ["Línea de tiempo con correos, notas, llamadas y reuniones", "Pipeline en Kanban y en tabla", "Etapas personalizables por empresa"],
  },
  reportes: {
    title: "Reportes",
    description: "Indicadores calculados solo con datos reales de la plataforma.",
    stage: 9,
    items: ["Rendimiento por vendedor y campaña", "Valor del pipeline y conversión", "Filtros por fecha, industria y país"],
  },
};

export function ModulePage({ slug }: { slug: keyof typeof MODULES }) {
  const info = MODULES[slug];
  return (
    <>
      <PageHeader title={info.title} description={info.description} />
      <Card className="relative overflow-hidden">
        <div className="ork-glow pointer-events-none absolute inset-0 opacity-30" aria-hidden />
        <div className="relative max-w-2xl py-6">
          <Badge tone="violet">
            <Clock3 className="size-3.5" aria-hidden /> En construcción · Etapa {info.stage}
          </Badge>
          <h2 className="mt-4 text-[20px] font-bold text-ork-ink">Este módulo todavía no está activo</h2>
          <p className="mt-2 text-[14.5px] text-ork-graphite">
            Para no mostrar funciones simuladas, ORKESTA lo habilita cuando su backend esté listo y probado. Esto es lo que traerá:
          </p>
          <ul className="mt-4 space-y-2">
            {info.items.map((item) => (
              <li key={item} className="flex gap-2.5 text-[14px] text-ork-graphite">
                <span className="ork-gradient mt-2 size-1.5 shrink-0 rounded-full" aria-hidden />
                {item}
              </li>
            ))}
          </ul>
        </div>
      </Card>
    </>
  );
}
