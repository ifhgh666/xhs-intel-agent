import { useEffect, useState } from 'react'
import {
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  FileText,
  Image as ImageIcon,
  Loader2,
  MapPin,
  X,
} from 'lucide-react'
import { api } from '../api/client'
import type { NoteSnapshot } from '../api/types'

interface OriginalNoteViewerProps {
  noteId: string
  defaultExpanded?: boolean
}

export function OriginalNoteViewer({ noteId, defaultExpanded = false }: OriginalNoteViewerProps) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const [loading, setLoading] = useState(false)
  const [snapshot, setSnapshot] = useState<NoteSnapshot | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [failedImages, setFailedImages] = useState<Set<number>>(new Set())
  const [lightboxUrl, setLightboxUrl] = useState<string | null>(null)

  useEffect(() => {
    setExpanded(defaultExpanded)
    setSnapshot(null)
    setError(null)
    setFailedImages(new Set())
    setLightboxUrl(null)
  }, [noteId, defaultExpanded])

  useEffect(() => {
    if (!expanded || snapshot || loading) return
    let cancelled = false
    setLoading(true)
    setError(null)
    api
      .getNoteSnapshot(noteId)
      .then((res) => {
        if (!cancelled) setSnapshot(res)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : '加载本地原帖失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [expanded, noteId, snapshot, loading])

  const handleCopy = async () => {
    if (!snapshot) return
    const text = `${snapshot.title}\n\n${snapshot.desc}`.trim()
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1800)
    } catch {
      // ignore
    }
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/70">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full cursor-pointer items-center justify-between px-4 py-3 text-left transition hover:bg-slate-100/70"
      >
        <div className="flex items-center gap-2">
          <FileText className="h-4 w-4 text-slate-600" />
          <span className="text-xs font-semibold text-slate-800">
            本地原帖正文与配图（免扫码直接看）
          </span>
          {snapshot && snapshot.images.length > 0 && (
            <span className="rounded-full bg-slate-200/80 px-1.5 py-0.5 text-[10px] font-medium text-slate-600">
              {snapshot.images.length} 图
            </span>
          )}
        </div>
        <span className="inline-flex items-center gap-1 text-[11px] font-medium text-slate-500">
          {expanded ? '收起' : '展开查看'}
          {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
        </span>
      </button>

      {expanded && (
        <div className="border-t border-slate-200/80 px-4 py-3.5">
          {loading ? (
            <div className="flex items-center gap-2 py-4 text-xs text-slate-500">
              <Loader2 className="h-3.5 w-3.5 animate-spin text-slate-400" />
              正在读取本地缓存原帖…
            </div>
          ) : error ? (
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              {error}
            </div>
          ) : snapshot ? (
            <div className="space-y-3.5">
              {/* 标题与元信息 */}
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="text-sm font-semibold text-slate-900">{snapshot.title}</div>
                  <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[11px] text-slate-500">
                    {snapshot.author && <span>作者：{snapshot.author}</span>}
                    {snapshot.ip_location && (
                      <span className="inline-flex items-center gap-0.5">
                        <MapPin className="h-3 w-3" />
                        {snapshot.ip_location}
                      </span>
                    )}
                    {snapshot.published_at && (
                      <span>发布：{new Date(snapshot.published_at).toLocaleString('zh-CN')}</span>
                    )}
                  </div>
                </div>
                {snapshot.desc && (
                  <button
                    type="button"
                    onClick={() => void handleCopy()}
                    className="inline-flex shrink-0 cursor-pointer items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] font-medium text-slate-600 hover:bg-slate-50"
                  >
                    {copied ? (
                      <>
                        <Check className="h-3 w-3 text-emerald-600" />
                        已复制
                      </>
                    ) : (
                      <>
                        <Copy className="h-3 w-3" />
                        复制原文
                      </>
                    )}
                  </button>
                )}
              </div>

              {/* 正文全文 */}
              {snapshot.desc ? (
                <div className="max-h-72 overflow-y-auto rounded-lg border border-slate-200/80 bg-white p-3 text-xs leading-relaxed whitespace-pre-wrap text-slate-800">
                  {snapshot.desc}
                </div>
              ) : (
                <div className="rounded-lg border border-dashed border-slate-200 bg-white px-3 py-2 text-xs text-slate-400">
                  {snapshot.has_detail ? '原帖无正文文字（可能以图片为主）' : '本地仅缓存了标题，暂无完整正文快照'}
                </div>
              )}

              {/* 标签 */}
              {snapshot.tags.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {snapshot.tags.map((tag, i) => (
                    <span
                      key={`${tag}-${i}`}
                      className="rounded-full bg-white px-2 py-0.5 text-[11px] text-slate-600 ring-1 ring-slate-200"
                    >
                      #{tag}
                    </span>
                  ))}
                </div>
              )}

              {/* 原帖配图 */}
              {snapshot.images.length > 0 && (
                <div>
                  <div className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold text-slate-600">
                    <ImageIcon className="h-3.5 w-3.5 text-slate-400" />
                    原帖配图（共 {snapshot.images.length} 张，点击可查看大图）
                  </div>
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                    {snapshot.images.map((img, idx) =>
                      failedImages.has(idx) ? (
                        <div
                          key={idx}
                          className="flex h-28 flex-col items-center justify-center rounded-lg border border-dashed border-slate-200 bg-white p-2 text-center text-[10px] text-slate-400"
                        >
                          <ImageIcon className="mb-1 h-4 w-4 text-slate-300" />
                          图 {idx + 1} 签名已过期
                        </div>
                      ) : (
                        <button
                          key={idx}
                          type="button"
                          onClick={() => setLightboxUrl(img.url)}
                          className="group relative cursor-zoom-in overflow-hidden rounded-lg border border-slate-200 bg-white"
                        >
                          <img
                            src={img.preview_url || img.url}
                            alt={`原帖配图 ${idx + 1}`}
                            referrerPolicy="no-referrer"
                            loading="lazy"
                            onError={() =>
                              setFailedImages((prev) => {
                                const next = new Set(prev)
                                next.add(idx)
                                return next
                              })
                            }
                            className="h-32 w-full object-cover transition group-hover:scale-105"
                          />
                          <span className="absolute right-1.5 bottom-1.5 rounded bg-black/55 px-1.5 py-0.5 font-mono text-[10px] text-white">
                            {idx + 1}/{snapshot.images.length}
                          </span>
                        </button>
                      ),
                    )}
                  </div>
                </div>
              )}
            </div>
          ) : null}
        </div>
      )}

      {/* 原图全屏放大预览 */}
      {lightboxUrl && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-xs"
          onClick={() => setLightboxUrl(null)}
        >
          <button
            type="button"
            onClick={() => setLightboxUrl(null)}
            className="fixed top-4 right-4 cursor-pointer rounded-full bg-white/15 p-2 text-white hover:bg-white/25"
          >
            <X className="h-5 w-5" />
          </button>
          <img
            src={lightboxUrl}
            alt="原帖大图预览"
            referrerPolicy="no-referrer"
            onClick={(e) => e.stopPropagation()}
            className="max-h-[90vh] max-w-[92vw] rounded-lg object-contain shadow-2xl"
          />
        </div>
      )}
    </div>
  )
}
