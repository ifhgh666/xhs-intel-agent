import {
  AlertCircle,
  BookOpen,
  CheckCircle2,
  Cpu,
  Database,
  Download,
  FileSearch,
  FileText,
  Loader2,
  Search,
  ShieldCheck,
  Sparkles,
  Tag,
  XCircle,
} from 'lucide-react'
import type { SSEEventPacket } from '../api/types'

interface ExecutionTimelineProps {
  events: SSEEventPacket[]
  isRunning: boolean
}

const TOOL_LABELS: Record<string, { label: string; icon: React.ReactNode }> = {
  search_notes: { label: '搜索小红书笔记', icon: <Search className="h-3.5 w-3.5" /> },
  get_note_detail: { label: '预览笔记详情', icon: <FileSearch className="h-3.5 w-3.5" /> },
  extract_and_save_jobs: { label: '抽取岗位并入库', icon: <Database className="h-3.5 w-3.5" /> },
  query_jobs: { label: '查询本地岗位库', icon: <Sparkles className="h-3.5 w-3.5" /> },
  export_jobs: { label: '导出岗位 Excel', icon: <Download className="h-3.5 w-3.5" /> },
  mark_job: { label: '更新岗位状态', icon: <Tag className="h-3.5 w-3.5" /> },
  extract_and_save_experience: { label: '抽取干货并入经验库', icon: <BookOpen className="h-3.5 w-3.5" /> },
  query_experiences: { label: '查询本地经验库', icon: <Sparkles className="h-3.5 w-3.5" /> },
  mark_experience: { label: '更新学习状态', icon: <Tag className="h-3.5 w-3.5" /> },
  export_experiences: { label: '导出学习笔记', icon: <FileText className="h-3.5 w-3.5" /> },
}

export function ExecutionTimeline({ events, isRunning }: ExecutionTimelineProps) {
  const displayEvents = events.filter(
    (e) =>
      e.event === 'turn_start' ||
      e.event === 'tool_call' ||
      e.event === 'tool_result' ||
      e.event === 'llm_response' ||
      e.event === 'budget_exhausted' ||
      e.event === 'cancelled' ||
      e.event === 'turn_complete' ||
      e.event === 'error',
  )

  return (
    <div className="flex h-full flex-col bg-slate-50/70">
      <div className="flex items-center justify-between border-b border-slate-200 bg-white px-4 py-3">
        <div className="flex items-center gap-2 text-xs font-semibold text-slate-800">
          <Cpu className="h-4 w-4 text-rose-600" />
          <span>Agent 实时执行时间线</span>
        </div>
        {isRunning && (
          <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-rose-600">
            <Loader2 className="h-3 w-3 animate-spin" />
            防风控串行执行中…
          </span>
        )}
      </div>

      <div className="flex-1 space-y-2.5 overflow-y-auto p-4">
        {displayEvents.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center px-4 text-center text-xs text-slate-400">
            <ShieldCheck className="mb-2 h-8 w-8 text-slate-300" />
            <p className="font-medium text-slate-500">发送检索需求后，这里将实时直播每一步执行过程</p>
            <p className="mt-1 leading-relaxed">
              含拆词搜索、3–8s 防风控等待、结构化抽取、防编造核验与去重入库。
            </p>
          </div>
        ) : (
          displayEvents.map((pkt, idx) => <TimelineCard key={idx} pkt={pkt} />)
        )}

        {isRunning && (
          <div className="flex items-center gap-2 rounded-xl border border-dashed border-rose-200 bg-rose-50/50 px-3 py-2.5 text-xs text-rose-700">
            <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
            <span>正在等待小红书 MCP 响应或防风控随机间隔（3–8 秒/次）…</span>
          </div>
        )}
      </div>
    </div>
  )
}

