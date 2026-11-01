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

export async function api<T>(path: string, options: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, headers, ...rest } = options;
  const init: RequestInit = {
    credentials: "same-origin",
    ...rest,
    headers: {
      Accept: "application/json",
      ...(json !== undefined ? { "Content-Type": "application/json" } : {}),
      ...headers,
    },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  };
  let response = await fetch(path, init);
  const isAuthCall = path.startsWith("/api/auth/login") || path.startsWith("/api/auth/refresh");
  if (response.status === 401 && !isAuthCall && (await refreshSession())) {
    response = await fetch(path, init);
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
  user: { id: string; email: string; full_name: string; platform_admin: boolean; has_password: boolean };
  workspace: Workspace | null;
  workspaces: Workspace[];
};
