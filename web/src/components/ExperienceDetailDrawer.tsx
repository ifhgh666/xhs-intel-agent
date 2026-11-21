import {
  AlertTriangle,
  BookOpen,
  Camera,
  CheckSquare,
  ExternalLink,
  Heart,
  Link2,
  Quote,
  Trash2,
  User,
  X,
} from 'lucide-react'
import type { ExperienceCategory, ExperienceItem, ExperienceStatus } from '../api/types'

export const EXP_CATEGORY_META: Record<
  ExperienceCategory,
  { label: string; bg: string; text: string; ring: string }
> = {
  roadmap: { label: '学习路线', bg: 'bg-indigo-50', text: 'text-indigo-700', ring: 'ring-indigo-200' },
  project: { label: '项目实战', bg: 'bg-violet-50', text: 'text-violet-700', ring: 'ring-violet-200' },
  interview: { label: '面试真题', bg: 'bg-rose-50', text: 'text-rose-700', ring: 'ring-rose-200' },
  tips: { label: '避坑复盘', bg: 'bg-amber-50', text: 'text-amber-700', ring: 'ring-amber-200' },
}

export const EXP_STATUS_META: Record<
  ExperienceStatus,
  { label: string; bg: string; text: string; ring: string }
> = {
  unread: { label: '未读', bg: 'bg-slate-100', text: 'text-slate-700', ring: 'ring-slate-200' },
  reading: { label: '在学', bg: 'bg-sky-50', text: 'text-sky-700', ring: 'ring-sky-200' },
  starred: { label: '已收藏', bg: 'bg-amber-50', text: 'text-amber-700', ring: 'ring-amber-200' },
  archived: { label: '已归档', bg: 'bg-emerald-50', text: 'text-emerald-700', ring: 'ring-emerald-200' },
}

export const ALL_EXP_CATEGORIES: ExperienceCategory[] = ['roadmap', 'project', 'interview', 'tips']
export const ALL_EXP_STATUSES: ExperienceStatus[] = ['unread', 'reading', 'starred', 'archived']

export function ExpStatusSelect({
  value,
  disabled,
  onChange,
}: {
  value: ExperienceStatus
  disabled?: boolean
  onChange: (next: ExperienceStatus) => void
}) {
  const meta = EXP_STATUS_META[value] ?? EXP_STATUS_META.unread
  return (
    <select
      value={value}
      disabled={disabled}
      onClick={(e) => e.stopPropagation()}
      onChange={(e) => onChange(e.target.value as ExperienceStatus)}
      className={`cursor-pointer rounded-full px-2.5 py-1 text-xs font-medium ring-1 transition outline-none ${meta.bg} ${meta.text} ${meta.ring} disabled:opacity-50`}
    >
      {ALL_EXP_STATUSES.map((st) => (
        <option key={st} value={st} className="bg-white text-slate-900">
          {EXP_STATUS_META[st].label}
        </option>
      ))}
    </select>
  )
}

interface ExperienceDetailDrawerProps {
  item: ExperienceItem | null
  onClose: () => void
  onStatusChange: (id: number, status: ExperienceStatus) => void
  onDelete: (id: number) => void
}

