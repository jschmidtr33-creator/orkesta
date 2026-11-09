import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { UserRoundCog } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import { useSession } from "../lib/session";
import { Alert, Button, Modal, SelectField, TextField } from "./ui";

type Person = { user_id: string; name: string; email: string; job_title: string; role: string };
type Result = { moved: { contacts: number; deals: number; tasks: number }; to: { name: string } };

/** Pasar contactos u oportunidades a otra persona del equipo, eligiéndola de una lista. */
export function ReassignButton({ kind, ids, onDone, size = "sm" }: { kind: "contact" | "deal"; ids: string[]; onDone?: () => void; size?: "sm" | "md" }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button size={size} variant="secondary" icon={<UserRoundCog className="size-3.5" aria-hidden />} onClick={() => setOpen(true)} disabled={!ids.length}>
        Reasignar
      </Button>
      {open && <ReassignModal kind={kind} ids={ids} onClose={() => setOpen(false)} onDone={onDone} />}
    </>
  );
}

function ReassignModal({ kind, ids, onClose, onDone }: { kind: "contact" | "deal"; ids: string[]; onClose: () => void; onDone?: () => void }) {
  const client = useQueryClient();
  const { session } = useSession();
  const people = useQuery({ queryKey: ["reassign-people"], queryFn: () => api<Person[]>("/api/crm/reassign/people") });
  const [to, setTo] = useState("");
  const [reason, setReason] = useState("");
  const [related, setRelated] = useState(true);
  const send = useMutation({
    mutationFn: () => api<Result>("/api/crm/reassign", { method: "POST", json: { kind, ids, to_user_id: to, reason, with_related: related } }),
    onSuccess: () => {
      for (const key of ["contacts", "crm-deals", "crm-deal", "crm-stages", "crm-tasks"]) client.invalidateQueries({ queryKey: [key] });
      onDone?.();
    },
  });
  const n = ids.length;
  const what = kind === "contact" ? (n === 1 ? "1 contacto" : `${n} contactos`) : n === 1 ? "1 oportunidad" : `${n} oportunidades`;
  const others = (people.data ?? []).filter((p) => p.user_id !== session?.user.id);
  const r = send.data;
  return (
    <Modal open onOpenChange={(o) => !o && onClose()} title={`Reasignar ${what}`} description="Elige a quién del equipo pasarle la cuenta. Recibe un aviso y queda registrado.">
      {r ? (
        <div className="flex flex-col gap-4">
          <Alert tone="ok">
            Listo: pasaste a {r.to.name} {kind === "contact" ? `${r.moved.contacts} contacto${r.moved.contacts === 1 ? "" : "s"}` : ""}
            {kind === "contact" && r.moved.deals ? `, ${r.moved.deals} oportunidad${r.moved.deals === 1 ? "" : "es"}` : ""}
            {kind === "deal" ? `${r.moved.deals} oportunidad${r.moved.deals === 1 ? "" : "es"}` : ""}
            {r.moved.tasks ? ` y ${r.moved.tasks} tarea${r.moved.tasks === 1 ? "" : "s"} pendiente${r.moved.tasks === 1 ? "" : "s"}` : ""}.
          </Alert>
          <Button variant="secondary" onClick={onClose}>
            Cerrar
          </Button>
        </div>
      ) : (
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            send.mutate();
          }}
        >
          {send.error && <Alert>{send.error.message}</Alert>}
          <SelectField label="Pasar a" required value={to} onChange={(e) => setTo(e.target.value)}>
            <option value="">{people.isLoading ? "Cargando el equipo…" : "Elige a una persona…"}</option>
            {others.map((p) => (
              <option key={p.user_id} value={p.user_id}>
                {p.name}
                {p.job_title ? ` · ${p.job_title}` : ""}
              </option>
            ))}
          </SelectField>
          <TextField label="Motivo (opcional)" placeholder="Ej.: cubre la cuenta mientras estoy de vacaciones" value={reason} onChange={(e) => setReason(e.target.value)} />
          <label className="flex items-start gap-2 text-[13px] text-ork-graphite">
            <input type="checkbox" className="mt-0.5 size-4 accent-ork-blue" checked={related} onChange={(e) => setRelated(e.target.checked)} />
            <span>{kind === "contact" ? "Pasar también sus oportunidades abiertas y tareas pendientes" : "Pasar también sus tareas pendientes"}</span>
          </label>
          {kind === "contact" && <p className="-mt-2 text-[12px] text-ork-mist">Si el contacto está en una campaña activa, la campaña sigue saliendo desde el buzón de quien la creó hasta terminar.</p>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={onClose}>
              Cancelar
            </Button>
            <Button type="submit" loading={send.isPending} disabled={!to}>
              Reasignar
            </Button>
          </div>
        </form>
      )}
    </Modal>
  );
}
