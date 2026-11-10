import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookmarkPlus, Trash2, Users } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import { Alert, Button, cx, Modal, TextField } from "./ui";

export type TemplateKind = "campaign" | "proposal";
type TeamTemplate = {
  id: string;
  name: string;
  description: string;
  summary: string;
  preview: string;
  use_count: number;
  author: string;
  mine: boolean;
  can_manage: boolean;
  shared: boolean;
};

const WORDS: Record<TemplateKind, { one: string; blank: string }> = {
  campaign: { one: "campaña", blank: "Secuencia inicial de 3 correos para editar" },
  proposal: { one: "propuesta", blank: "Borrador armado con los datos de la oportunidad" },
};

export function useTeamTemplates(kind: TemplateKind, enabled = true) {
  return useQuery({
    queryKey: ["team-templates", kind],
    queryFn: () => api<TeamTemplate[]>(`/api/templates?tipo=${kind}`),
    enabled,
  });
}

/** «Empezar desde»: en blanco o desde una plantilla que compartió alguien de la empresa. */
export function TemplatePicker({ kind, value, onChange }: { kind: TemplateKind; value: string; onChange: (id: string) => void }) {
  const client = useQueryClient();
  const list = useTeamTemplates(kind);
  const remove = useMutation({
    mutationFn: (id: string) => api(`/api/templates/${id}`, { method: "DELETE" }),
    onSuccess: (_d, id) => {
      if (value === id) onChange("");
      client.invalidateQueries({ queryKey: ["team-templates", kind] });
    },
  });
  const share = useMutation({
    mutationFn: (v: { id: string; shared: boolean }) => api(`/api/templates/${v.id}`, { method: "PATCH", json: { shared: v.shared } }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["team-templates", kind] }),
  });
  const items = list.data ?? [];
  if (!items.length) return null;
  const option = (id: string, title: string, sub: string, extra?: React.ReactNode) => (
    <label
      key={id || "blank"}
      className={cx(
        "flex cursor-pointer items-start gap-3 rounded-xl border px-3 py-2.5 text-[13px] transition",
        value === id ? "border-ork-blue bg-ork-blue/5" : "border-ork-line hover:bg-ork-surface",
      )}
    >
      <input type="radio" name={`template-${kind}`} className="mt-0.5 size-4 accent-ork-blue" checked={value === id} onChange={() => onChange(id)} />
      <span className="min-w-0 flex-1">
        <span className="block font-semibold text-ork-ink">{title}</span>
        <span className="block text-[12px] text-ork-mist">{sub}</span>
      </span>
      {extra}
    </label>
  );
  return (
    <div>
      <p className="mb-1.5 flex items-center gap-1.5 text-[13px] font-semibold text-ork-ink">
        <Users className="size-4 text-ork-violet" aria-hidden /> Empezar desde
      </p>
      {remove.error && <Alert>{remove.error.message}</Alert>}
      <div className="flex max-h-60 flex-col gap-1.5 overflow-y-auto">
        {option("", "En blanco", WORDS[kind].blank)}
        {items.map((t) =>
          option(
            t.id,
            t.name,
            [t.shared ? "Del equipo" : "Solo tú", t.description || t.preview, t.summary, `de ${t.mine ? "ti" : t.author}`, t.use_count ? `usada ${t.use_count} ${t.use_count === 1 ? "vez" : "veces"}` : ""].filter(Boolean).join(" · "),
            <span className="flex shrink-0 items-center gap-1">
              {t.mine && (
                <button
                  type="button"
                  title={t.shared ? "Dejar de compartir con el equipo" : "Compartir con el equipo"}
                  onClick={(e) => {
                    e.preventDefault();
                    share.mutate({ id: t.id, shared: !t.shared });
                  }}
                  className="ork-focus rounded-lg px-2 py-1 text-[11.5px] font-semibold text-ork-blue hover:bg-ork-blue/5"
                >
                  {t.shared ? "Dejar de compartir" : "Compartir"}
                </button>
              )}
              {t.can_manage && (
              <button
                type="button"
                aria-label={`Borrar la plantilla ${t.name}`}
                title="Borrar plantilla"
                onClick={(e) => {
                  e.preventDefault();
                  if (window.confirm(t.shared ? `¿Borrar la plantilla «${t.name}»? Nadie del equipo podrá usarla.` : `¿Borrar tu plantilla «${t.name}»?`)) remove.mutate(t.id);
                }}
                className="ork-focus rounded-lg p-1 text-ork-mist hover:bg-ork-surface hover:text-red-600"
              >
                <Trash2 className="size-4" aria-hidden />
              </button>
              )}
            </span>,
          ),
        )}
      </div>
    </div>
  );
}