export function ExperienceDetailDrawer({
  item,
  onClose,
  onStatusChange,
  onDelete,
}: ExperienceDetailDrawerProps) {
  if (!item) return null
  const catMeta = EXP_CATEGORY_META[item.category] ?? EXP_CATEGORY_META.tips

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-slate-900/30 backdrop-blur-[1px]" onClick={onClose}>
      <aside
        className="flex h-full w-full max-w-xl flex-col border-l border-slate-200 bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between border-b border-slate-100 px-6 py-4">
          <div className="min-w-0 pr-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs text-slate-400">#{item.id}</span>
              <span className={`rounded-full px-2.5 py-0.5 text-[11px] font-medium ring-1 ${catMeta.bg} ${catMeta.text} ${catMeta.ring}`}>
                {catMeta.label}
              </span>
              <span className="rounded-md bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-700">
                {item.topic}
              </span>
              <ExpStatusSelect value={item.status} onChange={(next) => onStatusChange(item.id, next)} />
              {item.needs_image && (
                <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700 ring-1 ring-amber-200">
                  <Camera className="h-3 w-3" /> 含图解/导图
                </span>
              )}
            </div>
            <h2 className="mt-2 text-lg font-semibold text-slate-900">{item.title}</h2>
            <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-slate-500">
              {item.target_audience && (
                <span className="inline-flex items-center gap-1">
                  <User className="h-3.5 w-3.5" /> 适用：{item.target_audience}
                </span>
              )}
              {item.liked_count !== null && (
                <span className="inline-flex items-center gap-1">
                  <Heart className="h-3.5 w-3.5 text-rose-500" /> {item.liked_count} 赞
                </span>
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="flex-1 space-y-5 overflow-y-auto px-6 py-5">
          {/* 核心总结 */}
          <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-4">
            <h3 className="flex items-center gap-1.5 text-xs font-semibold text-slate-700">
              <BookOpen className="h-3.5 w-3.5 text-indigo-600" />
              核心总结
            </h3>
            <p className="mt-1.5 text-sm leading-relaxed text-slate-800">{item.summary}</p>
          </div>

          {/* 关键要点 / 题目清单 */}
          {item.key_points.length > 0 && (
            <div>
              <h3 className="flex items-center gap-1.5 text-xs font-semibold text-slate-700">
                <CheckSquare className="h-3.5 w-3.5 text-emerald-600" />
                {item.category === 'interview' ? '面试高频真题与考察点' : '核心要点与实操步骤'}（{item.key_points.length}）
              </h3>
              <ul className="mt-2 space-y-2">
                {item.key_points.map((pt, i) => (
                  <li
                    key={i}
                    className="flex gap-2.5 rounded-xl border border-slate-100 bg-white px-3.5 py-2.5 text-sm leading-relaxed text-slate-800 shadow-2xs"
                  >
                    <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-indigo-50 font-mono text-[11px] font-semibold text-indigo-700">
                      {i + 1}
                    </span>
                    <span>{pt}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* 技术栈 */}
          {item.tech_stack.length > 0 && (
            <div>
              <h3 className="text-xs font-semibold text-slate-500 uppercase">涉及技术栈与工具</h3>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {item.tech_stack.map((t) => (
                  <span
                    key={t}
                    className="rounded-md bg-indigo-50 px-2.5 py-1 text-xs font-medium text-indigo-700"
                  >
                    {t}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* 提及资源 */}
          {item.resources.length > 0 && (
            <div className="rounded-xl border border-sky-200 bg-sky-50/50 p-4">
              <h3 className="flex items-center gap-1.5 text-xs font-semibold text-sky-900">
                <Link2 className="h-3.5 w-3.5 text-sky-600" />
                原文提及的开源项目 / 教程 / 资源
              </h3>
              <ul className="mt-2 list-disc space-y-1 pl-4 text-xs leading-relaxed text-sky-950">
                {item.resources.map((r, i) => (
                  <li key={i} className="break-all">
                    {r}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* 避坑提醒 */}
          {item.pitfalls.length > 0 && (
            <div className="rounded-xl border border-amber-200 bg-amber-50/60 p-4">
              <h3 className="flex items-center gap-1.5 text-xs font-semibold text-amber-900">
                <AlertTriangle className="h-3.5 w-3.5 text-amber-600" />
                避坑提醒与高频误区
              </h3>
              <ul className="mt-2 list-disc space-y-1 pl-4 text-xs leading-relaxed text-amber-950">
                {item.pitfalls.map((p, i) => (
                  <li key={i}>{p}</li>
                ))}
              </ul>
            </div>
          )}

          {/* 防编造原文证据 */}
          <div className="rounded-xl border border-emerald-200 bg-emerald-50/60 p-4">
            <div className="flex items-center justify-between">
              <h3 className="flex items-center gap-1.5 text-xs font-semibold text-emerald-800">
                <Quote className="h-3.5 w-3.5" />
                防编造原文证据（已通过代码核验）
              </h3>
              <span className="font-mono text-[11px] text-emerald-700">
                置信度 {(item.confidence * 100).toFixed(0)}%
              </span>
            </div>
            <blockquote className="mt-2 border-l-2 border-emerald-400 pl-3 text-sm leading-relaxed text-emerald-950">
              “{item.evidence}”
            </blockquote>
          </div>

          <div className="space-y-1 border-t border-slate-100 pt-4 text-xs text-slate-500">
            <div>原帖标题：{item.note_title || '-'}</div>
            <div>作者：{item.note_author || '-'}</div>
          </div>
        </div>

        <div className="flex items-center justify-between border-t border-slate-100 bg-slate-50/70 px-6 py-3">
          <button
            onClick={() => onDelete(item.id)}
            className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-rose-600 hover:bg-rose-50"
          >
            <Trash2 className="h-3.5 w-3.5" />
            删除此条
          </button>
          <a
            href={item.source_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-3.5 py-1.5 text-xs font-medium text-white hover:bg-slate-800"
          >
            去小红书看原帖
            <ExternalLink className="h-3.5 w-3.5" />
          </a>
        </div>
      </aside>
    </div>
  )
}
