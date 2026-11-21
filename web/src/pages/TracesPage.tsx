import { useCallback, useEffect, useState } from 'react'
import {
  Activity,
  Coins,
  Database,
  History,
  Loader2,
  RefreshCw,
  Shield,
  Zap,
} from 'lucide-react'
import { api } from '../api/client'
import type { OverviewStats, TraceDetail, TraceSummary } from '../api/types'

function formatTime(iso: string | null): string {
  if (!iso) return '-'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function TracesPage() {
  const [stats, setStats] = useState<OverviewStats | null>(null)
  const [traces, setTraces] = useState<TraceSummary[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<TraceDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingDetail, setLoadingDetail] = useState(false)

  const loadAll = useCallback(async () => {
    setLoading(true)
    try {
      const [ov, list] = await Promise.all([api.getOverviewStats(), api.listTraces()])
      setStats(ov)
      setTraces(list)
      if (list.length > 0 && !selectedId) {
        setSelectedId(list[0].session_id)
      }
    } finally {
      setLoading(false)
    }
  }, [selectedId])

  useEffect(() => {
    void loadAll()
  }, [loadAll])

  useEffect(() => {
    if (!selectedId) {
      setDetail(null)
      return
    }
    setLoadingDetail(true)
    api
      .getTraceDetail(selectedId)
      .then((res) => setDetail(res))
      .catch(() => setDetail(null))
      .finally(() => setLoadingDetail(false))
  }, [selectedId])

  const searchPct = stats
    ? Math.min(100, Math.round((stats.daily_usage.search.used / Math.max(1, stats.daily_usage.search.limit)) * 100))
    : 0
  const detailPct = stats
    ? Math.min(100, Math.round((stats.daily_usage.detail.used / Math.max(1, stats.daily_usage.detail.limit)) * 100))
    : 0

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* 顶部标题 + KPI 卡片 */}
      <div className="border-b border-slate-200 bg-white px-6 py-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-slate-900">监控与 Trace 回放</h1>
            <p className="text-xs text-slate-500">
              汇总所有 CLI 与 Web 历史运行轨迹（traces/*.jsonl）、DeepSeek 缓存成本与今日防风控配额
            </p>
          </div>
          <button
            onClick={() => void loadAll()}
            className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            刷新
          </button>
        </div>

        {stats && (
          <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <KpiCard
              icon={<Database className="h-4 w-4 text-sky-600" />}
              label="本地岗位库"
              value={`${stats.total_jobs} 条`}
              sub={`感兴趣 ${stats.status_counts.interested ?? 0} · 已投递 ${stats.status_counts.applied ?? 0}`}
            />
            <KpiCard
              icon={<Coins className="h-4 w-4 text-rose-600" />}
              label="累计估算费用"
              value={`¥${stats.total_cost_cny.toFixed(4)}`}
              sub={`缓存共省 ¥${stats.total_cache_savings_cny.toFixed(4)}`}
            />
            <KpiCard
              icon={<Zap className="h-4 w-4 text-emerald-600" />}
              label="平均缓存命中率"
              value={`${(stats.avg_cache_hit_rate * 100).toFixed(1)}%`}
              sub={`共 ${stats.total_sessions} 次会话 · ${(stats.total_tokens / 1000).toFixed(1)}k tok`}
            />
            <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-3.5">
              <div className="flex items-center gap-1.5 text-xs font-medium text-slate-600">
                <Shield className="h-4 w-4 text-amber-600" />
                今日防风控配额 ({stats.daily_usage.day})
              </div>
              <div className="mt-2 space-y-1.5">
                <QuotaBar
                  label="搜索"
                  used={stats.daily_usage.search.used}
                  limit={stats.daily_usage.search.limit}
                  pct={searchPct}
                />
                <QuotaBar
                  label="详情"
                  used={stats.daily_usage.detail.used}
                  limit={stats.daily_usage.detail.limit}
                  pct={detailPct}
                />
              </div>
            </div>
          </div>
        )}
      </div>

      {/* 下半部：左侧会话列表 + 右侧事件时间线回放 */}
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <div className="w-80 shrink-0 overflow-y-auto border-r border-slate-200 bg-white p-3">
          <div className="mb-2 flex items-center gap-1.5 px-2 text-xs font-semibold text-slate-500">
            <History className="h-3.5 w-3.5" />
            历史会话 ({traces.length})
          </div>
          {traces.length === 0 ? (
            <div className="px-2 py-8 text-center text-xs text-slate-400">暂无 Trace 文件</div>
          ) : (
            <div className="space-y-1.5">
              {traces.map((t) => {
                const active = t.session_id === selectedId
                return (
                  <button
                    key={t.session_id}
                    onClick={() => setSelectedId(t.session_id)}
                    className={`w-full cursor-pointer rounded-xl border p-3 text-left transition ${
                      active
                        ? 'border-rose-300 bg-rose-50/50'
                        : 'border-slate-200/80 bg-white hover:bg-slate-50'
                    }`}
                  >
                    <div className="flex items-center justify-between font-mono text-[11px]">
                      <span className="font-semibold text-slate-800">{t.session_id}</span>
                      <span className="text-slate-400">{formatTime(t.started_at)}</span>
                    </div>
                    <p className="mt-1 line-clamp-2 text-xs text-slate-700">
                      {t.task || '(未记录任务描述)'}
                    </p>
                    <div className="mt-2 flex items-center justify-between font-mono text-[10px] text-slate-500">
                      <span>
                        {t.llm_calls}次LLM · 缓存{(t.cache_hit_rate * 100).toFixed(0)}%
                      </span>
                      <span className="font-semibold text-slate-700">¥{t.total_cost_cny.toFixed(4)}</span>
                    </div>
                  </button>
                )
              })}
            </div>
          )}
        </div>

        <div className="flex-1 overflow-y-auto bg-slate-50/70 p-5">
          {loadingDetail ? (
            <div className="flex h-40 items-center justify-center text-xs text-slate-400">
              <Loader2 className="mr-2 h-4 w-4 animate-spin text-rose-500" />
              正在加载轨迹事件…
            </div>
          ) : !detail ? (
            <div className="flex h-40 items-center justify-center text-xs text-slate-400">
              请选择左侧会话查看详细轨迹
            </div>
          ) : (
            <div className="mx-auto max-w-3xl space-y-2.5">
              <div className="flex items-center justify-between">
                <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                  <Activity className="h-4 w-4 text-rose-600" />
                  会话 {detail.session_id} · 共 {detail.event_count} 条事件
                </h2>
              </div>

              {detail.events.map((ev, idx) => (
                <div
                  key={idx}
                  className="rounded-xl border border-slate-200 bg-white px-4 py-3 text-xs shadow-2xs"
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="rounded bg-slate-100 px-2 py-0.5 font-mono text-[11px] font-semibold text-slate-700">
                        {String(ev.type ?? 'event')}
                      </span>
                      {ev.step !== undefined && (
                        <span className="font-mono text-[11px] text-slate-400">
                          Step {String(ev.step)}
                        </span>
                      )}
                      {ev.tool !== undefined && (
                        <span className="font-mono text-[11px] font-medium text-sky-700">
                          {String(ev.tool)}
                        </span>
                      )}
                    </div>
                    <span className="font-mono text-[10px] text-slate-400">
                      {ev.duration_ms !== undefined ? `${String(ev.duration_ms)}ms · ` : ''}
                      {formatTime(typeof ev.ts === 'string' ? ev.ts : null)}
                    </span>
                  </div>
                  <pre className="mt-2 max-h-48 overflow-auto rounded-lg bg-slate-50 p-2.5 font-mono text-[11px] leading-relaxed text-slate-700">
                    {JSON.stringify(ev, null, 2)}
                  </pre>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function KpiCard({
  icon,
  label,
  value,
  sub,
}: {
  icon: React.ReactNode
  label: string
  value: string
  sub: string
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-3.5">
      <div className="flex items-center gap-1.5 text-xs font-medium text-slate-600">
        {icon}
        {label}
      </div>
      <div className="mt-1.5 text-xl font-bold text-slate-900">{value}</div>
      <div className="mt-0.5 text-[11px] text-slate-500">{sub}</div>
    </div>
  )
}

function QuotaBar({
  label,
  used,
  limit,
  pct,
}: {
  label: string
  used: number
  limit: number
  pct: number
}) {
  const color = pct >= 100 ? 'bg-rose-500' : pct >= 80 ? 'bg-amber-500' : 'bg-emerald-500'
  return (
    <div className="text-[11px]">
      <div className="flex justify-between text-slate-600">
        <span>{label}</span>
        <span className="font-mono">
          {used}/{limit}
        </span>
      </div>
      <div className="mt-0.5 h-1.5 w-full overflow-hidden rounded-full bg-slate-200">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}
