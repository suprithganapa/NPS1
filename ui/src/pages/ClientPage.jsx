import { useState, useEffect } from 'react'

const s = {
  page: { minHeight: '100vh', background: 'linear-gradient(135deg,#0d1117 0%,#161b22 100%)',
    display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '48px 24px' },
  card: { background: '#161b22', border: '1px solid #30363d', borderRadius: '12px',
    padding: '28px 32px', width: '100%', maxWidth: '580px' },
  title: { fontSize: '20px', fontWeight: 700, color: '#58a6ff', marginBottom: '6px',
    display: 'flex', alignItems: 'center', gap: '10px' },
  sub: { fontSize: '12px', color: '#8b949e', marginBottom: '22px' },
  empty: { textAlign: 'center', padding: '40px 0', color: '#8b949e', fontSize: '13px' },
  list: { display: 'flex', flexDirection: 'column', gap: '9px' },
  item: { display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    background: '#0d1117', border: '1px solid #21262d', borderRadius: '8px',
    padding: '13px 16px', cursor: 'pointer', transition: 'border-color .12s, background .12s' },
  itemHov: { borderColor: '#58a6ff', background: '#161b22' },
  itemName: { display: 'flex', alignItems: 'center', gap: '9px',
    fontSize: '13px', fontWeight: 600, color: '#e6edf3' },
  itemRight: { display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '3px' },
  playBtn: { fontSize: '11px', color: '#58a6ff', fontWeight: 700 },
  statusRow: { display: 'flex', alignItems: 'center', gap: '5px',
    fontSize: '11px', fontWeight: 600 },
  dot: { width: '6px', height: '6px', borderRadius: '50%' },
  refreshBtn: { background: 'none', border: '1px solid #30363d', color: '#8b949e',
    borderRadius: '6px', padding: '5px 12px', fontSize: '11px', cursor: 'pointer',
    marginBottom: '16px' },
  toast: { position: 'fixed', bottom: '24px', left: '50%', transform: 'translateX(-50%)',
    borderRadius: '8px', padding: '11px 22px', fontSize: '12px', fontWeight: 600,
    zIndex: 1000, maxWidth: '420px', textAlign: 'center' },
  toastOk:  { background: '#1a3a2a', border: '1px solid #3fb950', color: '#7ee787' },
  toastErr: { background: '#2a1a1a', border: '1px solid #f85149', color: '#f85149' },
}

function useServerStatus() {
  const [status, setStatus] = useState({ running: false, stem: null })
  useEffect(() => {
    const poll = () => fetch('/api/server-status').then(r => r.json()).then(setStatus).catch(() => {})
    poll()
    const iv = setInterval(poll, 2500)
    return () => clearInterval(iv)
  }, [])
  return status
}

