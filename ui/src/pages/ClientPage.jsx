import { useState, useEffect } from 'react'

const c = {
  bg: '#0B0909', surface: '#0F0D0D', inset: '#080606', row: '#0C0A0A',
  border: '#2E4540', divider: 'rgba(46,69,64,0.45)',
  teal: '#408175', tealText: '#79ADA2', lav: '#B5B9F0',
  text: '#E7E4E0', dim: '#8E938F', faint: '#5C605D',
}
const serif = "'Iowan Old Style', 'Palatino Linotype', Palatino, Georgia, serif"
const mono  = "ui-monospace, 'SF Mono', SFMono-Regular, Menlo, Consolas, monospace"
const sans  = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"

const Play = p => (
  <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor" {...p}><path d="M7 4.5v15l13-7.5z" /></svg>
)

const s = {
  page: { minHeight: '100vh', background: c.bg, color: c.text, fontFamily: sans,
    display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '64px 24px' },
  card: { background: c.surface, border: `1px solid ${c.border}`, borderRadius: '6px',
    padding: '34px 36px', width: '100%', maxWidth: '600px' },

  eyebrow: { fontFamily: mono, fontSize: '11px', letterSpacing: '0.2em', textTransform: 'uppercase',
    color: c.teal, marginBottom: '13px' },
  title: { fontFamily: serif, fontSize: '25px', fontWeight: 600, color: c.text,
    letterSpacing: '-0.01em', margin: 0 },
  sub: { fontSize: '13px', color: c.dim, marginTop: '9px', lineHeight: 1.55 },

  statusRow: { display: 'flex', alignItems: 'center', gap: '8px', marginTop: '20px',
    paddingTop: '16px', borderTop: `1px solid ${c.divider}`,
    fontFamily: mono, fontSize: '11px', letterSpacing: '0.05em' },
  dot: { width: '6px', height: '6px', borderRadius: '50%', flexShrink: 0 },

  toolbar: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', margin: '18px 0 14px' },
  count: { fontFamily: mono, fontSize: '11px', letterSpacing: '0.06em', color: c.faint, textTransform: 'uppercase' },
  refresh: { background: 'transparent', border: `1px solid ${c.border}`, color: c.dim,
    borderRadius: '5px', padding: '6px 13px', fontFamily: sans, fontSize: '11px', fontWeight: 600,
    letterSpacing: '0.06em', textTransform: 'uppercase', cursor: 'pointer' },

  empty: { textAlign: 'center', padding: '46px 24px', border: `1px solid ${c.divider}`,
    borderRadius: '6px', color: c.dim, fontSize: '13px', lineHeight: 1.6 },
  emptyHint: { color: c.faint, fontSize: '12px', marginTop: '8px', fontFamily: mono },

  list: { display: 'flex', flexDirection: 'column', gap: '9px' },
  item: { display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    background: c.row, border: `1px solid ${c.divider}`, borderRadius: '6px',
    padding: '14px 16px', cursor: 'pointer', transition: 'border-color .13s, background .13s' },
  itemName: { display: 'flex', alignItems: 'center', gap: '11px',
    fontFamily: mono, fontSize: '13px', color: c.text },
  serving: { fontFamily: mono, fontSize: '9.5px', letterSpacing: '0.1em', textTransform: 'uppercase',
    color: c.teal, border: `1px solid ${c.teal}`, borderRadius: '3px', padding: '2px 7px' },
  play: { display: 'inline-flex', alignItems: 'center', gap: '7px', color: c.teal,
    fontFamily: sans, fontSize: '11px', fontWeight: 600, letterSpacing: '0.08em', textTransform: 'uppercase' },
  status: { display: 'inline-flex', alignItems: 'center', gap: '7px',
    fontFamily: mono, fontSize: '11px', letterSpacing: '0.02em', textAlign: 'right' },

  toast: { position: 'fixed', bottom: '28px', left: '50%', transform: 'translateX(-50%)',
    background: c.surface, borderRadius: '6px', padding: '12px 20px', fontFamily: mono, fontSize: '12px',
    letterSpacing: '0.02em', zIndex: 1000, maxWidth: '440px', textAlign: 'center' },
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
    setStemMsg(p => ({ ...p, [stem]: 'Requesting access…' }))

    try {
      const res = await fetch('/api/play', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stem }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || 'play failed')

      setStemState(p => ({ ...p, [stem]: 'playing' }))
      setStemMsg(p => ({ ...p, [stem]: 'Knock sent — opening player' }))
      showToast(`Knock sent for ${stem} — player opening shortly`)

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
    if (st === 'switching') return c.teal
    if (st === 'playing')   return c.teal
    if (st === 'error')     return c.lav
    return null
  }

  function stateLabel(st, msg) {
    if (st === 'switching') return msg || 'Starting…'
    if (st === 'playing')   return msg || 'Playing'
    if (st === 'error')     return msg || 'Error'
    return null
  }

  return (
    <div style={s.page}>
      <div style={s.card}>
        <div style={s.eyebrow}>Client</div>
        <h1 style={s.title}>Library</h1>
        <div style={s.sub}>Authorized playback over an ICMP single-packet knock.</div>

        {/* Server status */}
        <div style={s.statusRow}>
          <span style={{ ...s.dot, background: srvStatus.running ? c.teal : c.faint }} />
          <span style={{ color: srvStatus.running ? c.teal : c.dim }}>
            {srvStatus.running
              ? `Server online `
              : 'Server offline'}
          </span>
        </div>

        <div style={s.toolbar}>
          <span style={s.count}>
            {loading ? 'Loading' : `${videos.length} ${videos.length === 1 ? 'title' : 'titles'}`}
          </span>
          <button style={s.refresh} onClick={loadVideos}>Refresh</button>
        </div>

        {loading ? (
          <div style={s.empty}>Loading…</div>
        ) : videos.length === 0 ? (
          <div style={s.empty}>
            No encrypted videos yet.
            <div style={s.emptyHint}>Encrypt one on /server to get started.</div>
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
              const border = isServer ? c.teal : (hov === stem && !isActive ? c.teal : c.divider)

              return (
                <div key={stem}
                  style={{ ...s.item,
                    borderColor: border,
                    background: hov === stem && !isActive ? c.surface : c.row,
                    cursor: isActive ? 'default' : 'pointer' }}
                  onClick={() => handlePlay(stem)}
                  onMouseEnter={() => setHov(stem)}
                  onMouseLeave={() => setHov(null)}
                >
                  <span style={s.itemName}>
                    {stem}
                    {isServer && <span style={s.serving}>serving</span>}
                  </span>

                  {lbl ? (
                    <span style={{ ...s.status, color: col }}>
                      <span style={{ ...s.dot, background: col }} />
                      {lbl}
                    </span>
                  ) : (
                    <span style={s.play}><Play /> Play</span>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      {toast && (
        <div style={{ ...s.toast,
          border: `1px solid ${toast.ok ? c.teal : c.lav}`,
          color: toast.ok ? c.tealText : c.lav }}>
          {toast.msg}
        </div>
      )}
    </div>
  )
}