/** Guardar como plantilla: personal («Guardar como plantilla») o para toda la empresa
 * («Compartir con el equipo»). */
export function SaveTemplateButton({ kind, sourceId, defaultName, beforeSave }: { kind: TemplateKind; sourceId: string; defaultName: string; beforeSave?: () => Promise<unknown> }) {
  const client = useQueryClient();
  const [mode, setMode] = useState<"personal" | "equipo" | null>(null);
  const [name, setName] = useState(defaultName);
  const [description, setDescription] = useState("");
  const shared = mode === "equipo";
  const save = useMutation({
    mutationFn: async () => {
      // Cambios sin guardar: primero se guardan, así la plantilla lleva lo que se ve.
      if (beforeSave) await beforeSave();
      return api<{ id: string; name: string }>("/api/templates", { method: "POST", json: { kind, source_id: sourceId, name, description, shared } });
    },
    onSuccess: () => client.invalidateQueries({ queryKey: ["team-templates", kind] }),
  });
  const open = (m: "personal" | "equipo") => {
    save.reset();
    setName(defaultName);
    setDescription("");
    setMode(m);
  };
  return (
    <>
      <Button variant="secondary" icon={<BookmarkPlus className="size-4" aria-hidden />} onClick={() => open("personal")}>
        Guardar como plantilla
      </Button>
      <Button variant="secondary" icon={<Users className="size-4" aria-hidden />} onClick={() => open("equipo")}>
        Compartir con el equipo
      </Button>
      <Modal
        open={mode !== null}
        onOpenChange={(o) => !o && setMode(null)}
        title={shared ? "Plantilla para el equipo" : "Mi plantilla"}
        description={
          kind === "campaign"
            ? "Se guardan los correos, los tiempos entre pasos y el horario. Nunca los destinatarios ni tu buzón."
            : "Se guarda el contenido, los productos y el diseño. Nunca el cliente."
        }
      >
        {save.isSuccess ? (
          <div className="flex flex-col gap-4">
            <Alert tone="ok">
              {shared
                ? `Listo: «${save.data.name}» ya aparece para todo tu equipo al crear una ${WORDS[kind].one}.`
                : `Listo: «${save.data.name}» quedó en tus plantillas. Solo tú la ves.`}
            </Alert>
            <Button variant="secondary" onClick={() => setMode(null)}>
              Cerrar
            </Button>
          </div>
        ) : (
          <form
            className="flex flex-col gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              save.mutate();
            }}
          >
            {save.error && <Alert>{save.error.message}</Alert>}
            <TextField label="Nombre de la plantilla" value={name} onChange={(e) => setName(e.target.value)} autoFocus required />
            <TextField label="Para qué sirve (opcional)" placeholder="Ej.: primer contacto con bancos" value={description} onChange={(e) => setDescription(e.target.value)} />
            <p className="-mt-2 text-[12.5px] text-ork-mist">
              {shared
                ? `La verá toda tu empresa. Cada quien crea su propia ${WORDS[kind].one} a partir de ella; tu original no cambia.`
                : "Solo tú la ves. Más adelante puedes compartirla con el equipo desde «Empezar desde»."}
            </p>
            <Button type="submit" loading={save.isPending} disabled={name.trim().length < 2}>
              {shared ? "Compartir con el equipo" : "Guardar en mis plantillas"}
            </Button>
          </form>
        )}
      </Modal>
    </>
  );
}
