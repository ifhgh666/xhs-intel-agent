import { Activity, AlertTriangle, CheckCircle2, QrCode, RefreshCw, ShieldAlert } from 'lucide-react'
import type { SystemStatus } from '../api/types'

interface TopStatusBarProps {
  status: SystemStatus | null
  loading: boolean
  reconnecting: boolean
  onRefresh: () => void
  onReconnect: () => void
  onOpenLogin: () => void
}

function quotaTone(used: number, limit: number): string {
  const ratio = limit > 0 ? used / limit : 0
  if (ratio >= 1) return 'bg-rose-50 text-rose-700 ring-rose-200'
  if (ratio >= 0.8) return 'bg-amber-50 text-amber-700 ring-amber-200'
  return 'bg-slate-100 text-slate-700 ring-slate-200'
}

export function TopStatusBar({
  status,
  loading,
  reconnecting,
  onRefresh,
  onReconnect,
  onOpenLogin,
}: TopStatusBarProps) {
  const searchQuota = status?.daily_usage.search
  const detailQuota = status?.daily_usage.detail

  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-5">
      <div className="flex items-center gap-3">
        {!status ? (
          <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-3 py-1 text-xs text-slate-500">
            正在检测服务状态…
          </span>
        ) : status.mcp_connected && status.logged_in ? (
          <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700 ring-1 ring-emerald-200">
            <CheckCircle2 className="h-3.5 w-3.5" />
            MCP 已连接 · 小红书已登录
          </span>
        ) : status.mcp_connected && !status.logged_in ? (
          <button
            onClick={onOpenLogin}
            className="inline-flex cursor-pointer items-center gap-1.5 rounded-full bg-amber-50 px-3 py-1 text-xs font-medium text-amber-800 ring-1 ring-amber-300 transition hover:bg-amber-100"
          >
            <QrCode className="h-3.5 w-3.5" />
            小红书未登录 · 点击扫码
          </button>
        ) : (
          <div className="flex items-center gap-2">
            <span
              title={status.mcp_error ?? 'MCP 离线'}
              className="inline-flex items-center gap-1.5 rounded-full bg-rose-50 px-3 py-1 text-xs font-medium text-rose-700 ring-1 ring-rose-200"
            >
              <ShieldAlert className="h-3.5 w-3.5" />
              MCP 离线（降级模式：可查库/导出）
            </span>
            <button
              onClick={onReconnect}
              disabled={reconnecting}
              className="inline-flex cursor-pointer items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
            >
              <RefreshCw className={`h-3 w-3 ${reconnecting ? 'animate-spin' : ''}`} />
              重连 MCP
            </button>
          </div>
        )}

        {status?.active_session_id && (
          <span className="inline-flex items-center gap-1.5 rounded-full bg-rose-50 px-2.5 py-1 text-xs font-medium text-rose-700 ring-1 ring-rose-200">
            <Activity className="h-3.5 w-3.5 animate-pulse" />
            任务运行中 ({status.active_session_id})
          </span>
        )}
      </div>

      <div className="flex items-center gap-2.5">
        {searchQuota && detailQuota && (
          <div className="hidden items-center gap-2 sm:flex">
            <span
              title="当日小红书搜索防风控配额"
              className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 font-mono text-[11px] font-medium ring-1 ${quotaTone(searchQuota.used, searchQuota.limit)}`}
            >
              搜索 {searchQuota.used}/{searchQuota.limit}
            </span>
            <span
              title="当日小红书详情拉取防风控配额"
              className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 font-mono text-[11px] font-medium ring-1 ${quotaTone(detailQuota.used, detailQuota.limit)}`}
            >
              详情 {detailQuota.used}/{detailQuota.limit}
            </span>
            {(searchQuota.used >= searchQuota.limit || detailQuota.used >= detailQuota.limit) && (
              <span className="inline-flex items-center gap-1 text-xs text-rose-600">
                <AlertTriangle className="h-3.5 w-3.5" />
                今日配额已满
              </span>
            )}
          </div>
        )}

        <button
          onClick={onRefresh}
          disabled={loading}
          title="刷新状态与配额"
          className="inline-flex cursor-pointer items-center justify-center rounded-lg border border-slate-200 p-1.5 text-slate-500 transition hover:bg-slate-50 hover:text-slate-700 disabled:opacity-50"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>
    </header>
  )
}
