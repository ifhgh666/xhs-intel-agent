import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  AlertCircle,
  BookOpen,
  Bot,
  Briefcase,
  CheckCircle2,
  ExternalLink,
  Loader2,
  PanelRightClose,
  PanelRightOpen,
  Plus,
  Send,
  Sparkles,
  Square,
  Star,
  User,
} from 'lucide-react'
import { api, ApiError } from '../api/client'
import { subscribeSessionEvents } from '../api/sse'
import type {
  AgentMode,
  ExperienceItem,
  JobItem,
  SSEEventPacket,
  SessionDetail,
  SessionMessage,
  SystemStatus,
} from '../api/types'
import { ExecutionTimeline } from '../components/ExecutionTimeline'

interface WorkspacePageProps {
  systemStatus: SystemStatus | null
  onRefreshSystem: () => void
  onOpenLogin: () => void
}

const STARTER_PROMPTS: { mode: AgentMode; tag: string; text: string }[] = [
  {
    mode: 'experience',
    tag: '学习经验',
    text: '帮我搜索 AI Agent 与 RAG 的学习路线、高赞实战项目和面试准备经验，入库经验知识库并导出 Markdown 笔记。',
  },
  {
    mode: 'experience',
    tag: '工具技巧',
    text: '帮我搜索 OpenAI Codex CLI 的使用技巧、AGENTS.md 配置最佳实践和避坑经验，抽取入经验库并总结核心工作流。',
  },
  {
    mode: 'jobs',
    tag: '实习岗位',
    text: '帮我搜索广州的 AI Agent、RAG 实习岗位，优先找适合 2028 届本科生的岗位，入库后导出 Excel。',
  },
  {
    mode: 'auto',
    tag: '双模式协同',
    text: '帮我找广州 AI Agent 实习岗位，顺便搜集几篇高赞 AI Agent 实习面经真题和避坑经验。',
  },
]

const MODE_OPTIONS: { value: AgentMode; label: string; icon: React.ReactNode }[] = [
  { value: 'auto', label: '智能识别', icon: <Sparkles className="h-3 w-3" /> },
  { value: 'jobs', label: '找岗位', icon: <Briefcase className="h-3 w-3" /> },
  { value: 'experience', label: '找经验', icon: <BookOpen className="h-3 w-3" /> },
]

interface LiveStepItem {
  tool: string
  label: string
  status: 'running' | 'ok' | 'warn'
  summary?: string
}

function formatToolAction(tool: string, args: Record<string, unknown> | undefined): string {
  const kw = typeof args?.keyword === 'string' ? args.keyword : ''
  const note = typeof args?.note === 'string' ? args.note : ''
  switch (tool) {
    case 'search_notes':
      return kw ? `正在小红书搜索「${kw}」…` : '正在搜索小红书笔记…'
    case 'get_note_detail':
      return note ? `正在查看笔记 ${note} 详情…` : '正在查看笔记详情…'
    case 'extract_and_save_jobs':
      return note ? `正在抽取笔记 ${note} 的岗位信息并核验原文…` : '正在抽取岗位并入库…'
    case 'extract_and_save_experience':
      return note ? `正在抽取笔记 ${note} 的经验干货并核验原文…` : '正在抽取干货并入经验库…'
    case 'query_jobs':
      return '正在查询本地岗位库…'
    case 'query_experiences':
      return '正在查询本地经验知识库…'
    case 'export_jobs':
      return '正在导出岗位 Excel…'
    case 'export_experiences':
      return '正在导出学习笔记…'
    case 'mark_job':
      return '正在更新岗位状态…'
    case 'mark_experience':
      return '正在更新学习状态…'
    default:
      return `正在执行 ${tool}…`
  }
}

function formatStepLabel(tool: string, args: Record<string, unknown> | undefined): string {
  const kw = typeof args?.keyword === 'string' ? args.keyword : ''
  const note = typeof args?.note === 'string' ? args.note : ''
  switch (tool) {
    case 'search_notes':
      return kw ? `搜索「${kw}」` : '搜索小红书笔记'
    case 'get_note_detail':
      return note ? `预览笔记 ${note}` : '预览笔记详情'
    case 'extract_and_save_jobs':
      return note ? `抽取岗位 ${note}` : '抽取岗位入库'
    case 'extract_and_save_experience':
      return note ? `抽取经验 ${note}` : '抽取经验入库'
    case 'query_jobs':
      return '查询本地岗位库'
    case 'query_experiences':
      return '查询本地经验库'
    case 'export_jobs':
      return '导出岗位 Excel'
    case 'export_experiences':
      return '导出学习笔记'
    case 'mark_job':
      return '更新岗位状态'
    case 'mark_experience':
      return '更新学习状态'
    default:
      return tool
  }
}

