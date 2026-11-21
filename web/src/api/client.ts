import type {
  AgentMode,
  ExperienceItem,
  ExperienceListResponse,
  ExperienceQueryParams,
  ExperienceStatus,
  JobItem,
  JobListResponse,
  JobQueryParams,
  JobStatus,
  LoginQrcodeResponse,
  NoteSnapshot,
  OverviewStats,
  SessionDetail,
  SessionSummary,
  SystemStatus,
  TraceDetail,
  TraceSummary,
} from './types'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    ...init,
  })
  if (!res.ok) {
    let msg = `请求失败 (${res.status})`
    try {
      const data = await res.json()
      if (typeof data?.detail === 'string') msg = data.detail
    } catch {
      // ignore non-json
    }
    throw new ApiError(res.status, msg)
  }
  return (await res.json()) as T
}

function buildJobQuery(params: JobQueryParams): string {
  const sp = new URLSearchParams()
  if (params.city) sp.set('city', params.city)
  if (params.keyword) sp.set('keyword', params.keyword)
  if (params.graduation_year) sp.set('graduation_year', params.graduation_year)
  if (params.since) sp.set('since', params.since)
  if (params.status) sp.set('status', params.status)
  if (params.only_fresh) sp.set('only_fresh', 'true')
  if (params.needs_image !== undefined && params.needs_image !== null) {
    sp.set('needs_image', String(params.needs_image))
  }
  if (params.page) sp.set('page', String(params.page))
  if (params.page_size) sp.set('page_size', String(params.page_size))
  const qs = sp.toString()
  return qs ? `?${qs}` : ''
}

function buildExperienceQuery(params: ExperienceQueryParams, extra?: Record<string, string>): string {
  const sp = new URLSearchParams()
  if (params.category) sp.set('category', params.category)
  if (params.topic) sp.set('topic', params.topic)
  if (params.keyword) sp.set('keyword', params.keyword)
  if (params.status) sp.set('status', params.status)
  if (params.needs_image !== undefined && params.needs_image !== null) {
    sp.set('needs_image', String(params.needs_image))
  }
  if (params.sort_by) sp.set('sort_by', params.sort_by)
  if (params.page) sp.set('page', String(params.page))
  if (params.page_size) sp.set('page_size', String(params.page_size))
  if (extra) {
    for (const [k, v] of Object.entries(extra)) sp.set(k, v)
  }
  const qs = sp.toString()
  return qs ? `?${qs}` : ''
}

export const api = {
  // System
  getSystemStatus: (refresh = false) =>
    request<SystemStatus>(`/api/system/status${refresh ? '?refresh=true' : ''}`),
  reconnectMcp: () => request<SystemStatus>('/api/system/mcp/reconnect', { method: 'POST' }),
  fetchLoginQrcode: () => request<LoginQrcodeResponse>('/api/system/login/qrcode', { method: 'POST' }),

  // Jobs
  listJobs: (params: JobQueryParams = {}) =>
    request<JobListResponse>(`/api/jobs${buildJobQuery(params)}`),
  getJob: (id: number) => request<JobItem>(`/api/jobs/${id}`),
  updateJobStatus: (id: number, status: JobStatus) =>
    request<JobItem>(`/api/jobs/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ status }),
    }),
  deleteJob: (id: number) =>
    request<{ deleted: boolean; id: number }>(`/api/jobs/${id}`, { method: 'DELETE' }),
  getExportUrl: (params: JobQueryParams = {}) => `/api/jobs/export${buildJobQuery(params)}`,

  // Experiences
  listExperiences: (params: ExperienceQueryParams = {}) =>
    request<ExperienceListResponse>(`/api/experiences${buildExperienceQuery(params)}`),
  getExperience: (id: number) => request<ExperienceItem>(`/api/experiences/${id}`),
  updateExperienceStatus: (id: number, status: ExperienceStatus) =>
    request<ExperienceItem>(`/api/experiences/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ status }),
    }),
  deleteExperience: (id: number) =>
    request<{ deleted: boolean; id: number }>(`/api/experiences/${id}`, { method: 'DELETE' }),
  getExperiencesExportUrl: (params: ExperienceQueryParams = {}, format: 'md' | 'xlsx' = 'md') =>
    `/api/experiences/export${buildExperienceQuery(params, { format })}`,

  // Sessions
  createSession: (opts?: { max_search?: number; max_detail?: number; max_steps?: number; mode?: AgentMode }) =>
    request<SessionSummary>('/api/sessions', {
      method: 'POST',
      body: JSON.stringify(opts ?? {}),
    }),
  listSessions: () => request<SessionSummary[]>('/api/sessions'),
  getSession: (sessionId: string) => request<SessionDetail>(`/api/sessions/${sessionId}`),
  sendMessage: (
    sessionId: string,
    payload: { content: string; max_search?: number; max_detail?: number; max_steps?: number; mode?: AgentMode },
  ) =>
    request<{ run_id: string; session_id: string; turn: number; status: string }>(
      `/api/sessions/${sessionId}/messages`,
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
    ),
  cancelSession: (sessionId: string) =>
    request<{ cancelled: boolean; session_id: string; run_id: string | null }>(
      `/api/sessions/${sessionId}/cancel`,
      { method: 'POST' },
    ),

  // Notes (local cached original post + images)
  getNoteSnapshot: (feedId: string) =>
    request<NoteSnapshot>(`/api/notes/${encodeURIComponent(feedId)}`),

  // Traces & Overview
  listTraces: () => request<TraceSummary[]>('/api/traces'),
  getTraceDetail: (sessionId: string) => request<TraceDetail>(`/api/traces/${sessionId}`),
  getOverviewStats: () => request<OverviewStats>('/api/stats/overview'),
}
