// Cliente de la API: cookies de sesión del mismo origen, refresco automático y errores legibles.

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

let refreshing: Promise<boolean> | null = null;

async function refreshSession(): Promise<boolean> {
  refreshing ??= fetch("/api/auth/refresh", { method: "POST", credentials: "same-origin" })
    .then((r) => r.ok)
    .catch(() => false)
    .finally(() => {
      setTimeout(() => (refreshing = null), 0);
    });
  return refreshing;
}

// Pase del panel de administración: solo en memoria, así que recargar o volver a entrar
// siempre pide de nuevo las credenciales.
let adminToken: string | null = null;
export const ADMIN_LOCKED_EVENT = "orkesta:admin-locked";
export function setAdminToken(token: string | null) {
  adminToken = token;
}
export function hasAdminToken() {
  return adminToken !== null;
}

export async function api<T>(path: string, options: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, headers, ...rest } = options;
  const init: RequestInit = {
    credentials: "same-origin",
    ...rest,
    headers: {
      Accept: "application/json",
      ...(json !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(adminToken && path.startsWith("/api/admin/") ? { "X-Admin-Token": adminToken } : {}),
      ...headers,
    },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  };
  let response = await fetch(path, init);
  const isAuthCall = path.startsWith("/api/auth/login") || path.startsWith("/api/auth/refresh");
  if (response.status === 401 && !isAuthCall && (await refreshSession())) {
    response = await fetch(path, init);
  }
  if (response.status === 428 && path.startsWith("/api/admin/")) {
    adminToken = null;
    window.dispatchEvent(new Event(ADMIN_LOCKED_EVENT));
  }
  if (!response.ok) {
    let message = "Algo salió mal. Inténtalo de nuevo.";
    try {
      const data = await response.json();
      if (typeof data?.detail === "string") message = data.detail;
    } catch {
      /* respuesta sin cuerpo JSON */
    }
    if (response.status >= 500 && response.status !== 503) message = "El servidor no respondió como esperábamos. Inténtalo de nuevo en un momento.";
    throw new ApiError(message, response.status);
  }
  return (await response.json()) as T;
}

export type Workspace = { id: string; name: string; role: string; role_label: string };

export type Session = {
  user: { id: string; email: string; full_name: string; platform_admin: boolean; has_password: boolean; job_title?: string; avatar_url?: string | null };
  workspace: Workspace | null;
  workspaces: Workspace[];
};
