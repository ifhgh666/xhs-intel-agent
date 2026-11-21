import { useCallback, useEffect, useState } from 'react'
import {
  Camera,
  Clock,
  Download,
  ExternalLink,
  Link2,
  Loader2,
  RefreshCw,
  Search,
} from 'lucide-react'
import { api } from '../api/client'
import type { JobItem, JobListResponse, JobStatus } from '../api/types'
import { JobDetailDrawer } from '../components/JobDetailDrawer'
import { ALL_STATUSES, STATUS_META, StatusSelect } from '../components/StatusBadge'

interface JobsPageProps {
  onJobsMutated?: () => void
}

function shortDate(iso: string | null): string {
  if (!iso) return '-'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10)
  return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
}

export function JobsPage({ onJobsMutated }: JobsPageProps) {
  const [data, setData] = useState<JobListResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // 筛选条件
  const [statusFilter, setStatusFilter] = useState<JobStatus | ''>('')
  const [keyword, setKeyword] = useState('')
  const [city, setCity] = useState('')
  const [graduationYear, setGraduationYear] = useState('')
  const [onlyFresh, setOnlyFresh] = useState(false)
  const [onlyNeedsImage, setOnlyNeedsImage] = useState(false)
  const [page, setPage] = useState(1)
  const pageSize = 20

  const [selectedJob, setSelectedJob] = useState<JobItem | null>(null)
  const [updatingId, setUpdatingId] = useState<number | null>(null)
  const [toast, setToast] = useState<string | null>(null)

  const showToast = (msg: string) => {
    setToast(msg)
    window.setTimeout(() => setToast(null), 2400)
  }

  const fetchJobs = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await api.listJobs({
        status: statusFilter,
        keyword: keyword.trim() || undefined,
        city: city || undefined,
        graduation_year: graduationYear.trim() || undefined,
        only_fresh: onlyFresh,
        needs_image: onlyNeedsImage ? true : null,
        page,
        page_size: pageSize,
      })
      setData(res)
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载岗位列表失败')
    } finally {
      setLoading(false)
    }
  }, [statusFilter, keyword, city, graduationYear, onlyFresh, onlyNeedsImage, page])

  useEffect(() => {
    void fetchJobs()
  }, [fetchJobs])

  const handleStatusChange = async (jobId: number, nextStatus: JobStatus) => {
    setUpdatingId(jobId)
    try {
      const updated = await api.updateJobStatus(jobId, nextStatus)
      setData((prev) =>
        prev
          ? {
              ...prev,
              items: prev.items.map((item) => (item.id === jobId ? updated : item)),
            }
          : prev,
      )
      setSelectedJob((prev) => (prev && prev.id === jobId ? updated : prev))
      showToast(`已将 #${jobId} 标记为「${STATUS_META[nextStatus].label}」`)
      void fetchJobs()
      onJobsMutated?.()
    } catch (err) {
      showToast(err instanceof Error ? err.message : '更新状态失败')
    } finally {
      setUpdatingId(null)
    }
  }

  const handleDelete = async (jobId: number) => {
    try {
      await api.deleteJob(jobId)
      setSelectedJob(null)
      showToast(`已删除岗位 #${jobId}`)
      void fetchJobs()
      onJobsMutated?.()
    } catch (err) {
      showToast(err instanceof Error ? err.message : '删除失败')
    }
  }

  const exportUrl = api.getExportUrl({
    status: statusFilter,
    keyword: keyword.trim() || undefined,
    city: city || undefined,
    graduation_year: graduationYear.trim() || undefined,
    only_fresh: onlyFresh,
    needs_image: onlyNeedsImage ? true : null,
  })

  const statusCounts = data?.facets.status_counts
  const totalAll = statusCounts
    ? Object.values(statusCounts).reduce((acc, n) => acc + n, 0)
    : data?.total ?? 0
  const totalPages = data ? Math.max(1, Math.ceil(data.total / pageSize)) : 1

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* 顶部标题与操作栏 */}
      <div className="border-b border-slate-200 bg-white px-6 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold text-slate-900">岗位情报库</h1>
            <p className="text-xs text-slate-500">
              所有入库岗位均经原文防编造校验与去重；点击任意一行查看原文证据与完整投递信息
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => void fetchJobs()}
              className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
              刷新
            </button>
            <a
              href={exportUrl}
              download
              className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-3.5 py-2 text-xs font-medium text-white shadow-xs transition hover:bg-slate-800"
            >
              <Download className="h-3.5 w-3.5" />
              导出当前筛选 Excel
            </a>
          </div>
        </div>

        {/* 状态 Tab 条 */}
        <div className="mt-4 flex flex-wrap items-center gap-1.5">
          <button
            onClick={() => {
              setStatusFilter('')
              setPage(1)
            }}
            className={`cursor-pointer rounded-full px-3 py-1 text-xs font-medium transition ${
              statusFilter === ''
                ? 'bg-slate-900 text-white'
                : 'bg-slate-100 text-slate-600 hover:bg-slate-200/70'
            }`}
          >
            全部 ({totalAll})
          </button>
          {ALL_STATUSES.map((st) => (
            <button
              key={st}
              onClick={() => {
                setStatusFilter(st)
                setPage(1)
              }}
              className={`cursor-pointer rounded-full px-3 py-1 text-xs font-medium transition ${
                statusFilter === st
                  ? 'bg-rose-600 text-white'
                  : 'bg-slate-100 text-slate-600 hover:bg-slate-200/70'
              }`}
            >
              {STATUS_META[st].label} ({statusCounts?.[st] ?? 0})
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
              placeholder="搜公司、岗位、技能（如 Agent / RAG）"
              className="w-full rounded-lg border border-slate-200 bg-slate-50/60 py-1.5 pr-3 pl-8 text-xs text-slate-800 outline-none focus:border-rose-400 focus:bg-white"
            />
          </div>

          <select
            value={city}
            onChange={(e) => {
              setCity(e.target.value)
              setPage(1)
            }}
            className="rounded-lg border border-slate-200 bg-slate-50/60 px-2.5 py-1.5 text-xs text-slate-700 outline-none focus:border-rose-400 focus:bg-white"
          >
            <option value="">全部城市</option>
            {(data?.facets.cities ?? []).map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>

          <input
            type="text"
            value={graduationYear}
            onChange={(e) => {
              setGraduationYear(e.target.value)
              setPage(1)
            }}
            placeholder="届别（如 2028）"
            className="w-28 rounded-lg border border-slate-200 bg-slate-50/60 px-2.5 py-1.5 text-xs text-slate-800 outline-none focus:border-rose-400 focus:bg-white"
          />

          <label className="inline-flex cursor-pointer items-center gap-1.5 text-xs text-slate-600 select-none">
            <input
              type="checkbox"
              checked={onlyFresh}
              onChange={(e) => {
                setOnlyFresh(e.target.checked)
                setPage(1)
              }}
              className="rounded border-slate-300 text-rose-600 focus:ring-rose-500"
            />
            仅看 60 天内
          </label>

          <label className="inline-flex cursor-pointer items-center gap-1.5 text-xs text-slate-600 select-none">
            <input
              type="checkbox"
              checked={onlyNeedsImage}
              onChange={(e) => {
                setOnlyNeedsImage(e.target.checked)
                setPage(1)
              }}
              className="rounded border-slate-300 text-rose-600 focus:ring-rose-500"
            />
            仅看需看图
          </label>
        </div>
      </div>

      {/* 表格主体 */}
      <div className="flex-1 overflow-auto p-6">
        {error ? (
          <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">
            {error}
          </div>
        ) : loading && !data ? (
          <div className="flex h-48 items-center justify-center text-sm text-slate-400">
            <Loader2 className="mr-2 h-5 w-5 animate-spin text-rose-500" />
            正在加载岗位数据…
          </div>
        ) : data && data.items.length === 0 ? (
          <div className="flex h-56 flex-col items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-white text-center">
            <p className="text-sm font-medium text-slate-600">当前筛选条件下暂无岗位记录</p>
            <p className="mt-1 text-xs text-slate-400">
              可以放宽筛选条件，或前往「智能检索工作台」发起新的小红书搜索
            </p>
          </div>
        ) : (
          <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-xs">
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-left text-xs">
                <thead>
                  <tr className="border-b border-slate-200 bg-slate-50/80 text-[11px] font-semibold text-slate-500">
                    <th className="px-4 py-3">公司 / 岗位</th>
                    <th className="px-3 py-3">城市</th>
                    <th className="px-3 py-3">届别 / 学历</th>
                    <th className="px-3 py-3">出勤 / 薪资</th>
                    <th className="px-3 py-3">投递方式</th>
                    <th className="px-3 py-3">发布</th>
                    <th className="px-3 py-3">跟进状态</th>
                    <th className="px-3 py-3 text-right">原帖</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {(data?.items ?? []).map((job) => (
                    <tr
                      key={job.id}
                      onClick={() => setSelectedJob(job)}
                      className="cursor-pointer transition hover:bg-slate-50/80"
                    >
                      <td className="max-w-[280px] px-4 py-3">
                        <div className="font-semibold text-slate-900">
                          {job.position || '未具名岗位'}
                        </div>
                        <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] text-slate-500">
                          <span>{job.company || '未具名团队'}</span>
                          {job.needs_image && (
                            <span className="inline-flex items-center gap-0.5 rounded bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-700">
                              <Camera className="h-2.5 w-2.5" /> 见图
                            </span>
                          )}
                          {job.maybe_expired && (
                            <span className="inline-flex items-center gap-0.5 rounded bg-orange-50 px-1.5 py-0.5 text-[10px] font-medium text-orange-700">
                              <Clock className="h-2.5 w-2.5" /> &gt;60天
                            </span>
                          )}
                          {job.extra_sources.length > 0 && (
                            <span className="inline-flex items-center gap-0.5 rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] font-medium text-indigo-700">
                              <Link2 className="h-2.5 w-2.5" /> +{job.extra_sources.length}来源
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-3 py-3 whitespace-nowrap text-slate-700">
                        {job.city || '-'}
                      </td>
                      <td className="max-w-[140px] px-3 py-3 text-slate-600">
                        <div className="truncate">{job.graduation_year || '-'}</div>
                        <div className="truncate text-[11px] text-slate-400">{job.education || ''}</div>
                      </td>
                      <td className="max-w-[150px] px-3 py-3 text-slate-600">
                        <div className="truncate">{job.attendance_days || job.internship_duration || '-'}</div>
                        <div className="truncate text-[11px] text-emerald-700">{job.salary || ''}</div>
                      </td>
                      <td className="max-w-[180px] truncate px-3 py-3 text-slate-600" title={job.apply_method ?? ''}>
                        {job.apply_method || <span className="text-slate-400">未提及</span>}
                      </td>
                      <td className="px-3 py-3 whitespace-nowrap font-mono text-[11px] text-slate-500">
                        {shortDate(job.note_published_at)}
                      </td>
                      <td className="px-3 py-3 whitespace-nowrap">
                        <StatusSelect
                          value={job.status}
                          disabled={updatingId === job.id}
                          onChange={(next) => void handleStatusChange(job.id, next)}
                        />
                      </td>
                      <td className="px-3 py-3 text-right whitespace-nowrap">
                        <a
                          href={job.open_url || job.source_url}
                          target="_blank"
                          rel="noreferrer"
                          onClick={(e) => e.stopPropagation()}
                          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-rose-600 hover:bg-rose-50"
                        >
                          查看
                          <ExternalLink className="h-3 w-3" />
                        </a>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* 分页条 */}
            {data && data.total > pageSize && (
              <div className="flex items-center justify-between border-t border-slate-100 bg-slate-50/50 px-4 py-2.5 text-xs text-slate-500">
                <span>
                  共 {data.total} 条，第 {page} / {totalPages} 页
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
          </div>
        )}
      </div>

      {/* 原文证据与详情抽屉 */}
      <JobDetailDrawer
        job={selectedJob}
        onClose={() => setSelectedJob(null)}
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
