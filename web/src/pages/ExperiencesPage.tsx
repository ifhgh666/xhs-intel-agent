import { useCallback, useEffect, useState } from 'react'
import {
  Camera,
  Download,
  ExternalLink,
  FileText,
  Heart,
  Loader2,
  RefreshCw,
  Search,
} from 'lucide-react'
import { api } from '../api/client'
import type {
  ExperienceCategory,
  ExperienceItem,
  ExperienceListResponse,
  ExperienceStatus,
} from '../api/types'
import {
  ALL_EXP_CATEGORIES,
  ALL_EXP_STATUSES,
  EXP_CATEGORY_META,
  EXP_STATUS_META,
  ExpStatusSelect,
  ExperienceDetailDrawer,
} from '../components/ExperienceDetailDrawer'

interface ExperiencesPageProps {
  onMutated?: () => void
}

function shortDate(iso: string | null): string {
  if (!iso) return '-'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10)
  return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
}

export function ExperiencesPage({ onMutated }: ExperiencesPageProps) {
  const [data, setData] = useState<ExperienceListResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [category, setCategory] = useState<ExperienceCategory | ''>('')
  const [statusFilter, setStatusFilter] = useState<ExperienceStatus | ''>('')
  const [keyword, setKeyword] = useState('')
  const [sortBy, setSortBy] = useState<'latest' | 'likes'>('latest')
  const [onlyNeedsImage, setOnlyNeedsImage] = useState(false)
  const [page, setPage] = useState(1)
  const pageSize = 18

  const [selected, setSelected] = useState<ExperienceItem | null>(null)
  const [updatingId, setUpdatingId] = useState<number | null>(null)
  const [toast, setToast] = useState<string | null>(null)

  const showToast = (msg: string) => {
    setToast(msg)
    window.setTimeout(() => setToast(null), 2400)
  }

  const fetchList = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await api.listExperiences({
        category,
        status: statusFilter,
        keyword: keyword.trim() || undefined,
        needs_image: onlyNeedsImage ? true : null,
        sort_by: sortBy,
        page,
        page_size: pageSize,
      })
      setData(res)
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载经验知识库失败')
    } finally {
      setLoading(false)
    }
  }, [category, statusFilter, keyword, onlyNeedsImage, sortBy, page])

  useEffect(() => {
    void fetchList()
  }, [fetchList])

  const handleStatusChange = async (id: number, next: ExperienceStatus) => {
    setUpdatingId(id)
    try {
      const updated = await api.updateExperienceStatus(id, next)
      setData((prev) =>
        prev
          ? { ...prev, items: prev.items.map((it) => (it.id === id ? updated : it)) }
          : prev,
      )
      setSelected((prev) => (prev && prev.id === id ? updated : prev))
      showToast(`已将 #${id} 标记为「${EXP_STATUS_META[next].label}」`)
      void fetchList()
      onMutated?.()
    } catch (err) {
      showToast(err instanceof Error ? err.message : '更新状态失败')
    } finally {
      setUpdatingId(null)
    }
  }

  const handleDelete = async (id: number) => {
    try {
      await api.deleteExperience(id)
      setSelected(null)
      showToast(`已删除经验 #${id}`)
      void fetchList()
      onMutated?.()
    } catch (err) {
      showToast(err instanceof Error ? err.message : '删除失败')
    }
  }

  const exportParams = {
    category,
    status: statusFilter,
    keyword: keyword.trim() || undefined,
    needs_image: onlyNeedsImage ? true : null,
    sort_by: sortBy,
  }
  const mdExportUrl = api.getExperiencesExportUrl(exportParams, 'md')
  const xlsxExportUrl = api.getExperiencesExportUrl(exportParams, 'xlsx')

  const catCounts = data?.facets.category_counts
  const totalAll = catCounts
    ? Object.values(catCounts).reduce((a, b) => a + b, 0)
    : data?.total ?? 0
  const totalPages = data ? Math.max(1, Math.ceil(data.total / pageSize)) : 1

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-slate-200 bg-white px-6 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold text-slate-900">经验知识库</h1>
            <p className="text-xs text-slate-500">
              沉淀 AI Agent / RAG 学习路线、实战项目、面试高频真题与避坑复盘（与岗位库隔离存储，支持一键导出 Markdown 复习笔记）
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              onClick={() => void fetchList()}
              className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
              刷新
            </button>
            <a
              href={xlsxExportUrl}
              download
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50"
            >
              <Download className="h-3.5 w-3.5" />
              导出 Excel
            </a>
            <a
              href={mdExportUrl}
              download
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-2 text-xs font-medium text-white shadow-xs transition hover:bg-indigo-700"
            >
              <FileText className="h-3.5 w-3.5" />
              导出 Markdown 笔记
            </a>
          </div>
        </div>

        {/* 分类 Tab 条 */}
        <div className="mt-4 flex flex-wrap items-center gap-1.5">
          <button
            onClick={() => {
              setCategory('')
              setPage(1)
            }}
            className={`cursor-pointer rounded-full px-3 py-1 text-xs font-medium transition ${
              category === ''
                ? 'bg-slate-900 text-white'
                : 'bg-slate-100 text-slate-600 hover:bg-slate-200/70'
            }`}
          >
            全部 ({totalAll})
          </button>
          {ALL_EXP_CATEGORIES.map((cat) => (
            <button
              key={cat}
              onClick={() => {
                setCategory(cat)
                setPage(1)
              }}
              className={`cursor-pointer rounded-full px-3 py-1 text-xs font-medium transition ${
                category === cat
                  ? 'bg-indigo-600 text-white'
                  : 'bg-slate-100 text-slate-600 hover:bg-slate-200/70'
              }`}
            >
              {EXP_CATEGORY_META[cat].label} ({catCounts?.[cat] ?? 0})
            </button>
          ))}
        </div>

        {/* 筛选控件 */}
        <div className="mt-3 flex flex-wrap items-center gap-2.5">
          <div className="relative min-w-[200px] flex-1 max-w-xs">
            <Search className="pointer-events-none absolute top-1/2 left-3 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
            <input
              type="text"
              value={keyword}
              onChange={(e) => {
                setKeyword(e.target.value)
                setPage(1)
              }}
              placeholder="搜主题、技术栈、面试题（如 MCP / LangGraph）"
              className="w-full rounded-lg border border-slate-200 bg-slate-50/60 py-1.5 pr-3 pl-8 text-xs text-slate-800 outline-none focus:border-indigo-400 focus:bg-white"
            />
          </div>

          <select
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value as ExperienceStatus | '')
              setPage(1)
            }}
            className="rounded-lg border border-slate-200 bg-slate-50/60 px-2.5 py-1.5 text-xs text-slate-700 outline-none focus:border-indigo-400 focus:bg-white"
          >
            <option value="">全部学习状态</option>
            {ALL_EXP_STATUSES.map((st) => (
              <option key={st} value={st}>
                {EXP_STATUS_META[st].label}
              </option>
            ))}
          </select>

          <select
            value={sortBy}
            onChange={(e) => {
              setSortBy(e.target.value as 'latest' | 'likes')
              setPage(1)
            }}
            className="rounded-lg border border-slate-200 bg-slate-50/60 px-2.5 py-1.5 text-xs text-slate-700 outline-none focus:border-indigo-400 focus:bg-white"
          >
            <option value="latest">按最新发布</option>
            <option value="likes">按点赞最高</option>
          </select>

          <label className="inline-flex cursor-pointer items-center gap-1.5 text-xs text-slate-600 select-none">
            <input
              type="checkbox"
              checked={onlyNeedsImage}
              onChange={(e) => {
                setOnlyNeedsImage(e.target.checked)
                setPage(1)
              }}
              className="rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
            />
            仅看含图解/导图
          </label>
        </div>
      </div>

      {/* 知识卡片网格 */}
      <div className="flex-1 overflow-auto p-6">
        {error ? (
          <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">
            {error}
          </div>
        ) : loading && !data ? (
          <div className="flex h-48 items-center justify-center text-sm text-slate-400">
            <Loader2 className="mr-2 h-5 w-5 animate-spin text-indigo-500" />
            正在加载经验知识库…
          </div>
        ) : data && data.items.length === 0 ? (
          <div className="flex h-56 flex-col items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-white text-center">
            <p className="text-sm font-medium text-slate-600">当前筛选条件下暂无经验笔记</p>
            <p className="mt-1 text-xs text-slate-400">
              前往「智能检索工作台」切到「📚 找经验」模式，试试搜索「AI Agent 学习路线与高频面经」
            </p>
          </div>
        ) : (
          <>
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {(data?.items ?? []).map((exp) => {
                const catMeta = EXP_CATEGORY_META[exp.category] ?? EXP_CATEGORY_META.tips
                return (
                  <article
                    key={exp.id}
                    onClick={() => setSelected(exp)}
                    className="flex cursor-pointer flex-col justify-between rounded-2xl border border-slate-200 bg-white p-4 shadow-2xs transition hover:border-indigo-300 hover:shadow-sm"
                  >
                    <div>
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span
                            className={`rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ${catMeta.bg} ${catMeta.text} ${catMeta.ring}`}
                          >
                            {catMeta.label}
                          </span>
                          <span className="rounded bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600">
                            {exp.topic}
                          </span>
                          {exp.needs_image && (
                            <span className="inline-flex items-center gap-0.5 rounded bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-700">
                              <Camera className="h-2.5 w-2.5" /> 见图
                            </span>
                          )}
                        </div>
                        <ExpStatusSelect
                          value={exp.status}
                          disabled={updatingId === exp.id}
                          onChange={(next) => void handleStatusChange(exp.id, next)}
                        />
                      </div>

                      <h3 className="mt-2.5 line-clamp-2 text-sm font-semibold text-slate-900">
                        {exp.title}
                      </h3>
                      <p className="mt-1.5 line-clamp-2 text-xs leading-relaxed text-slate-600">
                        {exp.summary}
                      </p>

                      {exp.key_points.length > 0 && (
                        <ul className="mt-2.5 space-y-1 border-t border-slate-100 pt-2.5 text-[11px] text-slate-700">
                          {exp.key_points.slice(0, 3).map((pt, i) => (
                            <li key={i} className="line-clamp-1">
                              • {pt}
                            </li>
                          ))}
                          {exp.key_points.length > 3 && (
                            <li className="text-[10px] text-indigo-600">
                              + 还有 {exp.key_points.length - 3} 条要点（点击查看全文）
                            </li>
                          )}
                        </ul>
                      )}

                      {exp.tech_stack.length > 0 && (
                        <div className="mt-3 flex flex-wrap gap-1">
                          {exp.tech_stack.slice(0, 5).map((t) => (
                            <span
                              key={t}
                              className="rounded bg-indigo-50/70 px-1.5 py-0.5 text-[10px] font-medium text-indigo-700"
                            >
                              {t}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>

                    <div className="mt-3.5 flex items-center justify-between border-t border-slate-100 pt-2.5 text-[11px] text-slate-400">
                      <div className="flex items-center gap-2.5 font-mono">
                        <span>{shortDate(exp.note_published_at)}</span>
                        {exp.liked_count !== null && (
                          <span className="inline-flex items-center gap-0.5 text-slate-500">
                            <Heart className="h-3 w-3 text-rose-400" />
                            {exp.liked_count}
                          </span>
                        )}
                      </div>
                      <a
                        href={exp.open_url || exp.source_url}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="inline-flex items-center gap-1 font-medium text-indigo-600 hover:underline"
                      >
                        原帖
                        <ExternalLink className="h-3 w-3" />
                      </a>
                    </div>
                  </article>
                )
              })}
            </div>

            {data && data.total > pageSize && (
              <div className="mt-5 flex items-center justify-between text-xs text-slate-500">
                <span>
                  共 {data.total} 篇，第 {page} / {totalPages} 页
                </span>
                <div className="flex items-center gap-2">
                  <button
                    disabled={page <= 1}
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                    className="cursor-pointer rounded-md border border-slate-200 bg-white px-2.5 py-1 hover:bg-slate-50 disabled:opacity-40"
                  >
                    上一页
                  </button>
                  <button
                    disabled={page >= totalPages}
                    onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                    className="cursor-pointer rounded-md border border-slate-200 bg-white px-2.5 py-1 hover:bg-slate-50 disabled:opacity-40"
                  >
                    下一页
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </div>

      <ExperienceDetailDrawer
        item={selected}
        onClose={() => setSelected(null)}
        onStatusChange={(id, st) => void handleStatusChange(id, st)}
        onDelete={(id) => void handleDelete(id)}
      />

      {toast && (
        <div className="fixed right-6 bottom-6 z-50 rounded-xl bg-slate-900 px-4 py-2.5 text-xs font-medium text-white shadow-lg">
          {toast}
        </div>
      )}
    </div>
  )
}
