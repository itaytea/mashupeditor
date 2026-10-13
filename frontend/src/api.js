async function request(method, path, body) {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      /* not JSON */
    }
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  config: () => request("GET", "/config"),
  spotifyLogout: () => request("POST", "/spotify/logout"),
  library: () => request("GET", "/library"),
  scan: (folder) => request("POST", "/library/scan", { folder }),
  playlist: () => request("GET", "/playlist"),
  loadPlaylist: (url) => request("POST", "/playlist", { url }),
  pairs: (scope, limit = 100, focus = null, owned = false) =>
    request(
      "GET",
      `/pairs?scope=${scope}&limit=${limit}&owned=${owned}${focus ? `&focus=${encodeURIComponent(focus)}` : ""}`,
    ),
  lookup: () => request("POST", "/lookup"),
  find: (folder, watch) => request("POST", "/find", { folder, watch }),
  setWatch: (watch) => request("POST", "/find/watch", { watch }),
  cancelJob: (id) => request("POST", `/jobs/${id}/cancel`),
  createDraft: (body) => request("POST", "/drafts", body),
  drafts: () => request("GET", "/drafts"),
  jobs: () => request("GET", "/jobs"),
};

export const youtubeUrl = (artist, title) =>
  `https://www.youtube.com/results?search_query=${encodeURIComponent(`${artist} ${title}`.trim())}`;

export const draftUrl = (draftId, file) => `/api/drafts/${draftId}/${file}`;