function buildLiveProgress(events: SSEEventPacket[], mode: AgentMode): {
  headline: string
  subline: string
  steps: LiveStepItem[]
} {
  const steps: LiveStepItem[] = []
  for (const pkt of events) {
    if (pkt.event === 'tool_call') {
      const tool = String(pkt.data.tool ?? '')
      const args = (pkt.data.args as Record<string, unknown> | undefined) ?? undefined
      steps.push({
        tool,
        label: formatStepLabel(tool, args),
        status: 'running',
      })
    } else if (pkt.event === 'tool_result') {
      const tool = String(pkt.data.tool ?? '')
      const ok = Boolean(pkt.data.ok)
      const summary = ok
        ? String(pkt.data.summary ?? '完成')
        : String(pkt.data.error ?? pkt.data.summary ?? '调用提示')
      for (let i = steps.length - 1; i >= 0; i -= 1) {
        if (steps[i].tool === tool && steps[i].status === 'running') {
          steps[i] = { ...steps[i], status: ok ? 'ok' : 'warn', summary }
          break
        }
      }
    }
  }

  let headline =
    mode === 'experience'
      ? '正在查询中：规划经验检索关键词…'
      : mode === 'jobs'
        ? '正在查询中：规划岗位检索关键词…'
        : '正在查询中：理解需求并规划检索方案…'
  let subline = '为保护小红书账号，每次在线请求间隔 3–8 秒，过程会实时更新'

  for (let i = events.length - 1; i >= 0; i -= 1) {
    const pkt = events[i]
    const d = pkt.data
    if (pkt.event === 'cancelled') {
      headline = '正在停止当前查询并收尾…'
      subline = '等待当前步骤安全结束'
      break
    }
    if (pkt.event === 'budget_exhausted') {
      headline = '已达单轮查询上限，正在汇总已有结果…'
      subline = String(d.reason ?? '')
      break
    }
    if (pkt.event === 'tool_call') {
      const tool = String(d.tool ?? '')
      const args = (d.args as Record<string, unknown> | undefined) ?? undefined
      headline = formatToolAction(tool, args)
      subline =
        tool === 'search_notes' ||
        tool === 'get_note_detail' ||
        tool === 'extract_and_save_jobs' ||
        tool === 'extract_and_save_experience'
          ? '正在等待小红书 MCP 响应与防风控间隔（3–8 秒/次）…'
          : '正在处理本地数据…'
      break
    }
    if (pkt.event === 'tool_result') {
      headline = '正在分析检索结果，准备下一步…'
      subline = String(d.summary ?? '步骤已完成')
      break
    }
    if (pkt.event === 'llm_response') {
      const tcCount = Number(d.tool_call_count ?? 0)
      headline = tcCount === 0 ? '正在整理结果并生成最终回复…' : '已规划下一步工具调用，正在执行…'
      break
    }
    if (pkt.event === 'llm_request') {
      const step = Number(d.step ?? 1)
      headline =
        step <= 1
          ? '正在查询中：分析问题并拆解小红书搜索词…'
          : `正在查询中（第 ${step} 步）：思考是否继续检索或汇总结果…`
      break
    }
  }

  return { headline, subline, steps }
}

