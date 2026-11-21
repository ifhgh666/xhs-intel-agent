import { useEffect, useState } from 'react'
import { CheckCircle2, Loader2, QrCode, RefreshCw, X } from 'lucide-react'
import { api } from '../api/client'

interface LoginQrModalProps {
  open: boolean
  onClose: () => void
  onLoggedIn: () => void
}

export function LoginQrModal({ open, onClose, onLoggedIn }: LoginQrModalProps) {
  const [loading, setLoading] = useState(false)
  const [qrImg, setQrImg] = useState<string | null>(null)
  const [hint, setHint] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)

  const loadQr = async () => {
    setLoading(true)
    setError(null)
    setSuccess(false)
    try {
      const res = await api.fetchLoginQrcode()
      setQrImg(res.image_base64)
      setHint(res.hint_text || '请打开手机小红书 App 扫码并确认登录')
    } catch (err) {
      setError(err instanceof Error ? err.message : '获取二维码失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!open) return
    void loadQr()
  }, [open])

  // 每 3 秒轮询登录状态，扫码确认后自动关闭
  useEffect(() => {
    if (!open || success || !qrImg) return
    const timer = window.setInterval(async () => {
      try {
        const st = await api.getSystemStatus(true)
        if (st.logged_in) {
          setSuccess(true)
          window.setTimeout(() => {
            onLoggedIn()
            onClose()
          }, 900)
        }
      } catch {
        // ignore transient poll error
      }
    }, 3000)
    return () => window.clearInterval(timer)
  }, [open, success, qrImg, onClose, onLoggedIn])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/45 p-4 backdrop-blur-xs">
      <div className="w-full max-w-sm rounded-2xl border border-slate-200 bg-white p-6 shadow-xl">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-base font-semibold text-slate-900">
            <QrCode className="h-5 w-5 text-rose-600" />
            <span>小红书扫码登录</span>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="mt-5 flex flex-col items-center">
          {success ? (
            <div className="flex h-56 w-56 flex-col items-center justify-center rounded-xl bg-emerald-50 text-emerald-700">
              <CheckCircle2 className="h-12 w-12" />
              <p className="mt-3 text-sm font-medium">登录成功，正在刷新状态…</p>
            </div>
          ) : loading ? (
            <div className="flex h-56 w-56 flex-col items-center justify-center rounded-xl bg-slate-50 text-slate-500">
              <Loader2 className="h-8 w-8 animate-spin text-rose-500" />
              <p className="mt-3 text-xs">正在从 MCP 获取登录二维码…</p>
            </div>
          ) : error ? (
            <div className="flex h-56 w-full flex-col items-center justify-center rounded-xl bg-rose-50 p-4 text-center text-rose-700">
              <p className="text-sm font-medium">{error}</p>
              <button
                onClick={() => void loadQr()}
                className="mt-3 inline-flex items-center gap-1.5 rounded-lg bg-rose-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-rose-700"
              >
                <RefreshCw className="h-3.5 w-3.5" />
                重试获取
              </button>
            </div>
          ) : qrImg ? (
            <div className="rounded-xl border border-slate-200 bg-white p-3 shadow-xs">
              <img src={qrImg} alt="小红书登录二维码" className="h-52 w-52 object-contain" />
            </div>
          ) : (
            <div className="flex h-56 w-56 items-center justify-center rounded-xl bg-slate-50 p-4 text-center text-xs text-slate-500">
              未返回二维码图片，请查看提示文字或刷新重试。
            </div>
          )}

          {hint && !error && !success && (
            <p className="mt-4 text-center text-xs leading-relaxed text-slate-600">{hint}</p>
          )}
        </div>

        <div className="mt-5 flex items-center justify-between border-t border-slate-100 pt-4">
          <span className="text-[11px] text-slate-400">自动每 3 秒检测登录状态</span>
          <button
            onClick={() => void loadQr()}
            disabled={loading}
            className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            刷新二维码
          </button>
        </div>
      </div>
    </div>
  )
}