export default function ClientPage() {
  const [videos, setVideos]   = useState([])
  const [loading, setLoading] = useState(true)
  const [hov, setHov]         = useState(null)
  // per-stem state: 'idle' | 'switching' | 'playing' | 'error'
  const [stemState, setStemState] = useState({})
  const [stemMsg, setStemMsg]     = useState({})
  const [toast, setToast]         = useState(null)
  const srvStatus = useServerStatus()

  function showToast(msg, ok = true) {
    setToast({ msg, ok })
    setTimeout(() => setToast(null), 5000)
  }

  async function loadVideos() {
    setLoading(true)
    try {
      const d = await fetch('/api/videos').then(r => r.json())
      setVideos(d.videos || [])
    } catch (_) { setVideos([]) }
    finally { setLoading(false) }
  }

  useEffect(() => { loadVideos() }, [])

  async function handlePlay(stem) {
    const cur = stemState[stem]
    if (cur === 'switching' || cur === 'playing') return

    setStemState(p => ({ ...p, [stem]: 'switching' }))
    setStemMsg(p => ({ ...p, [stem]: 'Starting server for this video…' }))

    try {
      const res = await fetch('/api/play', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stem }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || 'play failed')

      setStemState(p => ({ ...p, [stem]: 'playing' }))
      setStemMsg(p => ({ ...p, [stem]: 'ICMP knock sent — ffplay/VLC opening…' }))
      showToast(`Knock sent for "${stem}" — player should open shortly`)

      // Reset status after 20s
      setTimeout(() => {
        setStemState(p => ({ ...p, [stem]: 'idle' }))
        setStemMsg(p => ({ ...p, [stem]: '' }))
      }, 20000)
    } catch (err) {
      setStemState(p => ({ ...p, [stem]: 'error' }))
      setStemMsg(p => ({ ...p, [stem]: err.message }))
      showToast(err.message, false)
      setTimeout(() => setStemState(p => ({ ...p, [stem]: 'idle' })), 6000)
    }
  }

  function stateColor(st) {
    if (st === 'switching') return '#f0883e'
    if (st === 'playing')   return '#3fb950'
    if (st === 'error')     return '#f85149'
    return null
  }

  function stateLabel(st, msg) {
    if (st === 'switching') return '⏳ ' + (msg || 'Starting…')
    if (st === 'playing')   return '▶ ' + (msg || 'Playing…')
    if (st === 'error')     return '✗ ' + (msg || 'Error')
    return null
  }

  return (
    <div style={s.page}>
      <div style={s.card}>
        <div style={s.title}><span>📺</span> Client</div>
        <div style={s.sub}>
          Click any video — the server auto-switches to serve it, then sends the ICMP knock and opens the player.
        </div>

        {/* Server status bar */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '7px',
          fontSize: '11px', marginBottom: '14px', color: '#8b949e' }}>
          <span style={{ ...s.dot, background: srvStatus.running ? '#3fb950' : '#555',
            display: 'inline-block' }} />
          {srvStatus.running
            ? <span>Server running — serving <strong style={{ color: '#e6edf3' }}>{srvStatus.stem}</strong></span>
            : <span>Server not running — will auto-start on click</span>}
        </div>

        <button style={s.refreshBtn} onClick={loadVideos}>↺ Refresh list</button>

        {loading ? (
          <div style={s.empty}>Loading…</div>
        ) : videos.length === 0 ? (
          <div style={s.empty}>
            <div style={{ fontSize: '28px', marginBottom: '10px' }}>📭</div>
            No encrypted videos yet.
            <br /><span style={{ fontSize: '11px' }}>
              Upload and encrypt a video on <strong>/server</strong> first.
            </span>
          </div>
        ) : (
          <div style={s.list}>
            {videos.map(stem => {
              const st  = stemState[stem] || 'idle'
              const msg = stemMsg[stem] || ''
              const col = stateColor(st)
              const lbl = stateLabel(st, msg)
              const isActive = st === 'switching' || st === 'playing'
              const isServer = srvStatus.stem === stem && srvStatus.running

              return (
                <div key={stem}
                  style={{ ...s.item, ...(hov === stem && !isActive ? s.itemHov : {}),
                    cursor: isActive ? 'default' : 'pointer',
                    borderColor: isServer ? '#238636' : (hov === stem ? '#58a6ff' : '#21262d'),
                  }}
                  onClick={() => handlePlay(stem)}
                  onMouseEnter={() => setHov(stem)}
                  onMouseLeave={() => setHov(null)}
                >
                  <span style={s.itemName}>
                    <span style={{ fontSize: '16px' }}>🎬</span>
                    {stem}
                    {isServer && <span style={{ fontSize: '10px', color: '#3fb950',
                      background: '#1a3a2a', padding: '2px 7px', borderRadius: '10px' }}>
                      serving
                    </span>}
                  </span>

                  <div style={s.itemRight}>
                    {lbl
                      ? <span style={{ ...s.statusRow, color: col }}>
                          <span style={{ ...s.dot, background: col }} />
                          {lbl}
                        </span>
                      : <span style={s.playBtn}>▶ Play</span>
                    }
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {toast && (
        <div style={{ ...s.toast, ...(toast.ok ? s.toastOk : s.toastErr) }}>
          {toast.msg}
        </div>
      )}
    </div>
  )
}
