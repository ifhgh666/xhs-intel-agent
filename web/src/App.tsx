import { useCallback, useEffect, useState } from 'react'
import { Activity, Briefcase, MessageSquareText } from 'lucide-react'
import { api } from './api/client'
import type { SystemStatus } from './api/types'
import { LoginQrModal } from './components/LoginQrModal'
import { TopStatusBar } from './components/TopStatusBar'
import { JobsPage } from './pages/JobsPage'
import { TracesPage } from './pages/TracesPage'
import { WorkspacePage } from './pages/WorkspacePage'

type PageTab = 'workspace' | 'jobs' | 'traces'

export function App() {
  const [activeTab, setActiveTab] = useState<PageTab>('workspace')
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [loadingStatus, setLoadingStatus] = useState(false)
  const [reconnecting, setReconnecting] = useState(false)
  const [loginModalOpen, setLoginModalOpen] = useState(false)

  const refreshStatus = useCallback(async (forceRefresh = false) => {
    setLoadingStatus(true)
    try {
      const st = await api.getSystemStatus(forceRefresh)
      setStatus(st)
    } catch {
      // ignore if backend restarting
    } finally {
      setLoadingStatus(false)
    }
  }, [])

  useEffect(() => {
    void refreshStatus(false)
    const timer = window.setInterval(() => void refreshStatus(false), 15000)
    return () => window.clearInterval(timer)
  }, [refreshStatus])

  const handleReconnect = async () => {
    setReconnecting(true)
    try {
      await api.reconnectMcp()
      await refreshStatus(false)
    } finally {
      setReconnecting(false)
    }
  }

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-slate-50 text-slate-900">
      {/* 左侧主导航栏 */}
      <nav className="flex w-52 shrink-0 flex-col border-r border-slate-200 bg-white">
        <div className="flex h-14 items-center gap-2.5 border-b border-slate-200 px-4">
          <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-rose-600 text-xs font-bold text-white shadow-xs">
            XHS
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm font-bold text-slate-900">实习情报 Agent</div>
            <div className="truncate text-[10px] text-slate-400">DeepSeek + MCP</div>
          </div>
        </div>

        <div className="flex-1 space-y-1 p-3">
          <NavItem
            active={activeTab === 'workspace'}
            icon={<MessageSquareText className="h-4 w-4" />}
            label="智能检索工作台"
            onClick={() => setActiveTab('workspace')}
          />
          <NavItem
            active={activeTab === 'jobs'}
            icon={<Briefcase className="h-4 w-4" />}
            label="岗位情报库"
            badge={status ? String(status.db_jobs_count) : undefined}
            onClick={() => setActiveTab('jobs')}
          />
          <NavItem
            active={activeTab === 'traces'}
            icon={<Activity className="h-4 w-4" />}
            label="监控与 Trace"
            onClick={() => setActiveTab('traces')}
          />
        </div>

        <div className="border-t border-slate-100 p-4 text-[11px] leading-relaxed text-slate-400">
          仅供个人学习使用
          <br />
          已启用只读白名单与防风控限流
        </div>
      </nav>

      {/* 右侧主区域 */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <TopStatusBar
          status={status}
          loading={loadingStatus}
          reconnecting={reconnecting}
          onRefresh={() => void refreshStatus(true)}
          onReconnect={() => void handleReconnect()}
          onOpenLogin={() => setLoginModalOpen(true)}
        />

        <main className="min-h-0 flex-1 overflow-hidden">
          {activeTab === 'workspace' && (
            <WorkspacePage
              systemStatus={status}
              onRefreshSystem={() => void refreshStatus(false)}
              onOpenLogin={() => setLoginModalOpen(true)}
            />
          )}
          {activeTab === 'jobs' && <JobsPage onJobsMutated={() => void refreshStatus(false)} />}
          {activeTab === 'traces' && <TracesPage />}
        </main>
      </div>

      <LoginQrModal
        open={loginModalOpen}
        onClose={() => setLoginModalOpen(false)}
        onLoggedIn={() => void refreshStatus(false)}
      />
    </div>
  )
}

function NavItem({
  active,
  icon,
  label,
  badge,
  onClick,
}: {
  active: boolean
  icon: React.ReactNode
  label: string
  badge?: string
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={`flex w-full cursor-pointer items-center justify-between rounded-xl px-3 py-2.5 text-xs font-medium transition ${
        active
          ? 'bg-rose-50 text-rose-700 ring-1 ring-rose-200/70'
          : 'text-slate-600 hover:bg-slate-100/70 hover:text-slate-900'
      }`}
    >
      <span className="flex items-center gap-2.5">
        {icon}
        {label}
      </span>
      {badge !== undefined && (
        <span
          className={`rounded-full px-2 py-0.5 font-mono text-[10px] ${
            active ? 'bg-rose-600 text-white' : 'bg-slate-100 text-slate-600'
          }`}
        >
          {badge}
        </span>
      )}
    </button>
  )
}