function TimelineCard({ pkt }: { pkt: SSEEventPacket }) {
  const d = pkt.data

  if (pkt.event === 'turn_start') {
    return (
      <div className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600">
        <span className="font-semibold text-slate-800">第 {String(d.turn ?? 1)} 轮开始：</span>
        <span className="ml-1">{String(d.user_input ?? '')}</span>
      </div>
    )
  }

  if (pkt.event === 'tool_call') {
    const tool = String(d.tool ?? '')
    const meta = TOOL_LABELS[tool] ?? { label: tool, icon: <Sparkles className="h-3.5 w-3.5" /> }
    const argsStr =
      typeof d.args === 'object' && d.args !== null
        ? JSON.stringify(d.args)
        : String(d.args_raw ?? '')
    return (
      <div className="rounded-xl border border-sky-200 bg-sky-50/60 px-3 py-2 text-xs">
        <div className="flex items-center justify-between text-sky-800">
          <span className="inline-flex items-center gap-1.5 font-semibold">
            {meta.icon}
            Step {String(d.step ?? '')} · {meta.label}
          </span>
          <span className="font-mono text-[10px] text-sky-600">{tool}</span>
        </div>
        <div className="mt-1 truncate font-mono text-[11px] text-sky-900/80" title={argsStr}>
          {argsStr}
        </div>
      </div>
    )
  }

  if (pkt.event === 'tool_result') {
    const ok = Boolean(d.ok)
    return (
      <div
        className={`rounded-xl border px-3 py-2 text-xs ${
          ok
            ? 'border-emerald-200 bg-emerald-50/50 text-emerald-900'
            : 'border-amber-200 bg-amber-50/70 text-amber-900'
        }`}
      >
        <div className="flex items-center justify-between">
          <span className="inline-flex items-center gap-1.5 font-medium">
            {ok ? (
              <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />
            ) : (
              <AlertCircle className="h-3.5 w-3.5 text-amber-600" />
            )}
            {String(d.tool ?? '')} {ok ? '完成' : '提示'}
          </span>
          <span className="font-mono text-[10px] opacity-70">{String(d.duration_ms ?? 0)}ms</span>
        </div>
        <div className="mt-1 break-all text-[11px] opacity-90">
          {ok ? String(d.summary ?? 'ok') : String(d.error ?? d.summary ?? '调用失败')}
        </div>
      </div>
    )
  }

  if (pkt.event === 'llm_response') {
    const usage = (d.usage ?? {}) as Record<string, number>
    const cacheHit = usage.cache_hit_tokens ?? 0
    const promptTokens = usage.prompt_tokens ?? 0
    const hitPct = promptTokens > 0 ? Math.round((cacheHit / promptTokens) * 100) : 0
    return (
      <div className="flex items-center justify-between rounded-lg bg-white/80 px-3 py-1.5 font-mono text-[11px] text-slate-500 ring-1 ring-slate-200/80">
        <span>
          LLM Step {String(d.step ?? '')} · {String(d.duration_ms ?? 0)}ms
        </span>
        <span>
          {usage.total_tokens ?? 0} tok · 缓存 {hitPct}%
        </span>
      </div>
    )
  }

  if (pkt.event === 'budget_exhausted' || pkt.event === 'cancelled') {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
        <AlertCircle className="h-3.5 w-3.5 shrink-0" />
        <span>
          {pkt.event === 'cancelled'
            ? '收到取消信号，正在收尾…'
            : `触发预算边界：${String(d.reason ?? '')}`}
        </span>
      </div>
    )
  }

  if (pkt.event === 'error') {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
        <XCircle className="h-3.5 w-3.5 shrink-0" />
        <span>错误：{String(d.message ?? '执行异常')}</span>
      </div>
    )
  }

  if (pkt.event === 'turn_complete') {
    const cost = (d.cost ?? {}) as Record<string, number>
    const newlyJobs = Array.isArray(d.newly_saved_jobs) ? d.newly_saved_jobs.length : 0
    const newlyExps = Array.isArray(d.newly_saved_experiences) ? d.newly_saved_experiences.length : 0
    return (
      <div className="rounded-xl border border-emerald-300 bg-emerald-50 px-3 py-2.5 text-xs text-emerald-900">
        <div className="flex items-center justify-between font-semibold">
          <span className="inline-flex items-center gap-1.5">
            <CheckCircle2 className="h-4 w-4 text-emerald-600" />
            本轮完成（{String(d.stop_reason ?? 'completed')}）
          </span>
          <span className="font-mono">¥{(cost.total_cost ?? 0).toFixed(4)}</span>
        </div>
        <div className="mt-1 text-[11px] text-emerald-800">
          新入库岗位 {newlyJobs} · 经验 {newlyExps} · LLM {String(d.total_llm_calls ?? 0)} 次
        </div>
      </div>
    )
  }

  return null
}
