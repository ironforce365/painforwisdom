// One function per C3 endpoint (spec 2026-10-31-pipeline-console-ui, Contracts C3).
const healthListeners = new Set();

/** fn(ok): false on a network failure or 5xx, true on any other response. */
export function onHealth(fn) {
  healthListeners.add(fn);
}

export class ApiError extends Error {
  constructor(status, body) {
    super(`HTTP ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }
}

async function request(path, init) {
  let res;
  try {
    res = await fetch(path, init);
  } catch (e) {
    healthListeners.forEach((fn) => fn(false));
    throw e;
  }
  healthListeners.forEach((fn) => fn(res.status < 500));
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, body);
  return body;
}

const enc = encodeURIComponent;

export const getGraph = () => request('/api/graph');
export const listRuns = () => request('/api/runs');
export const getRun = (id) => request(`/api/runs/${enc(id)}`);
export const listArtifacts = (id) => request(`/api/runs/${enc(id)}/artifacts`);
export const artifactUrl = (id, artifactId) => `/api/runs/${enc(id)}/artifacts/${enc(artifactId)}`;
export const getResult = (id) => request(`/api/runs/${enc(id)}/result`);
export const listInputs = () => request('/api/inputs');
export const launchRun = (body) => request('/api/runs', {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify(body),
});
