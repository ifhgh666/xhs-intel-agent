import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  Bot,
  ExternalLink,
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

const STARTER_PROMPTS = [
  '帮我搜索广州的 AI Agent、RAG 实习岗位，优先找适合 2028 届本科生的岗位，入库后导出 Excel。',
  '帮我搜索深圳的大模型 / AI 应用开发日常实习，要求最近 1 个月发布的。',
  '查询本地岗位库里所有广州的岗位，按推荐度整理给我。',
]

export function WorkspacePage({
  systemStatus,
  onRefreshSystem,
  onOpenLogin,
}: WorkspacePageProps) {
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [messages, setMessages] = useState<SessionMessage[]>([])
  const [events, setEvents] = useState<SSEEventPacket[]>([])
  const [input, setInput] = useState('')
  const [maxSearch, setMaxSearch] = useState(3)
  const [maxDetail, setMaxDetail] = useState(6)
  const [isRunning, setIsRunning] = useState(false)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [showTimeline, setShowTimeline] = useState(true)
  const [starredIds, setStarredIds] = useState<Set<number>>(new Set())

  const unsubscribeRef = useRef<(() => void) | null>(null)
  const bottomRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isRunning])

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
      const created = await api.createSession({ max_search: maxSearch, max_detail: maxDetail })
      setSessionId(created.session_id)
      setMessages([])
      setEvents([])
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : '创建会话失败')
    }
  }

  const handleSend = async (overrideText?: string) => {
    const text = (overrideText ?? input).trim()
    if (!text || isRunning) return
    setErrorMsg(null)

    try {
      let sid = sessionId
      if (!sid) {
        const created = await api.createSession({ max_search: maxSearch, max_detail: maxDetail })
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
                用自然语言告诉我你想找什么实习
              </h2>
              <p className="mt-1.5 max-w-md text-xs leading-relaxed text-slate-500">
                Agent 会自主拆分关键词搜索小红书、过滤面经与 Vlog、独立抽取结构化岗位、做原文防编造校验与去重入库，支持多轮追问筛选与一键导出 Excel。
              </p>

              <div className="mt-7 grid w-full gap-2.5 text-left">
                {STARTER_PROMPTS.map((prompt) => (
                  <button
                    key={prompt}
                    disabled={isRunning}
                    onClick={() => void handleSend(prompt)}
                    className="group flex cursor-pointer items-center justify-between rounded-xl border border-slate-200 bg-slate-50/70 px-4 py-3 text-xs text-slate-700 transition hover:border-rose-300 hover:bg-rose-50/40"
                  >
                    <span className="leading-relaxed">{prompt}</span>
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
              <div className="flex flex-wrap items-center gap-4">
                <label className="inline-flex items-center gap-1.5">
                  <span>最多搜索</span>
                  <input
                    type="range"
                    min={1}
                    max={6}
                    value={maxSearch}
                    disabled={isRunning}
                    onChange={(e) => setMaxSearch(Number(e.target.value))}
                    className="h-1.5 w-20 accent-rose-600"
                  />
                  <span className="w-5 font-mono font-semibold text-slate-700">{maxSearch}</span>
                </label>
                <label className="inline-flex items-center gap-1.5">
                  <span>最多抽取</span>
                  <input
                    type="range"
                    min={1}
                    max={20}
                    value={maxDetail}
                    disabled={isRunning}
                    onChange={(e) => setMaxDetail(Number(e.target.value))}
                    className="h-1.5 w-20 accent-rose-600"
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
