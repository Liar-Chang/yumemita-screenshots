import { useEffect, useMemo, useRef, useState } from 'react'

type Item = {
  id: string
  ep: number
  t: number
  text: string
  img: string
  tags: string[]
}

const BASE = import.meta.env.BASE_URL
const PAGE = 120

/** 全半形/大小寫/空白 正規化，讓搜尋寬鬆一點 */
function norm(s: string): string {
  return s.normalize('NFKC').toLowerCase().replace(/\s+/g, '')
}

function fmtTime(t: number): string {
  const m = Math.floor(t / 60)
  const s = Math.floor(t % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

/** 保留目前的搜尋/集數篩選，換成指向特定截圖的分享連結 */
function shareUrl(id: string): string {
  const p = new URLSearchParams(location.search)
  p.set('id', id)
  return `${location.origin}${location.pathname}?${p.toString()}`
}

function useToast(): [string, (m: string) => void] {
  const [msg, setMsg] = useState('')
  const timer = useRef<number>(0)
  const show = (m: string) => {
    setMsg(m)
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setMsg(''), 1600)
  }
  return [msg, show]
}

async function copyImageToClipboard(url: string): Promise<void> {
  const blob = await (await fetch(url)).blob()
  const bmp = await createImageBitmap(blob)
  const canvas = document.createElement('canvas')
  canvas.width = bmp.width
  canvas.height = bmp.height
  canvas.getContext('2d')!.drawImage(bmp, 0, 0)
  const png: Blob = await new Promise((res, rej) =>
    canvas.toBlob(b => (b ? res(b) : rej(new Error('toBlob failed'))), 'image/png'),
  )
  await navigator.clipboard.write([new ClipboardItem({ 'image/png': png })])
}

export default function App() {
  const [items, setItems] = useState<Item[] | null>(null)
  const [loadError, setLoadError] = useState(false)
  // 直接從網址參數初始化（不能放 effect：StrictMode 下網址同步 effect 會先清掉參數）
  const [q, setQ] = useState(() => new URLSearchParams(location.search).get('q') ?? '')
  const [ep, setEp] = useState(() => Number(new URLSearchParams(location.search).get('ep')) || 0)
  const [desc, setDesc] = useState(false)
  const [shown, setShown] = useState(PAGE)
  const [selected, setSelected] = useState<Item | null>(null)
  const [toast, showToast] = useToast()
  const sentinel = useRef<HTMLDivElement>(null)
  const pendingId = useRef<string | null>(new URLSearchParams(location.search).get('id'))

  // 載入索引
  useEffect(() => {
    fetch(`${BASE}index/yumemita.json`)
      .then(r => r.json())
      .then(d => setItems(d.items))
      .catch(() => setLoadError(true))
  }, [])

  // 開啟分享連結指定的截圖
  useEffect(() => {
    if (items && pendingId.current) {
      const hit = items.find(i => i.id === pendingId.current)
      pendingId.current = null
      if (hit) setSelected(hit)
    }
  }, [items])

  // 搜尋/篩選狀態 → 網址（可直接分享）
  useEffect(() => {
    const p = new URLSearchParams()
    if (q) p.set('q', q)
    if (ep) p.set('ep', String(ep))
    if (selected) p.set('id', selected.id)
    const qs = p.toString()
    history.replaceState(null, '', qs ? `?${qs}` : location.pathname)
  }, [q, ep, selected])

  const episodes = useMemo(() => {
    if (!items) return []
    const count = new Map<number, number>()
    for (const i of items) count.set(i.ep, (count.get(i.ep) ?? 0) + 1)
    return [...count.entries()].sort((a, b) => a[0] - b[0])
  }, [items])

  const filtered = useMemo(() => {
    if (!items) return []
    const nq = norm(q)
    const r = items.filter(
      i =>
        (ep === 0 || i.ep === ep) &&
        (nq === '' || norm(i.text).includes(nq) || i.tags.some(t => norm(t).includes(nq))),
    )
    return r.sort((a, b) => (a.ep - b.ep || a.t - b.t) * (desc ? -1 : 1))
  }, [items, q, ep, desc])

  // 搜尋條件變動 → 重置分頁
  useEffect(() => setShown(PAGE), [q, ep, desc])

  // 無限捲動
  useEffect(() => {
    const el = sentinel.current
    if (!el) return
    const ob = new IntersectionObserver(
      e => e[0].isIntersecting && setShown(n => n + PAGE),
      { rootMargin: '600px' },
    )
    ob.observe(el)
    return () => ob.disconnect()
  }, [])

  // Lightbox 鍵盤操作
  useEffect(() => {
    if (!selected) return
    const idx = filtered.findIndex(i => i.id === selected.id)
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setSelected(null)
      if (e.key === 'ArrowLeft' && idx > 0) setSelected(filtered[idx - 1])
      if (e.key === 'ArrowRight' && idx >= 0 && idx < filtered.length - 1)
        setSelected(filtered[idx + 1])
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selected, filtered])

  const selIdx = selected ? filtered.findIndex(i => i.id === selected.id) : -1

  return (
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="pt-10 pb-6 px-4 text-center">
        <h1 className="text-3xl sm:text-4xl font-black tracking-wide bg-gradient-to-r from-pink-400 via-fuchsia-400 to-cyan-300 bg-clip-text text-transparent">
          YUME∞MITA 截圖搜尋器
        </h1>
        <p className="mt-2 text-sm text-zinc-400">
          BanG Dream! YUME∞MITA 台詞截圖搜尋——輸入關鍵字，或選擇集數瀏覽
        </p>
      </header>

      {/* Controls */}
      <div className="sticky top-0 z-20 bg-[#0b0b13]/90 backdrop-blur border-b border-white/5 px-4 py-3">
        <div className="max-w-6xl mx-auto flex flex-wrap gap-2 items-center">
          <input
            value={q}
            onChange={e => setQ(e.target.value)}
            placeholder="輸入關鍵字，例如：夢"
            className="flex-1 min-w-48 rounded-xl bg-white/5 border border-white/10 px-4 py-2.5 outline-none focus:border-pink-400/60 focus:ring-2 focus:ring-pink-400/20 placeholder:text-zinc-500"
          />
          <select
            value={ep}
            onChange={e => setEp(Number(e.target.value))}
            className="rounded-xl bg-white/5 border border-white/10 px-3 py-2.5 outline-none focus:border-cyan-300/60 text-sm"
          >
            <option value={0} className="bg-zinc-900">全部集數</option>
            {episodes.map(([n, c]) => (
              <option key={n} value={n} className="bg-zinc-900">
                第 {n} 集（{c}）
              </option>
            ))}
          </select>
          <button
            onClick={() => setDesc(d => !d)}
            title="切換排序方向"
            className="rounded-xl bg-white/5 border border-white/10 px-3 py-2.5 text-sm hover:border-white/25 transition-colors"
          >
            {desc ? '時間 ↓' : '時間 ↑'}
          </button>
        </div>
        {items && (
          <div className="max-w-6xl mx-auto mt-2 text-xs text-zinc-500">
            {q || ep !== 0 ? `找到 ${filtered.length} 張` : `共收錄 ${items.length} 張`}
          </div>
        )}
      </div>

      {/* Grid */}
      <main className="flex-1 max-w-6xl w-full mx-auto px-4 py-6">
        {loadError && (
          <p className="text-center text-zinc-400 py-20">索引載入失敗，請重新整理再試。</p>
        )}
        {!items && !loadError && (
          <p className="text-center text-zinc-500 py-20 animate-pulse">載入中…</p>
        )}
        {items && filtered.length === 0 && (
          <p className="text-center text-zinc-500 py-20">找不到符合的截圖，換個關鍵字試試。</p>
        )}
        <div className="grid grid-cols-[repeat(auto-fill,minmax(230px,1fr))] gap-3">
          {filtered.slice(0, shown).map(i => (
            <div
              key={i.id}
              role="button"
              tabIndex={0}
              onClick={() => setSelected(i)}
              onKeyDown={e => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  setSelected(i)
                }
              }}
              className="fade-in text-left rounded-xl overflow-hidden bg-white/5 border border-white/10 hover:border-pink-400/50 hover:-translate-y-0.5 transition-all group cursor-pointer"
            >
              <div className="relative">
                <img
                  src={BASE + i.img}
                  alt={i.text || '場景截圖'}
                  loading="lazy"
                  className="w-full aspect-video object-cover bg-black/40"
                />
                <div className="absolute top-1.5 right-1.5 flex items-center gap-0.5 rounded-full bg-black/55 backdrop-blur-sm px-1 py-1">
                  <button
                    onClick={e => {
                      e.stopPropagation()
                      copyImageToClipboard(BASE + i.img)
                        .then(() => showToast('已複製圖片 ✓'))
                        .catch(() => showToast('複製失敗，改用下載吧'))
                    }}
                    title="複製圖片"
                    className="p-1 rounded-full hover:bg-white/25 transition-colors"
                  >
                    <svg viewBox="0 0 20 20" width="15" height="15" fill="none" stroke="currentColor" strokeWidth={1.6}>
                      <rect x="3" y="4" width="14" height="12" rx="1.5" />
                      <circle cx="7.3" cy="8.3" r="1.3" />
                      <path d="M4 14.5l4-4 3 3 2-2 4 3.5" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </button>
                  <button
                    onClick={e => {
                      e.stopPropagation()
                      navigator.clipboard.writeText(shareUrl(i.id)).then(() => showToast('已複製連結 ✓'))
                    }}
                    title="複製連結"
                    className="p-1 rounded-full hover:bg-white/25 transition-colors"
                  >
                    <svg viewBox="0 0 20 20" width="15" height="15" fill="none" stroke="currentColor" strokeWidth={1.6}>
                      <path
                        d="M8.5 11.5l3-3M7 12.5l-1.2 1.2a2.3 2.3 0 01-3.3-3.3L3.8 9.2M12.2 7.8L13.4 6.6a2.3 2.3 0 013.3 3.3L15.5 11"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </button>
                  <a
                    href={BASE + i.img}
                    download={`${i.text || i.id}.webp`}
                    onClick={e => e.stopPropagation()}
                    title="下載"
                    className="p-1 rounded-full hover:bg-white/25 transition-colors"
                  >
                    <svg viewBox="0 0 20 20" width="15" height="15" fill="none" stroke="currentColor" strokeWidth={1.6}>
                      <path d="M10 3.5v9m0 0l-3-3M10 12.5l3-3M4.5 15.5h11" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </a>
                </div>
              </div>
              <div className="p-2.5">
                <p className={`text-sm leading-snug line-clamp-2 min-h-10 ${i.text ? '' : 'text-zinc-500 italic'}`}>
                  {i.text || `（${i.tags[0] ?? '場景'}）`}
                </p>
                <p className="mt-1.5 text-[11px] text-zinc-500 group-hover:text-zinc-400">
                  第 {i.ep} 集 · {fmtTime(i.t)}
                </p>
              </div>
            </div>
          ))}
        </div>
        <div ref={sentinel} className="h-1" />
      </main>

      {/* Lightbox */}
      {selected && (
        <div
          className="fixed inset-0 z-50 bg-black/85 backdrop-blur-sm flex items-center justify-center p-4"
          onClick={() => setSelected(null)}
        >
          <div
            className="fade-in max-w-4xl w-full rounded-2xl overflow-hidden bg-[#14141f] border border-white/10 shadow-2xl"
            onClick={e => e.stopPropagation()}
          >
            <div className="relative">
              <img src={BASE + selected.img} alt={selected.text} className="w-full" />
              <div className="absolute top-2 right-2 flex items-center gap-0.5 rounded-full bg-black/55 backdrop-blur-sm px-1.5 py-1.5">
                <button
                  onClick={() =>
                    copyImageToClipboard(BASE + selected.img)
                      .then(() => showToast('已複製圖片 ✓'))
                      .catch(() => showToast('複製失敗，改用下載吧'))
                  }
                  title="複製圖片"
                  className="p-1.5 rounded-full hover:bg-white/20 transition-colors"
                >
                  <svg viewBox="0 0 20 20" width="17" height="17" fill="none" stroke="currentColor" strokeWidth={1.6}>
                    <rect x="3" y="4" width="14" height="12" rx="1.5" />
                    <circle cx="7.3" cy="8.3" r="1.3" />
                    <path d="M4 14.5l4-4 3 3 2-2 4 3.5" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </button>
                {selected.text && (
                  <button
                    onClick={() =>
                      navigator.clipboard.writeText(selected.text).then(() => showToast('已複製台詞 ✓'))
                    }
                    title="複製台詞"
                    className="p-1.5 rounded-full hover:bg-white/20 transition-colors"
                  >
                    <svg viewBox="0 0 20 20" width="17" height="17" fill="none" stroke="currentColor" strokeWidth={1.6}>
                      <path d="M4 6h12M4 10h12M4 14h7" strokeLinecap="round" />
                    </svg>
                  </button>
                )}
                <button
                  onClick={() =>
                    navigator.clipboard.writeText(location.href).then(() => showToast('已複製連結 ✓'))
                  }
                  title="複製連結"
                  className="p-1.5 rounded-full hover:bg-white/20 transition-colors"
                >
                  <svg viewBox="0 0 20 20" width="17" height="17" fill="none" stroke="currentColor" strokeWidth={1.6}>
                    <path
                      d="M8.5 11.5l3-3M7 12.5l-1.2 1.2a2.3 2.3 0 01-3.3-3.3L3.8 9.2M12.2 7.8L13.4 6.6a2.3 2.3 0 013.3 3.3L15.5 11"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                </button>
                <a
                  href={BASE + selected.img}
                  download={`${selected.text || selected.id}.webp`}
                  title="下載"
                  className="p-1.5 rounded-full hover:bg-white/20 transition-colors"
                >
                  <svg viewBox="0 0 20 20" width="17" height="17" fill="none" stroke="currentColor" strokeWidth={1.6}>
                    <path d="M10 3.5v9m0 0l-3-3M10 12.5l3-3M4.5 15.5h11" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </a>
                <button
                  onClick={() => setSelected(null)}
                  title="關閉"
                  className="ml-0.5 p-1.5 rounded-full hover:bg-white/20 transition-colors"
                >
                  <svg viewBox="0 0 20 20" width="17" height="17" fill="none" stroke="currentColor" strokeWidth={2}>
                    <path d="M5.5 5.5l9 9M14.5 5.5l-9 9" strokeLinecap="round" />
                  </svg>
                </button>
              </div>
              {selIdx > 0 && (
                <button
                  onClick={() => setSelected(filtered[selIdx - 1])}
                  className="absolute left-2 top-1/2 -translate-y-1/2 size-10 rounded-full bg-black/50 hover:bg-black/75 text-lg"
                  title="上一張（←）"
                >
                  ←
                </button>
              )}
              {selIdx < filtered.length - 1 && (
                <button
                  onClick={() => setSelected(filtered[selIdx + 1])}
                  className="absolute right-2 top-1/2 -translate-y-1/2 size-10 rounded-full bg-black/50 hover:bg-black/75 text-lg"
                  title="下一張（→）"
                >
                  →
                </button>
              )}
            </div>
            <div className="px-4 py-3">
              <p className="text-base">{selected.text || `（${selected.tags[0] ?? '場景'}）`}</p>
              <p className="mt-1 text-xs text-zinc-500">
                第 {selected.ep} 集 · {fmtTime(selected.t)}
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Toast */}
      {toast && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-[60] px-4 py-2 rounded-full bg-zinc-800 border border-white/15 text-sm shadow-lg fade-in">
          {toast}
        </div>
      )}

      <footer className="px-4 py-8 text-center text-xs text-zinc-600 leading-relaxed">
        非官方粉絲網站，僅供交流使用。動畫截圖著作權屬 ©BanG Dream! Project／原權利人所有，
        <br className="hidden sm:block" />
        如權利方提出要求將立即配合下架。
      </footer>
    </div>
  )
}