export function WorkspacePage({
  systemStatus,
  onRefreshSystem,
  onOpenLogin,
}: WorkspacePageProps) {
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [messages, setMessages] = useState<SessionMessage[]>([])
  const [events, setEvents] = useState<SSEEventPacket[]>([])
  const [input, setInput] = useState('')
  const [mode, setMode] = useState<AgentMode>('auto')
  const [maxSearch, setMaxSearch] = useState(3)
  const [maxDetail, setMaxDetail] = useState(6)
  const [isRunning, setIsRunning] = useState(false)
  const [elapsedSec, setElapsedSec] = useState(0)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [showTimeline, setShowTimeline] = useState(true)
  const [starredIds, setStarredIds] = useState<Set<number>>(new Set())
  const [starredExpIds, setStarredExpIds] = useState<Set<number>>(new Set())

  const unsubscribeRef = useRef<(() => void) | null>(null)
  const bottomRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isRunning, events.length])

  useEffect(() => {
    if (!isRunning) {
      setElapsedSec(0)
      return
    }
    setElapsedSec(0)
    const timer = window.setInterval(() => {
      setElapsedSec((s) => s + 1)
    }, 1000)
    return () => window.clearInterval(timer)
  }, [isRunning])

  useEffect(() => {
    return () => {
      unsubscribeRef.current?.()
    }
  }, [])

  // 恢复已有最近会话（若有）
  useEffect(() => {
    ;(async () => {
      try {
        const list = await api.listSessions()
        if (list.length > 0 && !sessionId) {
          const detail: SessionDetail = await api.getSession(list[0].session_id)
          setSessionId(detail.session_id)
          setMessages(detail.messages)
          setEvents(detail.latest_events)
          if (detail.status === 'running' && detail.latest_run_id) {
            setIsRunning(true)
            attachStream(detail.session_id, detail.latest_run_id)
          }
        }
      } catch {
        // ignore
      }
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const attachStream = (sid: string, runId: string) => {
    unsubscribeRef.current?.()
    unsubscribeRef.current = subscribeSessionEvents(sid, runId, {
      onEvent: (pkt) => {
        setEvents((prev) => [...prev, pkt])
        if (pkt.event === 'turn_complete') {
          const d = pkt.data
          const assistantMsg: SessionMessage = {
            role: 'assistant',
            content: String(d.answer ?? ''),
            turn: Number(d.turn ?? 1),
            created_at: pkt.ts ?? new Date().toISOString(),
            meta: {
              run_id: String(d.run_id ?? runId),
              stop_reason: String(d.stop_reason ?? 'completed'),
              steps: Number(d.steps ?? 0),
              total_llm_calls: Number(d.total_llm_calls ?? 0),
              total_usage: d.total_usage as SessionMessage['meta'] extends infer M
                ? M extends { total_usage?: infer U }
                  ? U
                  : never
                : never,
              cost: d.cost as SessionMessage['meta'] extends infer M
                ? M extends { cost?: infer C }
                  ? C
                  : never
                : never,
              tool_calls: (d.tool_calls as Record<string, number>) ?? {},
              newly_saved_jobs: (d.newly_saved_jobs as JobItem[]) ?? [],
              newly_saved_experiences: (d.newly_saved_experiences as ExperienceItem[]) ?? [],
              mode: (d.mode as AgentMode) ?? mode,
            },
          }
          setMessages((prev) => [...prev, assistantMsg])
          setIsRunning(false)
          onRefreshSystem()
        } else if (pkt.event === 'error') {
          setErrorMsg(String(pkt.data.message ?? '执行出错'))
          setIsRunning(false)
          onRefreshSystem()
        }
      },
      onClose: () => {
        setIsRunning(false)
      },
      onError: (err) => {
        setErrorMsg(err.message)
        setIsRunning(false)
      },
    })
  }

  const handleNewSession = async () => {
    if (isRunning) return
    setErrorMsg(null)
    try {
      const created = await api.createSession({ max_search: maxSearch, max_detail: maxDetail, mode })
      setSessionId(created.session_id)
      setMessages([])
      setEvents([])
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : '创建会话失败')
    }
  }

  const handleSend = async (overrideText?: string, overrideMode?: AgentMode) => {
    const text = (overrideText ?? input).trim()
    const targetMode = overrideMode ?? mode
    if (overrideMode) setMode(overrideMode)
    if (!text || isRunning) return
    setErrorMsg(null)

    try {
      let sid = sessionId
      if (!sid) {
        const created = await api.createSession({
          max_search: maxSearch,
          max_detail: maxDetail,
          mode: targetMode,
        })
        sid = created.session_id
        setSessionId(sid)
      }

      const nextTurn = Math.floor(messages.length / 2) + 1
      setMessages((prev) => [
        ...prev,
        { role: 'user', content: text, turn: nextTurn, created_at: new Date().toISOString() },
      ])
      setInput('')
      setEvents([])
      setIsRunning(true)

      const res = await api.sendMessage(sid, {
        content: text,
        max_search: maxSearch,
        max_detail: maxDetail,
        mode: targetMode,
      })
      onRefreshSystem()
      attachStream(sid, res.run_id)
    } catch (err) {
      setIsRunning(false)
      if (err instanceof ApiError && err.status === 409) {
        setErrorMsg(err.message)
      } else {
        setErrorMsg(err instanceof Error ? err.message : '发送消息失败')
      }
    }
  }

  const handleCancel = async () => {
    if (!sessionId || !isRunning) return
    try {
      await api.cancelSession(sessionId)
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : '取消失败')
    }
  }

  const handleQuickStar = async (jobId: number) => {
    try {
      await api.updateJobStatus(jobId, 'interested')
      setStarredIds((prev) => new Set(prev).add(jobId))
    } catch {
      // ignore
    }
  }

  const handleQuickStarExp = async (expId: number) => {
    try {
      await api.updateExperienceStatus(expId, 'starred')
      setStarredExpIds((prev) => new Set(prev).add(expId))
    } catch {
      // ignore
    }
  }

  return (
    <div className="flex h-full overflow-hidden">
      {/* 左栏：对话主区域 */}
      <div className="flex min-w-0 flex-1 flex-col bg-white">
        {/* 会话顶部栏 */}
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-2.5">
          <div className="flex items-center gap-2.5">
            <span className="text-sm font-semibold text-slate-900">智能检索工作台</span>
            {sessionId && (
              <span className="rounded-md bg-slate-100 px-2 py-0.5 font-mono text-[11px] text-slate-500">
                {sessionId}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => void handleNewSession()}
              disabled={isRunning}
              className="inline-flex cursor-pointer items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-40"
            >
              <Plus className="h-3.5 w-3.5" />
              新会话
            </button>
            <button
              onClick={() => setShowTimeline((v) => !v)}
              title={showTimeline ? '收起实时时间线' : '展开实时时间线'}
              className="inline-flex cursor-pointer items-center gap-1 rounded-lg border border-slate-200 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50"
            >
              {showTimeline ? (
                <PanelRightClose className="h-4 w-4" />
              ) : (
                <PanelRightOpen className="h-4 w-4" />
              )}
            </button>
          </div>
        </div>

        {/* 未登录提醒条 */}
        {systemStatus && systemStatus.mcp_connected && !systemStatus.logged_in && (
          <div className="flex items-center justify-between border-b border-amber-200 bg-amber-50 px-5 py-2 text-xs text-amber-800">
            <span>小红书当前未登录，发起实时搜索前需先扫码（查询本地已有岗位库不受影响）。</span>
            <button
              onClick={onOpenLogin}
              className="cursor-pointer rounded-md bg-amber-600 px-2.5 py-1 font-medium text-white hover:bg-amber-700"
            >
              立即扫码
            </button>
          </div>
        )}

        {/* 消息流 */}
        <div className="flex-1 overflow-y-auto px-5 py-5">
          {messages.length === 0 ? (
            <div className="mx-auto flex max-w-2xl flex-col items-center py-10 text-center">
              <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-rose-50 text-rose-600 ring-1 ring-rose-100">
                <Sparkles className="h-6 w-6" />
              </div>
              <h2 className="mt-4 text-lg font-semibold text-slate-900">
                实习岗位 + 学习经验双模式智能检索
              </h2>
              <p className="mt-1.5 max-w-lg text-xs leading-relaxed text-slate-500">
                支持「找岗位」与「找学习路线 / 实战项目 / 高频面经」双管道协同，分别沉淀到独立的岗位库与经验知识库，全程原文防编造校验并支持导出 Excel 与 Markdown 复习笔记。
              </p>

              <div className="mt-7 grid w-full gap-2.5 text-left">
                {STARTER_PROMPTS.map((item) => (
                  <button
                    key={item.text}
                    disabled={isRunning}
                    onClick={() => void handleSend(item.text, item.mode)}
                    className="group flex cursor-pointer items-center justify-between rounded-xl border border-slate-200 bg-slate-50/70 px-4 py-3 text-xs text-slate-700 transition hover:border-rose-300 hover:bg-rose-50/40"
                  >
                    <div className="flex items-start gap-2.5">
                      <span
                        className={`mt-0.5 shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                          item.mode === 'experience'
                            ? 'bg-indigo-100 text-indigo-700'
                            : item.mode === 'jobs'
                              ? 'bg-rose-100 text-rose-700'
                              : 'bg-slate-200 text-slate-700'
                        }`}
                      >
                        {item.tag}
                      </span>
                      <span className="leading-relaxed">{item.text}</span>
                    </div>
                    <Send className="ml-3 h-3.5 w-3.5 shrink-0 text-slate-400 group-hover:text-rose-600" />
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="mx-auto max-w-3xl space-y-5">
              {messages.map((msg, idx) => (
                <div
                  key={idx}
                  className={`flex gap-3 ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
                >
                  {msg.role === 'assistant' && (
                    <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-rose-600 text-white">
                      <Bot className="h-4 w-4" />
                    </div>
                  )}

                  <div
                    className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm ${
                      msg.role === 'user'
                        ? 'bg-slate-900 text-white'
                        : 'border border-slate-200 bg-slate-50/60 text-slate-800'
                    }`}
                  >
                    {msg.role === 'user' ? (
                      <p className="whitespace-pre-wrap leading-relaxed">{msg.content}</p>
                    ) : (
                      <>
                        <div className="prose-chat text-[13.5px]">
                          <ReactMarkdown
                            remarkPlugins={[remarkGfm]}
                            components={{
                              a: ({ ...props }) => (
                                <a {...props} target="_blank" rel="noreferrer" />
                              ),
                            }}
                          >
                            {msg.content || '(无内容)'}
                          </ReactMarkdown>
                        </div>

                        {/* 本轮新入库岗位快捷卡片 */}
                        {msg.meta?.newly_saved_jobs && msg.meta.newly_saved_jobs.length > 0 && (
                          <div className="mt-3 space-y-1.5 border-t border-slate-200 pt-3">
                            <div className="text-[11px] font-semibold text-slate-500">
                              本轮新入库岗位（{msg.meta.newly_saved_jobs.length} 条）
                            </div>
                            <div className="grid gap-1.5 sm:grid-cols-2">
                              {msg.meta.newly_saved_jobs.map((job) => {
                                const isStarred = starredIds.has(job.id) || job.status === 'interested'
                                return (
                                  <div
                                    key={job.id}
                                    className="flex items-center justify-between rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs"
                                  >
                                    <div className="min-w-0 pr-2">
                                      <div className="truncate font-semibold text-slate-900">
                                        {job.position || '未具名岗位'}
                                      </div>
                                      <div className="truncate text-[11px] text-slate-500">
                                        {job.company || '未具名'} · {job.city || '-'}
                                      </div>
                                    </div>
                                    <div className="flex shrink-0 items-center gap-1">
                                      <button
                                        onClick={() => void handleQuickStar(job.id)}
                                        title="标为感兴趣"
                                        className={`cursor-pointer rounded-md p-1 transition ${
                                          isStarred
                                            ? 'bg-amber-50 text-amber-500'
                                            : 'text-slate-400 hover:bg-slate-100 hover:text-amber-500'
                                        }`}
                                      >
                                        <Star className={`h-3.5 w-3.5 ${isStarred ? 'fill-amber-400' : ''}`} />
                                      </button>
                                      <a
                                        href={job.source_url}
                                        target="_blank"
                                        rel="noreferrer"
                                        className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-rose-600"
                                      >
                                        <ExternalLink className="h-3.5 w-3.5" />
                                      </a>
                                    </div>
                                  </div>
                                )
                              })}
                            </div>
                          </div>
                        )}

                        {/* 本轮新入库经验卡片 */}
                        {msg.meta?.newly_saved_experiences && msg.meta.newly_saved_experiences.length > 0 && (
                          <div className="mt-3 space-y-1.5 border-t border-slate-200 pt-3">
                            <div className="text-[11px] font-semibold text-indigo-600">
                              本轮新入库经验知识（{msg.meta.newly_saved_experiences.length} 篇）
                            </div>
                            <div className="grid gap-1.5 sm:grid-cols-2">
                              {msg.meta.newly_saved_experiences.map((exp) => {
                                const isStarred = starredExpIds.has(exp.id) || exp.status === 'starred'
                                return (
                                  <div
                                    key={exp.id}
                                    className="flex items-center justify-between rounded-xl border border-indigo-100 bg-white px-3 py-2 text-xs"
                                  >
                                    <div className="min-w-0 pr-2">
                                      <div className="truncate font-semibold text-slate-900">
                                        {exp.title}
                                      </div>
                                      <div className="truncate text-[11px] text-slate-500">
                                        {exp.category_label} · {exp.topic} · {exp.key_points.length} 条要点
                                      </div>
                                    </div>
                                    <div className="flex shrink-0 items-center gap-1">
                                      <button
                                        onClick={() => void handleQuickStarExp(exp.id)}
                                        title="收藏该篇经验"
                                        className={`cursor-pointer rounded-md p-1 transition ${
                                          isStarred
                                            ? 'bg-amber-50 text-amber-500'
                                            : 'text-slate-400 hover:bg-slate-100 hover:text-amber-500'
                                        }`}
                                      >
                                        <Star className={`h-3.5 w-3.5 ${isStarred ? 'fill-amber-400' : ''}`} />
                                      </button>
                                      <a
                                        href={exp.source_url}
                                        target="_blank"
                                        rel="noreferrer"
                                        className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-indigo-600"
                                      >
                                        <ExternalLink className="h-3.5 w-3.5" />
                                      </a>
                                    </div>
                                  </div>
                                )
                              })}
                            </div>
                          </div>
                        )}

                        {/* 费用与缓存统计条 */}
                        {msg.meta?.cost && (
                          <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-slate-200/80 pt-2 font-mono text-[11px] text-slate-500">
                            <span>steps={msg.meta.steps ?? 0}</span>
                            <span>LLM={msg.meta.total_llm_calls ?? 0}次</span>
                            {msg.meta.total_usage && (
                              <span>
                                tokens={msg.meta.total_usage.total_tokens.toLocaleString()} (缓存{' '}
                                {msg.meta.total_usage.prompt_tokens > 0
                                  ? Math.round(
                                      (msg.meta.total_usage.cache_hit_tokens /
                                        msg.meta.total_usage.prompt_tokens) *
                                        100,
                                    )
                                  : 0}
                                %)
                              </span>
                            )}
                            <span className="font-semibold text-slate-700">
                              ¥{msg.meta.cost.total_cost.toFixed(4)}
                            </span>
                          </div>
                        )}
                      </>
                    )}
                  </div>

                  {msg.role === 'user' && (
                    <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-slate-200 text-slate-700">
                      <User className="h-4 w-4" />
                    </div>
                  )}
                </div>
              ))}

              {/* 运行中实时进度气泡：避免对话区陷入无输出等待 */}
              {isRunning && (() => {
                const progress = buildLiveProgress(events, mode)
                const recentSteps = progress.steps.slice(-5)
                return (
                  <div className="flex justify-start gap-3">
                    <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-rose-600 text-white">
                      <Bot className="h-4 w-4" />
                    </div>
                    <div className="w-full max-w-[85%] rounded-2xl border border-rose-200/80 bg-rose-50/40 px-4 py-3 text-sm text-slate-800">
                      <div className="flex items-center justify-between gap-3">
                        <div className="flex items-center gap-2 font-medium text-rose-700">
                          <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
                          <span>{progress.headline}</span>
                        </div>
                        <span className="shrink-0 rounded-full bg-white px-2 py-0.5 font-mono text-[11px] text-slate-500 ring-1 ring-rose-100">
                          已耗时 {elapsedSec}s
                        </span>
                      </div>

                      <p className="mt-1.5 text-xs text-slate-500">{progress.subline}</p>

                      {recentSteps.length > 0 && (
                        <div className="mt-2.5 space-y-1.5 border-t border-rose-100 pt-2.5">
                          {recentSteps.map((st, i) => (
                            <div
                              key={`${st.tool}-${i}`}
                              className="flex items-start gap-2 text-xs text-slate-700"
                            >
                              {st.status === 'running' ? (
                                <Loader2 className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin text-rose-600" />
                              ) : st.status === 'ok' ? (
                                <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600" />
                              ) : (
                                <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600" />
                              )}
                              <div className="min-w-0 flex-1">
                                <span className="font-medium text-slate-800">{st.label}</span>
                                {st.summary && (
                                  <span className="ml-1.5 text-[11px] text-slate-500">
                                    · {st.summary}
                                  </span>
                                )}
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                )
              })()}
              <div ref={bottomRef} />
            </div>
          )}
        </div>

        {/* 错误提示 */}
        {errorMsg && (
          <div className="mx-5 mb-2 rounded-xl border border-rose-200 bg-rose-50 px-3.5 py-2 text-xs text-rose-700">
            {errorMsg}
          </div>
        )}

        {/* 底部输入与预算控制栏 */}
        <div className="border-t border-slate-200 bg-white px-5 py-3.5">
          <div className="mx-auto max-w-3xl">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-500">
              <div className="flex flex-wrap items-center gap-3">
                {/* 双模式切换胶囊 */}
                <div className="inline-flex rounded-lg border border-slate-200 bg-slate-100/80 p-0.5">
                  {MODE_OPTIONS.map((opt) => (
                    <button
                      key={opt.value}
                      type="button"
                      disabled={isRunning}
                      onClick={() => setMode(opt.value)}
                      className={`inline-flex cursor-pointer items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium transition ${
                        mode === opt.value
                          ? opt.value === 'experience'
                            ? 'bg-indigo-600 text-white shadow-2xs'
                            : opt.value === 'jobs'
                              ? 'bg-rose-600 text-white shadow-2xs'
                              : 'bg-slate-900 text-white shadow-2xs'
                          : 'text-slate-600 hover:text-slate-900'
                      }`}
                    >
                      {opt.icon}
                      {opt.label}
                    </button>
                  ))}
                </div>

                <label className="inline-flex items-center gap-1.5">
                  <span>搜索</span>
                  <input
                    type="range"
                    min={1}
                    max={6}
                    value={maxSearch}
                    disabled={isRunning}
                    onChange={(e) => setMaxSearch(Number(e.target.value))}
                    className="h-1.5 w-16 accent-rose-600"
                  />
                  <span className="w-4 font-mono font-semibold text-slate-700">{maxSearch}</span>
                </label>
                <label className="inline-flex items-center gap-1.5">
                  <span>抽取</span>
                  <input
                    type="range"
                    min={1}
                    max={20}
                    value={maxDetail}
                    disabled={isRunning}
                    onChange={(e) => setMaxDetail(Number(e.target.value))}
                    className="h-1.5 w-16 accent-rose-600"
                  />
                  <span className="w-6 font-mono font-semibold text-slate-700">{maxDetail}篇</span>
                </label>
              </div>
              <span className="hidden text-slate-400 sm:inline">Enter 发送 · Shift+Enter 换行</span>
            </div>

            <div className="flex items-end gap-2 rounded-2xl border border-slate-200 bg-slate-50/70 p-2 focus-within:border-rose-400 focus-within:bg-white">
              <textarea
                rows={2}
                value={input}
                disabled={isRunning}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    void handleSend()
                  }
                }}
                placeholder="输入检索需求，或继续追问（如“只看 4 天以内”“把 1 号标为已投递并导出 Excel”）…"
                className="max-h-36 flex-1 resize-none bg-transparent px-2 py-1 text-sm text-slate-900 outline-none placeholder:text-slate-400 disabled:opacity-50"
              />
              {isRunning ? (
                <button
                  onClick={() => void handleCancel()}
                  className="inline-flex cursor-pointer items-center gap-1.5 rounded-xl bg-rose-600 px-3.5 py-2 text-xs font-medium text-white transition hover:bg-rose-700"
                >
                  <Square className="h-3.5 w-3.5 fill-white" />
                  中止
                </button>
              ) : (
                <button
                  onClick={() => void handleSend()}
                  disabled={!input.trim()}
                  className="inline-flex cursor-pointer items-center gap-1.5 rounded-xl bg-slate-900 px-4 py-2 text-xs font-medium text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <Send className="h-3.5 w-3.5" />
                  发送
                </button>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* 右栏：实时 SSE 执行时间线 */}
      {showTimeline && (
        <div className="hidden w-96 shrink-0 border-l border-slate-200 lg:block">
          <ExecutionTimeline events={events} isRunning={isRunning} />
        </div>
      )}
    </div>
  )
}
