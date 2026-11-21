import type { JobStatus } from '../api/types'

export const STATUS_META: Record<
  JobStatus,
  { label: string; bg: string; text: string; ring: string }
> = {
  new: {
    label: '新发现',
    bg: 'bg-sky-50',
    text: 'text-sky-700',
    ring: 'ring-sky-200',
  },
  interested: {
    label: '感兴趣',
    bg: 'bg-amber-50',
    text: 'text-amber-700',
    ring: 'ring-amber-200',
  },
  applied: {
    label: '已投递',
    bg: 'bg-emerald-50',
    text: 'text-emerald-700',
    ring: 'ring-emerald-200',
  },
  rejected: {
    label: '不合适',
    bg: 'bg-rose-50',
    text: 'text-rose-700',
    ring: 'ring-rose-200',
  },
  expired: {
    label: '已过期',
    bg: 'bg-slate-100',
    text: 'text-slate-500',
    ring: 'ring-slate-200',
  },
}

export const ALL_STATUSES: JobStatus[] = ['new', 'interested', 'applied', 'rejected', 'expired']

interface StatusSelectProps {
  value: JobStatus
  disabled?: boolean
  onChange: (next: JobStatus) => void
}

export function StatusSelect({ value, disabled, onChange }: StatusSelectProps) {
  const meta = STATUS_META[value] ?? STATUS_META.new
  return (
    <select
      value={value}
      disabled={disabled}
      onClick={(e) => e.stopPropagation()}
      onChange={(e) => onChange(e.target.value as JobStatus)}
      className={`cursor-pointer rounded-full px-2.5 py-1 text-xs font-medium ring-1 transition outline-none ${meta.bg} ${meta.text} ${meta.ring} disabled:opacity-50`}
    >
      {ALL_STATUSES.map((st) => (
        <option key={st} value={st} className="bg-white text-slate-900">
          {STATUS_META[st].label}
        </option>
      ))}
    </select>
  )
}
