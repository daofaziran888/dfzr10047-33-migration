import { request } from './client'

export type JsonRecord = Record<string, any>

async function postJson<T = JsonRecord>(path: string, body: JsonRecord = {}): Promise<T> {
  const response = await request(path, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(data.detail || data.message || `接口返回 ${response.status}`)
  }
  return data as T
}

async function getJson<T = JsonRecord>(path: string): Promise<T> {
  const response = await request(path)
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(data.detail || data.message || `接口返回 ${response.status}`)
  }
  return data as T
}

export const pathWorkbenchApi = {
  scenario: () => postJson('/api/path-workbench/scenario'),
  getScenario: () => getJson('/api/path-workbench/scenario'),
  previewChoice: (body: JsonRecord) => postJson('/api/path-preview/choice', body),
  previewPath: (body: JsonRecord) => postJson('/api/path-preview', body),
  migrate: (body: JsonRecord) => postJson('/api/path-migrations', body),
  confirm: (body: JsonRecord) => postJson('/api/path-confirmations', body),
  confirmations: (choiceGroup = 'CG-AB-LITERAL') =>
    getJson(`/api/path-confirmations?choice_group=${encodeURIComponent(choiceGroup)}`),
  targetRows: () => getJson('/api/path-target-rows'),
  rule: () => getJson('/api/path-rule'),
  rollback: (body: JsonRecord = {}) => postJson('/api/path-rule/rollback', body),
  deploy: (body: JsonRecord = {}) => postJson('/api/path-rule/deploy', body),
  sourceLocation: (reference: string) =>
    getJson(`/api/source-locations/${encodeURIComponent(reference)}`),
  receipts: () => getJson('/api/cast-receipts'),
  createReceipt: (body: JsonRecord) => postJson('/api/cast-receipts', body),
  receipt: (id: string) => getJson(`/api/cast-receipts/${encodeURIComponent(id)}`),
  claim: (id: string, body: JsonRecord = {}) =>
    postJson(`/api/cast-receipts/${encodeURIComponent(id)}/claim`, body),
  select: (id: string, body: JsonRecord) =>
    postJson(`/api/cast-receipts/${encodeURIComponent(id)}/select`, body),
  execute: (id: string, body: JsonRecord = {}) =>
    postJson(`/api/cast-receipts/${encodeURIComponent(id)}/execute`, body),
  isolation: () => getJson('/api/isolation-ledger'),
  recover: (id: string) =>
    postJson(`/api/isolation-records/${encodeURIComponent(id)}/recover`),
}
