import { useEffect, useMemo, useRef, useState } from 'react'

type Item = {
  id: string
  ep: number
  t: number
  text: string
  img: string
  tags: string[]
  source?: string
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

/** OP/ED 是跨集共用的獨立分類，用特殊 ep 值標記（-1=OP，1000=ED；0 保留給「全部集數」） */
function epLabel(ep: number): string {
  if (ep === -1) return 'OP'
  if (ep === 1000) return 'ED'
  return `第 ${ep} 集`
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

const IS_LOCAL = location.hostname === 'localhost' || location.hostname === '127.0.0.1'
const CAN_EDIT = IS_LOCAL && 'showDirectoryPicker' in window

/** 編輯模式：用 File System Access API 直接讀寫本機的專案資料夾（改台詞、刪圖都要用到） */
async function pickProjectRoot(): Promise<FileSystemDirectoryHandle> {
  const dir = await (window as any).showDirectoryPicker({ id: 'yumemita-root' })
  // 驗證選對資料夾（要選專案根目錄，同時有 site 跟 素材 兩層才對）
  await dir.getDirectoryHandle('site')
  await dir.getDirectoryHandle('素材')
  return dir
}

async function getPublicDir(root: FileSystemDirectoryHandle): Promise<FileSystemDirectoryHandle> {
  const site = await root.getDirectoryHandle('site')
  return site.getDirectoryHandle('public')
}

async function writeIndex(
  root: FileSystemDirectoryHandle,
  items: Item[],
  draftEpisodes: number[],
): Promise<void> {
  const publicDir = await getPublicDir(root)
  const indexDir = await publicDir.getDirectoryHandle('index')
  const fileHandle = await indexDir.getFileHandle('yumemita.json')
  const writable = await (fileHandle as any).createWritable()
  await writable.write(JSON.stringify({ series: 'yumemita', items, draftEpisodes }))
  await writable.close()
}

/** 刪掉單一圖片檔案（不處理索引/排除清單，給批次刪除共用） */
async function removeImageFile(root: FileSystemDirectoryHandle, img: string): Promise<void> {
  const parts = img.split('/') // "img/e01/0042.webp" -> ["img","e01","0042.webp"]
  const publicDir = await getPublicDir(root)
  let dir = publicDir
  for (const part of parts.slice(0, -1)) dir = await dir.getDirectoryHandle(part)
  await (dir as any).removeEntry(parts[parts.length - 1])
}

/** 記入排除清單（跟 prune.py 用同一份檔案，重跑管線不會復活），一次可以加多筆。
 * 有帶 source 的話 batch_add_images.py 重跑也不會把原始截圖當新檔案復活匯入。 */
async function appendExcludeList(
  root: FileSystemDirectoryHandle,
  entries: { ep: number; t: number; text: string; source?: string }[],
): Promise<void> {
  const 素材 = await root.getDirectoryHandle('素材')
  let exclude: { ep: number; t: number; text: string; source?: string }[] = []
  try {
    const fh = await 素材.getFileHandle('排除清單.json')
    exclude = JSON.parse(await (await fh.getFile()).text())
  } catch {
    // 檔案不存在就從空清單開始
  }
  exclude.push(...entries)
  const fh = await 素材.getFileHandle('排除清單.json', { create: true })
  const writable = await (fh as any).createWritable()
  await writable.write(JSON.stringify(exclude, null, 1))
  await writable.close()
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
  const [editMode, setEditMode] = useState(false)
  const [rootHandle, setRootHandle] = useState<FileSystemDirectoryHandle | null>(null)
  const [dirty, setDirty] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [draftEpisodes, setDraftEpisodes] = useState<number[]>([])
  const [dragId, setDragId] = useState<string | null>(null)
  const [overId, setOverId] = useState<string | null>(null)
  const sentinel = useRef<HTMLDivElement>(null)
  const pendingId = useRef<string | null>(new URLSearchParams(location.search).get('id'))

  const updateText = (id: string, text: string) => {
    setItems(prev => prev && prev.map(x => (x.id === id ? { ...x, text } : x)))
    setSelected(prev => (prev && prev.id === id ? { ...prev, text } : prev))
    setDirty(true)
  }

  const toggleEditMode = async () => {
    if (editMode) {
      setEditMode(false)
      setSelectedIds(new Set())
      return
    }
    if (!rootHandle) {
      try {
        setRootHandle(await pickProjectRoot())
      } catch {
        showToast('未選擇資料夾，或選錯了（要選專案根目錄）')
        return
      }
    }
    setEditMode(true)
  }

  const saveChanges = async () => {
    if (!rootHandle || !items) return
    try {
      await writeIndex(rootHandle, items, draftEpisodes)
      setDirty(false)
      showToast('已儲存到本機 ✓ 記得跑刪圖同步發佈')
    } catch {
      showToast('儲存失敗，請確認資料夾權限')
    }
  }

  const toggleDraft = (n: number) => {
    setDraftEpisodes(prev => (prev.includes(n) ? prev.filter(x => x !== n) : [...prev, n]))
    setDirty(true)
  }

  /** 排序完全靠 t（時間戳）決定，這裡讓使用者直接調整順序，不用去猜正確的秒數。
   * 把 a 移到「緊鄰 b、朝 dir 方向」的位置：用 b 跟它另一側鄰居的中點算出 a 的新 t，
   * 而不是單純跟 b 互換數值，這樣就算兩者的 t 剛好相同（重複匯入很常見）也一定會
   * 真的移動、不會卡住。dir=-1 表示移到 b 前面，+1 表示移到 b 後面。
   * 拖曳（handleDrop）跟 ▲▼（moveItem）共用這個核心邏輯。 */
  const repositionNextTo = (aId: string, bId: string, dir: -1 | 1) => {
    const bIdx = filtered.findIndex(x => x.id === bId)
    const a = filtered.find(x => x.id === aId)
    const b = filtered[bIdx]
    if (!a || !b || a.id === b.id || a.ep !== b.ep) return
    const k = bIdx + dir
    const beyond = k >= 0 && k < filtered.length && filtered[k].ep === a.ep ? filtered[k].t : null
    const newT = Math.round((beyond !== null ? (b.t + beyond) / 2 : b.t + dir * 0.001) * 1000) / 1000
    setItems(prev => prev && prev.map(x => (x.id === a.id ? { ...x, t: newT } : x)))
    setDirty(true)
  }

  const moveItem = (id: string, dir: -1 | 1) => {
    const idx = filtered.findIndex(x => x.id === id)
    const neighbor = filtered[idx + dir]
    if (idx < 0 || !neighbor) return
    repositionNextTo(id, neighbor.id, dir)
  }

  const handleDrop = (aId: string, targetId: string) => {
    if (aId === targetId) return
    const fromIdx = filtered.findIndex(x => x.id === aId)
    const toIdx = filtered.findIndex(x => x.id === targetId)
    if (fromIdx < 0 || toIdx < 0) return
    repositionNextTo(aId, targetId, fromIdx < toIdx ? 1 : -1)
  }

  const toggleSelect = (id: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const deleteImage = async (item: Item) => {
    if (!rootHandle) return
    if (!window.confirm(`確定要刪除這張圖嗎？\n${item.text || '（無文字）'}\n此動作無法復原。`)) return
    try {
      await removeImageFile(rootHandle, item.img)
      await appendExcludeList(rootHandle, [
        { ep: item.ep, t: item.t, text: item.text.slice(0, 20), source: item.source },
      ])
      const next = (items ?? []).filter(x => x.id !== item.id)
      setItems(next)
      setSelected(prev => (prev && prev.id === item.id ? null : prev))
      await writeIndex(rootHandle, next, draftEpisodes)
      showToast('已刪除 ✓ 記得跑刪圖同步發佈')
    } catch {
      showToast('刪除失敗，請確認資料夾權限')
    }
  }

  const deleteSelected = async () => {
    if (!rootHandle || !items || selectedIds.size === 0) return
    const targets = items.filter(x => selectedIds.has(x.id))
    if (!window.confirm(`確定要刪除這 ${targets.length} 張圖嗎？此動作無法復原。`)) return
    try {
      for (const item of targets) await removeImageFile(rootHandle, item.img)
      await appendExcludeList(
        rootHandle,
        targets.map(item => ({ ep: item.ep, t: item.t, text: item.text.slice(0, 20), source: item.source })),
      )
      const next = items.filter(x => !selectedIds.has(x.id))
      setItems(next)
      setSelected(prev => (prev && selectedIds.has(prev.id) ? null : prev))
      setSelectedIds(new Set())
      await writeIndex(rootHandle, next, draftEpisodes)
      showToast(`已刪除 ${targets.length} 張 ✓ 記得跑刪圖同步發佈`)
    } catch {
      showToast('刪除失敗，請確認資料夾權限（可能部分已刪除，建議重新整理確認）')
    }
  }

  // 載入索引：本機一律看全部（含草稿集），正式網站自動濾掉草稿集
  useEffect(() => {
    fetch(`${BASE}index/yumemita.json`)
      .then(r => r.json())
      .then(d => {
        const draft: number[] = d.draftEpisodes ?? []
        setDraftEpisodes(draft)
        setItems(IS_LOCAL ? d.items : d.items.filter((i: Item) => !draft.includes(i.ep)))
      })
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

  // 拖曳排序時滾輪捲動整頁（有些截圖離正確位置很遠，拖曳中要能滾到很上面/很下面）
  useEffect(() => {
    if (!dragId) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      window.scrollBy({ top: e.deltaY, left: e.deltaX })
    }
    window.addEventListener('wheel', onWheel, { passive: false })
    return () => window.removeEventListener('wheel', onWheel)
  }, [dragId])

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
        <p className="mt-1 text-xs text-zinc-600">作者：千早愛音的暴躁柯基</p>
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
                {epLabel(n)}（{c}）
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
          {CAN_EDIT && (
            <button
              onClick={toggleEditMode}
              title="開啟後可直接編輯台詞、刪除圖片，會寫回本機檔案（選資料夾時請選專案根目錄）"
              className={`rounded-xl px-3 py-2.5 text-sm border transition-colors ${
                editMode
                  ? 'bg-pink-500/20 border-pink-400/50 text-pink-200'
                  : 'bg-white/5 border-white/10 hover:border-white/25'
              }`}
            >
              {editMode ? '編輯模式中' : '編輯模式'}
            </button>
          )}
          {editMode && dirty && (
            <button
              onClick={saveChanges}
              className="rounded-xl px-3 py-2.5 text-sm bg-emerald-500/20 border border-emerald-400/50 text-emerald-200 hover:bg-emerald-500/30 transition-colors"
            >
              儲存變更
            </button>
          )}
          {editMode && selectedIds.size > 0 && (
            <button
              onClick={deleteSelected}
              className="rounded-xl px-3 py-2.5 text-sm bg-red-500/20 border border-red-400/50 text-red-200 hover:bg-red-500/30 transition-colors"
            >
              刪除已選 {selectedIds.size} 張
            </button>
          )}
        </div>
        {editMode && (
          <div className="max-w-6xl mx-auto mt-2 flex flex-wrap items-center gap-1.5 text-xs">
            <span className="text-zinc-500">正式網站公開狀態：</span>
            {episodes
              .filter(([n]) => n !== -1 && n !== 1000)
              .map(([n]) => (
                <button
                  key={n}
                  onClick={() => toggleDraft(n)}
                  title="點一下切換這一集在正式網站上是否顯示（本機一律看得到）"
                  className={`px-2 py-1 rounded-md border transition-colors ${
                    draftEpisodes.includes(n)
                      ? 'bg-amber-500/15 border-amber-400/40 text-amber-300'
                      : 'bg-emerald-500/10 border-emerald-400/30 text-emerald-300'
                  }`}
                >
                  {epLabel(n)}
                  {draftEpisodes.includes(n) ? ' 草稿' : ' 公開'}
                </button>
              ))}
          </div>
        )}
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
          {filtered.slice(0, shown).map((i, idx) => {
            const canMoveUp = idx > 0 && filtered[idx - 1].ep === i.ep
            const canMoveDown = idx < filtered.length - 1 && filtered[idx + 1].ep === i.ep
            return (
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
              onDragOver={e => {
                if (editMode && dragId) e.preventDefault()
              }}
              onDragEnter={e => {
                if (editMode && dragId && dragId !== i.id) {
                  e.preventDefault()
                  setOverId(i.id)
                }
              }}
              onDrop={e => {
                if (!editMode || !dragId) return
                e.preventDefault()
                e.stopPropagation()
                handleDrop(dragId, i.id)
                setDragId(null)
                setOverId(null)
              }}
              onDragEnd={() => {
                setDragId(null)
                setOverId(null)
              }}
              className={`fade-in text-left rounded-xl overflow-hidden bg-white/5 border transition-all group cursor-pointer ${
                dragId === i.id
                  ? 'opacity-40'
                  : overId === i.id && dragId
                    ? 'border-cyan-300 ring-2 ring-cyan-300/50'
                    : selectedIds.has(i.id)
                      ? 'border-pink-400 ring-2 ring-pink-400/50'
                      : 'border-white/10 hover:border-pink-400/50 hover:-translate-y-0.5'
              }`}
            >
              <div className="relative">
                <img
                  src={BASE + i.img}
                  alt={i.text || '場景截圖'}
                  loading="lazy"
                  className="w-full aspect-video object-cover bg-black/40"
                />
                {editMode && (
                  <button
                    onClick={e => {
                      e.stopPropagation()
                      toggleSelect(i.id)
                    }}
                    title="選取（可批次刪除）"
                    className={`absolute top-1.5 left-1.5 size-6 rounded-md border flex items-center justify-center transition-colors ${
                      selectedIds.has(i.id)
                        ? 'bg-pink-500 border-pink-400'
                        : 'bg-black/50 border-white/40 hover:border-white/70'
                    }`}
                  >
                    {selectedIds.has(i.id) && (
                      <svg viewBox="0 0 20 20" width="14" height="14" fill="none" stroke="white" strokeWidth={2.4}>
                        <path d="M4 10.5l4 4 8-9" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    )}
                  </button>
                )}
                {editMode && (
                  <button
                    draggable
                    onDragStart={e => {
                      e.stopPropagation()
                      e.dataTransfer.effectAllowed = 'move'
                      setDragId(i.id)
                    }}
                    onClick={e => e.stopPropagation()}
                    title="拖曳調整順序"
                    className="absolute top-1.5 left-8 size-6 rounded-md border border-white/40 bg-black/50 hover:border-white/70 flex items-center justify-center cursor-grab active:cursor-grabbing"
                  >
                    <svg viewBox="0 0 20 20" width="12" height="12" fill="currentColor">
                      <circle cx="6" cy="5" r="1.4" />
                      <circle cx="6" cy="10" r="1.4" />
                      <circle cx="6" cy="15" r="1.4" />
                      <circle cx="13" cy="5" r="1.4" />
                      <circle cx="13" cy="10" r="1.4" />
                      <circle cx="13" cy="15" r="1.4" />
                    </svg>
                  </button>
                )}
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
                  {editMode && (
                    <button
                      onClick={e => {
                        e.stopPropagation()
                        deleteImage(i)
                      }}
                      title="刪除這張圖"
                      className="p-1 rounded-full hover:bg-red-500/40 transition-colors"
                    >
                      <svg viewBox="0 0 20 20" width="15" height="15" fill="none" stroke="currentColor" strokeWidth={1.6}>
                        <path d="M4 5.5h12M8 5.5V4h4v1.5M5.5 5.5l.7 10a1 1 0 001 .9h5.6a1 1 0 001-.9l.7-10" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    </button>
                  )}
                </div>
              </div>
              <div className="p-2.5">
                {editMode ? (
                  <textarea
                    value={i.text}
                    onChange={e => updateText(i.id, e.target.value)}
                    onClick={e => e.stopPropagation()}
                    placeholder="（無文字，可輸入台詞或描述）"
                    rows={2}
                    className="w-full resize-none rounded-lg bg-black/30 border border-pink-400/30 px-2 py-1 text-sm leading-snug outline-none focus:border-pink-400/70"
                  />
                ) : (
                  <p className={`text-sm leading-snug line-clamp-2 min-h-10 whitespace-pre-line ${i.text ? '' : 'text-zinc-500 italic'}`}>
                    {i.text || `（${i.tags[0] ?? '場景'}）`}
                  </p>
                )}
                <p className="mt-1.5 text-[11px] text-zinc-500 group-hover:text-zinc-400 flex items-center gap-1.5">
                  <span>{epLabel(i.ep)} · {fmtTime(i.t)}</span>
                  {editMode && (
                    <span className="ml-auto flex gap-0.5">
                      <button
                        onClick={e => {
                          e.stopPropagation()
                          moveItem(i.id, -1)
                        }}
                        disabled={!canMoveUp}
                        title="往前移（跟上一張互換順序）"
                        className="size-5 rounded flex items-center justify-center border border-white/10 hover:border-white/30 disabled:opacity-20 disabled:hover:border-white/10 transition-colors"
                      >
                        ▲
                      </button>
                      <button
                        onClick={e => {
                          e.stopPropagation()
                          moveItem(i.id, 1)
                        }}
                        disabled={!canMoveDown}
                        title="往後移（跟下一張互換順序）"
                        className="size-5 rounded flex items-center justify-center border border-white/10 hover:border-white/30 disabled:opacity-20 disabled:hover:border-white/10 transition-colors"
                      >
                        ▼
                      </button>
                    </span>
                  )}
                </p>
              </div>
            </div>
            )
          })}
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
                {editMode && (
                  <button
                    onClick={() => deleteImage(selected)}
                    title="刪除這張圖"
                    className="p-1.5 rounded-full hover:bg-red-500/40 transition-colors"
                  >
                    <svg viewBox="0 0 20 20" width="17" height="17" fill="none" stroke="currentColor" strokeWidth={1.6}>
                      <path d="M4 5.5h12M8 5.5V4h4v1.5M5.5 5.5l.7 10a1 1 0 001 .9h5.6a1 1 0 001-.9l.7-10" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </button>
                )}
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
              {editMode ? (
                <textarea
                  value={selected.text}
                  onChange={e => updateText(selected.id, e.target.value)}
                  placeholder="（無文字，可輸入台詞或描述）"
                  rows={2}
                  className="w-full resize-none rounded-lg bg-black/30 border border-pink-400/30 px-3 py-2 text-base outline-none focus:border-pink-400/70"
                />
              ) : (
                <p className="text-base whitespace-pre-line">{selected.text || `（${selected.tags[0] ?? '場景'}）`}</p>
              )}
              <p className="mt-1 text-xs text-zinc-500">
                {epLabel(selected.ep)} · {fmtTime(selected.t)}
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
