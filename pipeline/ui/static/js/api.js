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
export const getVersion = () => request('/api/version');
export const listRuns =() => request('/api/runs');
export const getRun = (id) => request(`/api/runs/${enc(id)}`);
export const listArtifacts = (id) => request(`/api/runs/${enc(id)}/artifacts`);
export const artifactUrl = (id, artifactId) => `/api/runs/${enc(id)}/artifacts/${enc(artifactId)}`;
export const getResult = (id) => request(`/api/runs/${enc(id)}/result`);
export const listInputs = () => request('/api/inputs');
export const uploadInput = (file) => request(`/api/inputs/${enc(file.name)}`, { method: 'PUT', body: file });
export const launchRun = (body) => request('/api/runs', {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify(body),
});
const send = (method, path, body) => request(path, {
  method,
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify(body),
});
export const replyRun = (id, pendingId, reply) => send('POST', `/api/runs/${enc(id)}/reply`, { id: pendingId, reply });
export const stopRun = (id) => send('POST', `/api/runs/${enc(id)}/stop`, {});
export const retryRun = (id, autoApprove = false) => send('POST', `/api/runs/${enc(id)}/retry`, { auto_approve: autoApprove });
export const restartRun = (id, autoApprove = false) => send('POST', `/api/runs/${enc(id)}/restart`, { auto_approve: autoApprove });
export const deleteRun = (id) => request(`/api/runs/${enc(id)}`, { method: 'DELETE' });
export const getKnowledge = (limit) => request(limit ? `/api/knowledge?limit=${limit}` : '/api/knowledge');
export const getKnowledgeEntry = (slug) => request(`/api/knowledge/${enc(slug)}`);
export const dismissRun = (id, dismissed = true) => send('POST', `/api/runs/${enc(id)}/dismiss`, { dismissed });
export const getLog = (id, tail = 500) => request(`/api/runs/${enc(id)}/log?tail=${tail}`);
export const getResearch = (id) => request(`/api/runs/${enc(id)}/research`);
export const researchFileUrl = (id, n) => `/api/runs/${enc(id)}/research/${enc(n)}/file`;
export const saveArtifact = (id, artifactId, text) => request(`/api/runs/${enc(id)}/artifacts/${enc(artifactId)}`, { method: 'PUT', body: text });
export const savePackage = (id, target, fields) => send('PUT', `/api/runs/${enc(id)}/publish/${enc(target)}`, { fields });
export const preparePackage = (id, target) => send('POST', `/api/runs/${enc(id)}/publish/${enc(target)}/prepare`, {});
export const packageAssetUrl = (id, target, role) => `/api/runs/${enc(id)}/publish/${enc(target)}/asset/${enc(role)}`;
export const assetPreview = (id, target, role) => request(`${packageAssetUrl(id, target, role)}/preview`);
export const startAssetPreview = (id, target, role) => send('POST', `${packageAssetUrl(id, target, role)}/preview`, {});
export const assetPreviewUrl = (id, target, role) => `${packageAssetUrl(id, target, role)}/preview/file`;
export const publishPackages = (id) => request(`/api/runs/${enc(id)}/publish`);
export const publish = (id, target, fields) => request(`/api/runs/${enc(id)}/publish/${enc(target)}`, {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ fields }),
});
