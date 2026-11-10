import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CalendarDays,
  ExternalLink,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
  Video,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  Alert,
  Badge,
  Button,
  Card,
  Modal,
  PageHeader,
  SelectField,
  Spinner,
  TextField,
  cx,
} from "../components/ui";
import { api } from "../lib/api";
import { ContactPicker } from "../components/ContactPicker";

type Status = {
  available: boolean;
  connected: boolean;
  email: string | null;
  synced_at: string | null;
  error: string | null;
};
type Attendee = { email: string; name: string; status: string };
export type CalEvent = {
  id: string;
  title: string;
  description: string;
  location: string;
  start_at: string;
  end_at: string;
  all_day: boolean;
  attendees: Attendee[];
  meet_url: string;
  html_link: string;
  source: "google" | "orkesta";
  contact: {
    id: string;
    full_name: string;
    company_name: string | null;
  } | null;
  deal_id: string | null;
  campaign_id: string | null;
};

const DAY = new Intl.DateTimeFormat("es", {
  weekday: "long",
  day: "numeric",
  month: "long",
});
const HOUR = new Intl.DateTimeFormat("es", {
  hour: "2-digit",
  minute: "2-digit",
});
const pad = (n: number) => String(n).padStart(2, "0");
const localDate = (d: Date) =>
  `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const localTime = (d: Date) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;

export function CalendarPage() {
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const status = useQuery({
    queryKey: ["calendar-status"],
    queryFn: () => api<Status>("/api/calendar/status"),
  });
  const connected = Boolean(status.data?.connected);
  const events = useQuery({
    queryKey: ["calendar-events"],
    queryFn: () => api<CalEvent[]>("/api/calendar/events"),
    enabled: connected,
    refetchInterval: 60000,
  });
  const sync = useMutation({
    mutationFn: () =>
      api<{ ok: boolean; error: string | null }>("/api/calendar/sync", {
        method: "POST",
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["calendar-events"] });
      client.invalidateQueries({ queryKey: ["calendar-status"] });
    },
  });
  // Al entrar, trae lo último de Google (además de la sincronización automática cada 5 min).
  useEffect(() => {
    if (connected) sync.mutate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connected]);
  const [editing, setEditing] = useState<CalEvent | "new" | null>(null);
  const remove = useMutation({
    mutationFn: (id: string) =>
      api(`/api/calendar/events/${id}`, { method: "DELETE" }),
    onSuccess: () =>
      client.invalidateQueries({ queryKey: ["calendar-events"] }),
  });
  const days = useMemo(() => {
    const groups = new Map<string, CalEvent[]>();
    for (const e of events.data ?? []) {
      const key = localDate(new Date(e.start_at));
      groups.set(key, [...(groups.get(key) ?? []), e]);
    }
    return [...groups.entries()];
  }, [events.data]);
  const today = localDate(new Date());
  const justConnected = params.get("gmail");

  return (
    <div>
      <PageHeader
        title="Calendario"
        description="Tu Google Calendar dentro de ORKESTA: lo que agendas aquí aparece en Google y lo de Google aparece aquí. Si un lead de tus campañas agenda una reunión, se detiene su secuencia y se crea la oportunidad."
        action={
          connected && (
            <div className="flex gap-2">
              <Button
                variant="secondary"
                icon={<RefreshCw className="size-4" aria-hidden />}
                loading={sync.isPending}
                onClick={() => sync.mutate()}
              >
                Sincronizar
              </Button>
              <Button
                icon={<Plus className="size-4" aria-hidden />}
                onClick={() => setEditing("new")}
              >
                Nueva reunión
              </Button>
            </div>
          )
        }
      />
      {justConnected === "conectado" && connected && (
        <div className="mb-4">
          <Alert tone="ok">
            Calendario conectado.{" "}
            <button
              type="button"
              className="font-semibold underline"
              onClick={() => setParams({})}
            >
              Cerrar
            </button>
          </Alert>
        </div>
      )}
      {status.isLoading ? (
        <Spinner />
      ) : !connected ? (
        <ConnectCard status={status.data} />
      ) : (
        <>
          {(status.data?.error || sync.data?.error || sync.error) && (
            <div className="mb-4">
              <Alert>
                {sync.data?.error ?? sync.error?.message ?? status.data?.error}
              </Alert>
            </div>
          )}
          <p className="mb-3 text-[12.5px] text-ork-mist">
            Calendario de {status.data?.email}
            {status.data?.synced_at &&
              ` · actualizado ${HOUR.format(new Date(status.data.synced_at))}`}
          </p>
          {events.isLoading ? (
            <Spinner />
          ) : days.length === 0 ? (
            <Card>
              <p className="py-6 text-center text-[13.5px] text-ork-mist">
                No tienes reuniones en los próximos dos meses.
              </p>
            </Card>
          ) : (
            <div className="flex flex-col gap-5">
              {days.map(([day, items]) => (
                <section
                  key={day}
                  aria-label={DAY.format(new Date(`${day}T12:00:00`))}
                >
                  <h2
                    className={cx(
                      "mb-2 text-[13px] font-bold uppercase tracking-wide",
                      day === today ? "text-ork-blue" : "text-ork-mist",
                    )}
                  >
                    {day === today ? "Hoy · " : ""}
                    {DAY.format(new Date(`${day}T12:00:00`))}
                  </h2>
                  <ul className="flex flex-col gap-2">
                    {items.map((e) => (
                      <EventRow
                        key={e.id}
                        event={e}
                        onEdit={() => setEditing(e)}
                        onDelete={() => remove.mutate(e.id)}
                        deleting={remove.isPending && remove.variables === e.id}
                      />
                    ))}
                  </ul>
                </section>
              ))}
            </div>
          )}
        </>
      )}
      {editing && (
        <EventModal
          event={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}

function ConnectCard({ status }: { status?: Status }) {
  return (
    <Card>
      <div className="flex flex-col items-start gap-3 py-2">
        <CalendarDays className="size-8 text-ork-blue" aria-hidden />
        <p className="text-[15px] font-semibold text-ork-ink">
          Conecta tu calendario de Google
        </p>
        <p className="max-w-xl text-[13.5px] text-ork-graphite">
          Usa la misma cuenta de Gmail con la que envías tus campañas. En la
          pantalla de Google, marca también el permiso de calendario. Tus
          reuniones son privadas: solo tú las ves en ORKESTA.
        </p>
        {status?.available === false ? (
          <Alert>
            La conexión con Google aún no está configurada en ORKESTA. Pídesela
            a la administración.
          </Alert>
        ) : (
          <a
            href="/api/mail/google/start?volver=calendario"
            className="ork-gradient rounded-xl px-4 py-2 text-[14px] font-semibold text-white hover:brightness-110"
          >
            Conectar mi calendario de Google
          </a>
        )}
      </div>
    </Card>
  );
}

function EventRow({
  event: e,
  onEdit,
  onDelete,
  deleting,
}: {
  event: CalEvent;
  onEdit: () => void;
  onDelete: () => void;
  deleting: boolean;
}) {
  const [confirm, setConfirm] = useState(false);
  const start = new Date(e.start_at);
  const end = new Date(e.end_at);
  return (
    <li className="flex flex-wrap items-start justify-between gap-3 rounded-xl border border-ork-line bg-ork-card px-4 py-3">
      <div className="flex min-w-0 gap-3">
        <p className="w-24 shrink-0 text-[13px] font-semibold text-ork-graphite">
          {e.all_day
            ? "Todo el día"
            : `${HOUR.format(start)} – ${HOUR.format(end)}`}
        </p>
        <div className="min-w-0">
          <p className="font-semibold text-ork-ink">{e.title}</p>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[12.5px] text-ork-graphite">
            {e.campaign_id && (
              <Badge tone="ok">La agendó un lead de tu campaña</Badge>
            )}
            {e.contact && (
              <Link
                to={`/contactos?contacto=${e.contact.id}`}
                className="font-semibold text-ork-blue hover:underline"
              >
                {e.contact.full_name}
                {e.contact.company_name ? ` · ${e.contact.company_name}` : ""}
              </Link>
            )}
            {e.deal_id && (
              <Link
                to={`/crm?oportunidad=${e.deal_id}`}
                className="font-semibold text-ork-blue hover:underline"
              >
                Ver oportunidad →
              </Link>
            )}
            {!e.contact && e.attendees.length > 0 && (
              <span>
                {e.attendees.map((a) => a.name || a.email).join(", ")}
              </span>
            )}
          </div>
          {e.location && (
            <p className="mt-0.5 text-[12px] text-ork-mist">{e.location}</p>
          )}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        {e.meet_url && (
          <a
            href={e.meet_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[12.5px] font-semibold text-ork-blue hover:bg-ork-blue/5"
          >
            <Video className="size-3.5" aria-hidden /> Meet
          </a>
        )}
        {e.html_link && (
          <a
            href={e.html_link}
            target="_blank"
            rel="noreferrer"
            aria-label="Abrir en Google Calendar"
            className="rounded-lg p-1.5 text-ork-mist hover:text-ork-ink"
          >
            <ExternalLink className="size-4" aria-hidden />
          </a>
        )}
        <Button
          size="sm"
          variant="ghost"
          aria-label="Editar"
          icon={<Pencil className="size-4" aria-hidden />}
          onClick={onEdit}
        />
        {confirm ? (
          <span className="flex items-center gap-1 text-[12.5px]">
            ¿Cancelarla?
            <Button
              size="sm"
              variant="secondary"
              loading={deleting}
              onClick={onDelete}
            >
              Sí
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirm(false)}>
              No
            </Button>
          </span>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            aria-label="Cancelar reunión"
            icon={<Trash2 className="size-4" aria-hidden />}
            onClick={() => setConfirm(true)}
          />
        )}
      </div>
    </li>
  );
}

/** Crear o editar una reunión: se guarda primero en Google Calendar y luego en ORKESTA. */
export function EventModal({
  event,
  contactId,
  onClose,
}: {
  event: CalEvent | null;
  contactId?: string;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const base = event ? new Date(event.start_at) : nextSlot();
  const [title, setTitle] = useState(event?.title ?? "");
  const [day, setDay] = useState(localDate(base));
  const [time, setTime] = useState(localTime(base));
  const [minutes, setMinutes] = useState(
    event
      ? Math.max(15, Math.round((+new Date(event.end_at) - +base) / 60000))
      : 30,
  );
  const [contacts, setContacts] = useState<string[]>(
    event?.contact ? [event.contact.id] : contactId ? [contactId] : [],
  );
  const [guests, setGuests] = useState("");
  const [description, setDescription] = useState(event?.description ?? "");
  const [meet, setMeet] = useState(!event);
  const [notify, setNotify] = useState(true);
  const save = useMutation({
    mutationFn: () => {
      const start = new Date(`${day}T${time}`);
      const end = new Date(start.getTime() + minutes * 60000);
      const attendees = guests
        .split(/[,;\s]+/)
        .map((x) => x.trim())
        .filter(Boolean)
        .map((email) => ({ email }));
      const json = {
        title,
        description,
        start_at: start.toISOString(),
        end_at: end.toISOString(),
        contact_id: contacts[0] ?? null,
        meet,
        notify,
        // Se conservan los invitados que ya tenía (el contacto se agrega solo, sin duplicarse).
        attendees: [
          ...(event?.attendees ?? []).map((a) => ({
            email: a.email,
            name: a.name,
          })),
          ...attendees,
        ],
      };
      return event
        ? api(`/api/calendar/events/${event.id}`, { method: "PATCH", json })
        : api("/api/calendar/events", { method: "POST", json });
    },
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["calendar-events"] });
      onClose();
    },
  });
  return (
    <Modal
      open
      onOpenChange={(o) => !o && onClose()}
      title={event ? "Editar reunión" : "Nueva reunión"}
      description="Se guarda también en tu Google Calendar."
    >
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        {save.error && <Alert>{save.error.message}</Alert>}
        <TextField
          label="Título"
          required
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Reunión con…"
        />
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <TextField
            label="Fecha"
            type="date"
            required
            value={day}
            onChange={(e) => setDay(e.target.value)}
          />
          <TextField
            label="Hora"
            type="time"
            required
            value={time}
            onChange={(e) => setTime(e.target.value)}
          />
          <SelectField
            label="Duración"
            value={String(minutes)}
            onChange={(e) => setMinutes(Number(e.target.value))}
          >
            {[15, 30, 45, 60, 90, 120].map((m) => (
              <option key={m} value={m}>
                {m < 60 ? `${m} min` : `${m / 60} h`}
              </option>
            ))}
          </SelectField>
        </div>
        <div>
          <p className="mb-1 text-[13px] font-semibold text-ork-ink">
            Contacto (opcional)
          </p>
          <ContactPicker
            ids={contacts}
            onChange={(ids) => setContacts(ids.slice(-1))}
          />
        </div>
        <TextField
          label="Otros invitados (opcional)"
          placeholder="correo@empresa.com, otro@empresa.com"
          value={guests}
          onChange={(e) => setGuests(e.target.value)}
        />
        <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-ork-ink">
          Notas (opcional)
          <textarea
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            className="ork-focus rounded-xl border border-ork-line bg-ork-card px-3 py-2 text-[14px] font-normal"
          />
        </label>
        {!event?.meet_url && (
          <label className="flex items-center gap-2 text-[13px] text-ork-graphite">
            <input
              type="checkbox"
              checked={meet}
              onChange={(e) => setMeet(e.target.checked)}
              className="size-4 accent-[#2563EB]"
            />
            Agregar videollamada de Google Meet
          </label>
        )}
        <label className="flex items-center gap-2 text-[13px] text-ork-graphite">
          <input
            type="checkbox"
            checked={notify}
            onChange={(e) => setNotify(e.target.checked)}
            className="size-4 accent-[#2563EB]"
          />
          Enviar la invitación a los invitados
        </label>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button type="submit" loading={save.isPending}>
            {event ? "Guardar" : "Agendar"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function nextSlot() {
  const d = new Date();
  d.setMinutes(d.getMinutes() < 30 ? 30 : 60, 0, 0);
  return d;
}
