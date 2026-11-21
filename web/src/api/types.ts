export type JobStatus = 'new' | 'interested' | 'applied' | 'rejected' | 'expired'
export type AgentMode = 'auto' | 'jobs' | 'experience'
export type ExperienceCategory = 'roadmap' | 'project' | 'interview' | 'tips'
export type ExperienceStatus = 'unread' | 'reading' | 'starred' | 'archived'

export interface QuotaItem {
  used: number
  limit: number
}

export interface DailyUsageSummary {
  day: string
  search: QuotaItem
  detail: QuotaItem
}

export interface SystemStatus {
  mcp_url: string
  mcp_connected: boolean
  logged_in: boolean
  login_message: string
  mcp_error: string | null
  validated_tools: string[]
  db_path: string
  db_jobs_count: number
  db_experiences_count: number
  active_session_id: string | null
  daily_usage: DailyUsageSummary
  config_summary: {
    agent_model?: string
    extract_model?: string
    max_steps?: number
    max_search_calls?: number
    max_detail_calls?: number
    min_interval_sec?: number
    jitter_sec?: number
  }
}

export interface LoginQrcodeResponse {
  image_base64: string | null
  hint_text: string
}

export interface JobItem {
  id: number
  company: string | null
  position: string | null
  city: string | null
  education: string | null
  graduation_year: string | null
  skills: string[]
  internship_duration: string | null
  attendance_days: string | null
  salary: string | null
  deadline: string | null
  apply_method: string | null
  application_url: string | null
  evidence: string
  confidence: number
  note_id: string
  source_url: string
  note_title: string | null
  note_author: string | null
  note_published_at: string | null
  crawled_at: string
  needs_image: boolean
  maybe_expired: boolean
  extra_sources: string[]
  status: JobStatus
}

export interface JobFacets {
  cities: string[]
  status_counts: Record<JobStatus, number>
}

export interface JobListResponse {
  total: number
  page: number
  page_size: number
  items: JobItem[]
  facets: JobFacets
}

export interface JobQueryParams {
  city?: string
  keyword?: string
  graduation_year?: string
  since?: string
  status?: JobStatus | ''
  only_fresh?: boolean
  needs_image?: boolean | null
  page?: number
  page_size?: number
}

export interface CostEstimate {
  currency: string
  cache_hit_cost: number
  cache_miss_cost: number
  output_cost: number
  total_cost: number
  cache_savings: number
}

export interface TokenUsage {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cache_hit_tokens: number
  cache_miss_tokens: number
}

export interface SessionMessage {
  role: 'user' | 'assistant'
  content: string
  turn: number
  created_at: string
  meta?: {
    run_id?: string
    stop_reason?: string
    steps?: number
    llm_calls?: number
    extract_llm_calls?: number
    total_llm_calls?: number
    total_usage?: TokenUsage
    cost?: CostEstimate
    tool_calls?: Record<string, number>
    newly_saved_jobs?: JobItem[]
    newly_saved_experiences?: ExperienceItem[]
    mode?: AgentMode
    error?: string
  }
}

export interface ExperienceItem {
  id: number
  note_id: string
  category: ExperienceCategory
  category_label: string
  topic: string
  title: string
  target_audience: string | null
  summary: string
  key_points: string[]
  tech_stack: string[]
  resources: string[]
  pitfalls: string[]
  evidence: string
  confidence: number
  source_url: string
  note_title: string | null
  note_author: string | null
  note_published_at: string | null
  liked_count: number | null
  collected_count: number | null
  crawled_at: string
  needs_image: boolean
  status: ExperienceStatus
}

export interface ExperienceFacets {
  category_counts: Record<ExperienceCategory, number>
  status_counts: Record<ExperienceStatus, number>
  topics: string[]
}

export interface ExperienceListResponse {
  total: number
  page: number
  page_size: number
  items: ExperienceItem[]
  facets: ExperienceFacets
}

export interface ExperienceQueryParams {
  category?: ExperienceCategory | ''
  topic?: string
  keyword?: string
  status?: ExperienceStatus | ''
  needs_image?: boolean | null
  sort_by?: 'latest' | 'likes'
  page?: number
  page_size?: number
}

export interface SessionSummary {
  session_id: string
  created_at: string
  status: 'idle' | 'running' | 'completed' | 'cancelled' | 'error'
  turns: number
  total_llm_calls: number
  total_tokens: number
  total_cost_cny: number
  last_message: string
  latest_run_id: string | null
  trace_path: string
}

export interface SSEEventPacket {
  event: string
  data: Record<string, unknown>
  ts?: string
}

export interface SessionDetail extends SessionSummary {
  total_usage: TokenUsage
  cost: CostEstimate
  tool_calls: Record<string, number>
  messages: SessionMessage[]
  latest_events: SSEEventPacket[]
}

export interface TraceSummary {
  session_id: string
  started_at: string | null
  mode: string
  task: string
  turns: number
  steps: number
  llm_calls: number
  total_tokens: number
  cache_hit_tokens: number
  cache_hit_rate: number
  total_cost_cny: number
  cache_savings_cny: number
  stop_reason: string
  event_count: number
}

export interface TraceDetail {
  session_id: string
  event_count: number
  events: Record<string, unknown>[]
}

export interface OverviewStats {
  total_jobs: number
  status_counts: Record<string, number>
  total_experiences: number
  experience_category_counts: Record<string, number>
  total_sessions: number
  total_tokens: number
  total_cost_cny: number
  total_cache_savings_cny: number
  avg_cache_hit_rate: number
  daily_usage: DailyUsageSummary
}
