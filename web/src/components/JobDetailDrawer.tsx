import {
  Building2,
  Calendar,
  Camera,
  Clock,
  ExternalLink,
  GraduationCap,
  Link2,
  MapPin,
  Quote,
  Send,
  Trash2,
  Wallet,
  X,
} from 'lucide-react'
import type { JobItem, JobStatus } from '../api/types'
import { OriginalNoteViewer } from './OriginalNoteViewer'
import { StatusSelect } from './StatusBadge'

interface JobDetailDrawerProps {
  job: JobItem | null
  onClose: () => void
  onStatusChange: (jobId: number, status: JobStatus) => void
  onDelete: (jobId: number) => void
}

function formatDateTime(iso: string | null): string {
  if (!iso) return '未知'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function JobDetailDrawer({
  job,
  onClose,
  onStatusChange,
  onDelete,
}: JobDetailDrawerProps) {
  if (!job) return null

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-slate-900/30 backdrop-blur-[1px]" onClick={onClose}>
      <aside
        className="flex h-full w-full max-w-lg flex-col border-l border-slate-200 bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between border-b border-slate-100 px-6 py-4">
          <div className="min-w-0 pr-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs text-slate-400">#{job.id}</span>
              <StatusSelect value={job.status} onChange={(next) => onStatusChange(job.id, next)} />
              {job.needs_image && (
                <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700 ring-1 ring-amber-200">
                  <Camera className="h-3 w-3" /> 详情见原帖图片
                </span>
              )}
              {job.maybe_expired && (
                <span className="inline-flex items-center gap-1 rounded-full bg-orange-50 px-2 py-0.5 text-[11px] font-medium text-orange-700 ring-1 ring-orange-200">
                  <Clock className="h-3 w-3" /> 发布超过 60 天
                </span>
              )}
            </div>
            <h2 className="mt-2 text-lg font-semibold text-slate-900">
              {job.position || '未具名实习岗位'}
            </h2>
            <p className="mt-0.5 flex items-center gap-1.5 text-sm text-slate-600">
              <Building2 className="h-4 w-4 text-slate-400" />
              {job.company || '未具名公司/团队'}
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="flex-1 space-y-5 overflow-y-auto px-6 py-5">
          {/* 核心字段网格 */}
          <div className="grid grid-cols-2 gap-3 text-sm">
            <InfoTile icon={<MapPin className="h-4 w-4" />} label="工作城市" value={job.city} />
            <InfoTile icon={<GraduationCap className="h-4 w-4" />} label="届别 / 学历" value={[job.graduation_year, job.education].filter(Boolean).join(' · ') || null} />
            <InfoTile icon={<Calendar className="h-4 w-4" />} label="出勤 / 时长" value={[job.attendance_days, job.internship_duration].filter(Boolean).join(' · ') || null} />
            <InfoTile icon={<Wallet className="h-4 w-4" />} label="薪资待遇" value={job.salary} />
            <InfoTile icon={<Clock className="h-4 w-4" />} label="截止时间" value={job.deadline} />
            <InfoTile icon={<Calendar className="h-4 w-4" />} label="笔记发布时间" value={formatDateTime(job.note_published_at)} />
          </div>

          {/* 技能标签 */}
          <div>
            <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">技能与方向关键词</h3>
            {job.skills.length > 0 ? (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {job.skills.map((sk) => (
                  <span
                    key={sk}
                    className="rounded-md bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-700"
                  >
                    {sk}
                  </span>
                ))}
              </div>
            ) : (
              <p className="mt-1.5 text-xs text-slate-400">原文未单独列举技能标签</p>
            )}
          </div>

          {/* 投递方式 */}
          <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-4">
            <h3 className="flex items-center gap-1.5 text-xs font-semibold text-slate-700">
              <Send className="h-3.5 w-3.5 text-rose-500" />
              投递方式
            </h3>
            <p className="mt-1.5 text-sm break-all text-slate-800">
              {job.apply_method || '原文未写明具体邮箱/链接（建议打开原帖私信或看图）'}
            </p>
            {job.application_url && (
              <a
                href={job.application_url}
                target="_blank"
                rel="noreferrer"
                className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-rose-600 hover:underline"
              >
                <ExternalLink className="h-3.5 w-3.5" />
                {job.application_url}
              </a>
            )}
          </div>

          {/* 原文防编造证据 */}
          <div className="rounded-xl border border-emerald-200 bg-emerald-50/60 p-4">
            <div className="flex items-center justify-between">
              <h3 className="flex items-center gap-1.5 text-xs font-semibold text-emerald-800">
                <Quote className="h-3.5 w-3.5" />
                防编造原文证据（已通过代码核验）
              </h3>
              <span className="font-mono text-[11px] text-emerald-700">
                置信度 {(job.confidence * 100).toFixed(0)}%
              </span>
            </div>
            <blockquote className="mt-2 border-l-2 border-emerald-400 pl-3 text-sm leading-relaxed text-emerald-950">
              “{job.evidence}”
            </blockquote>
          </div>

          {/* 本地原帖全文与配图（免扫码直接查看） */}
          {job.note_id && (
            <OriginalNoteViewer noteId={job.note_id} defaultExpanded={job.needs_image} />
          )}

          {/* 来源溯源 */}
          <div className="space-y-2 border-t border-slate-100 pt-4 text-xs text-slate-600">
            <div className="font-semibold text-slate-700">来源溯源</div>
            <div>原帖标题：{job.note_title || '-'}</div>
            <div>作者：{job.note_author || '-'}</div>
            <a
              href={job.open_url || job.source_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 font-medium text-rose-600 hover:underline"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              打开小红书原帖（{job.note_id}）
            </a>
            {job.extra_sources.length > 0 && (
              <div className="mt-2 space-y-1">
                <div className="flex items-center gap-1 font-medium text-indigo-700">
                  <Link2 className="h-3.5 w-3.5" />
                  已合并的其他同岗转发来源（{job.extra_sources.length} 条）：
                </div>
                {job.extra_sources.map((src) => (
                  <a
                    key={src}
                    href={src}
                    target="_blank"
                    rel="noreferrer"
                    className="block truncate text-indigo-600 hover:underline"
                  >
                    {src}
                  </a>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="flex items-center justify-between border-t border-slate-100 bg-slate-50/70 px-6 py-3">
          <button
            onClick={() => onDelete(job.id)}
            className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-rose-600 hover:bg-rose-50"
          >
            <Trash2 className="h-3.5 w-3.5" />
            删除此条
          </button>
          <a
            href={job.open_url || job.source_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-3.5 py-1.5 text-xs font-medium text-white hover:bg-slate-800"
          >
            去小红书查看原帖
            <ExternalLink className="h-3.5 w-3.5" />
          </a>
        </div>
      </aside>
    </div>
  )
}

function InfoTile({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode
  label: string
  value: string | null | undefined
}) {
  return (
    <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-3">
      <div className="flex items-center gap-1.5 text-[11px] font-medium text-slate-500">
        <span className="text-slate-400">{icon}</span>
        {label}
      </div>
      <div className="mt-1 text-sm font-medium text-slate-900">
        {value || <span className="font-normal text-slate-400">原文未提及</span>}
      </div>
    </div>
  )
}
