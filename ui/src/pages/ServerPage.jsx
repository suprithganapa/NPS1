import { useState, useRef, useEffect } from 'react'

const c = {
  bg: '#0B0909', surface: '#0F0D0D', inset: '#080606',
  border: '#2E4540', divider: 'rgba(46,69,64,0.45)',
  teal: '#408175', tealText: '#79ADA2', lav: '#B5B9F0',
  text: '#E7E4E0', dim: '#8E938F', faint: '#5C605D',
}
const serif = "'Iowan Old Style', 'Palatino Linotype', Palatino, Georgia, serif"
const mono  = "ui-monospace, 'SF Mono', SFMono-Regular, Menlo, Consolas, monospace"
const sans  = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"

const Ico = {
  upload: p => (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M12 15V4" /><path d="M8 8l4-4 4 4" />
      <path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" />
    </svg>
  ),
  lock: p => (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <rect x="4" y="10.5" width="16" height="9.5" rx="1.5" /><path d="M8 10.5V7a4 4 0 0 1 8 0v3.5" />
    </svg>
  ),
  play: p => (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" {...p}><path d="M7 4.5v15l13-7.5z" /></svg>
  ),
  stop: p => (
    <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor" {...p}><rect x="5" y="5" width="14" height="14" rx="1.5" /></svg>
  ),
  check: p => (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" {...p}><path d="M5 12.5l4.5 4.5L19 6.5" /></svg>
  ),
}

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
  rule: { height: '1px', background: c.divider, margin: '26px 0' },

  stepRow: { display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '16px' },
  stepNum: { display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
    width: '22px', height: '22px', borderRadius: '3px', border: `1px solid ${c.border}`,
    color: c.dim, fontFamily: mono, fontSize: '11px', flexShrink: 0 },
  stepLabel: { fontSize: '14px', fontWeight: 600, color: c.text, letterSpacing: '0.005em' },
  stepDone: { display: 'inline-flex', alignItems: 'center', gap: '6px', marginLeft: 'auto',
    fontFamily: mono, fontSize: '11.5px', color: c.teal },

  drop: { border: `1px solid ${c.border}`, borderRadius: '6px', padding: '30px 20px',
    textAlign: 'center', cursor: 'pointer', transition: 'border-color .15s, background .15s',
    marginBottom: '14px', background: c.bg },
  dropHov: { borderColor: c.teal, background: c.surface },
  dropIcon: { color: c.dim, marginBottom: '11px', display: 'flex', justifyContent: 'center' },
  dropText: { color: c.dim, fontSize: '13px' },
  dropHint: { color: c.faint, fontSize: '11px', marginTop: '6px', fontFamily: mono, letterSpacing: '0.04em' },
  fileName: { color: c.text, fontSize: '13px', fontFamily: mono, wordBreak: 'break-all' },

  btn: { width: '100%', padding: '11px 14px', borderRadius: '5px', fontFamily: sans,
    fontSize: '11.5px', fontWeight: 600, letterSpacing: '0.09em', textTransform: 'uppercase',
    cursor: 'pointer', transition: 'opacity .15s, background .15s',
    display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: '8px', marginBottom: '10px' },
  btnPrimary: { background: c.teal, color: c.bg, border: `1px solid ${c.teal}` },
  btnOutline: { background: 'transparent', color: c.tealText, border: `1px solid ${c.border}` },
  btnGhost: { background: 'transparent', color: c.dim, border: `1px solid ${c.border}` },
  btnOff: { opacity: 0.4, cursor: 'not-allowed' },

  badge: { display: 'inline-flex', alignItems: 'center', gap: '7px', marginLeft: 'auto',
    fontFamily: mono, fontSize: '11px', letterSpacing: '0.05em' },
  dot: { width: '6px', height: '6px', borderRadius: '50%', flexShrink: 0 },

  logLabel: { fontFamily: mono, fontSize: '10.5px', letterSpacing: '0.14em', textTransform: 'uppercase',
    color: c.faint, marginBottom: '8px' },
  readout: { background: c.inset, border: `1px solid ${c.border}`, borderRadius: '5px',
    padding: '11px 13px', fontSize: '11.5px', color: c.tealText, fontFamily: mono,
    lineHeight: 1.65, whiteSpace: 'pre-wrap', wordBreak: 'break-all', marginTop: '2px' },
  logBox: { background: c.inset, border: `1px solid ${c.border}`, borderRadius: '5px',
    padding: '12px 13px', fontSize: '11.5px', color: c.tealText, fontFamily: mono, lineHeight: 1.65,
    maxHeight: '220px', overflowY: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all' },
  err: { display: 'flex', gap: '8px', color: c.lav, fontFamily: mono, fontSize: '11.5px',
    marginTop: '10px', lineHeight: 1.5 },
}

export default function ServerPage() {
  const [file, setFile]       = useState(null)
  const [hover, setHover]     = useState(false)
  const [encBusy, setEncBusy] = useState(false)
  const [encDone, setEncDone] = useState(null)
  const [encErr,  setEncErr]  = useState(null)
  const [srvRunning, setSrv]  = useState(false)
  const [srvStem, setStem]    = useState(null)
  const [srvBusy, setSrvBusy] = useState(false)
  const [srvErr, setSrvErr]   = useState(null)
  const [log, setLog]         = useState('')
  const fileRef = useRef(null)
  const logRef  = useRef(null)
  const esRef   = useRef(null)

  useEffect(() => {
    fetch('/api/server-status').then(r => r.json()).then(d => {
      setSrv(d.running); setStem(d.stem)
    }).catch(() => {})
  }, [])

  useEffect(() => {
    if (esRef.current) esRef.current.close()
    const es = new EventSource('/api/server-log')
    es.onmessage = e => {
      try { setLog(prev => (prev + JSON.parse(e.data)).slice(-80000)) } catch (_) {}
    }
    esRef.current = es
    return () => es.close()
  }, [])

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [log])

  function handleFilePick(e) {
    const f = e.target.files[0]
    if (f) { setFile(f); setEncDone(null); setEncErr(null) }
  }

  async function handleEncrypt() {
    if (!file) return
    setEncBusy(true); setEncErr(null); setEncDone(null)
    const form = new FormData()
    form.append('video', file)
    try {
      const res  = await fetch('/api/encrypt', { method: 'POST', body: form })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error)
      setEncDone(data)
    } catch (err) { setEncErr(err.message) }
    finally { setEncBusy(false) }
  }

  async function handleStartServer() {
    const stem = encDone?.stem || srvStem
    if (!stem) return
    setSrvBusy(true); setSrvErr(null); setLog('')
    try {
      const res  = await fetch('/api/start-server', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stem }) })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error)
      setSrv(true); setStem(stem)
    } catch (err) { setSrvErr(err.message) }
    finally { setSrvBusy(false) }
  }

  async function handleStop() {
    await fetch('/api/stop-server', { method: 'POST' })
    setSrv(false); setStem(null)
  }

  const canStart = !!(encDone?.stem || srvStem)

  return (
    <div style={s.page}>
      <div style={s.card}>
        <div style={s.eyebrow}>Server</div>
        <h1 style={s.title}>Encrypt &amp; publish</h1>
        <div style={s.sub}>Encrypt a video and bring the ICMP single-packet knock server online.</div>

        <div style={s.rule} />

        {/* Step 1 — Encrypt */}
        <div style={s.stepRow}>
          <span style={s.stepNum}>1</span>
          <span style={s.stepLabel}>Upload &amp; encrypt video</span>
          {encDone && (
            <span style={s.stepDone}><Ico.check /> {encDone.stem}</span>
          )}
        </div>

        <div
          style={{ ...s.drop, ...(hover ? s.dropHov : {}) }}
          onClick={() => fileRef.current.click()}
          onMouseEnter={() => setHover(true)}
          onMouseLeave={() => setHover(false)}
        >
          <div style={s.dropIcon}><Ico.upload /></div>
          {file ? (
            <>
              <div style={s.fileName}>{file.name}</div>
              <div style={s.dropHint}>Click to choose a different file</div>
            </>
          ) : (
            <>
              <div style={s.dropText}>Choose a video file</div>
              <div style={s.dropHint}>MP4 · AVI · MKV · MOV</div>
            </>
          )}
        </div>
        <input ref={fileRef} type="file" accept="video/*" onChange={handleFilePick} style={{ display: 'none' }} />

        <button style={{ ...s.btn, ...s.btnPrimary, ...(!file || encBusy ? s.btnOff : {}) }}
          onClick={handleEncrypt} disabled={!file || encBusy}>
          <Ico.lock /> {encBusy ? 'Encrypting…' : 'Encrypt video'}
        </button>

        {encErr && <div style={s.err}><span>—</span><span>{encErr}</span></div>}
        {encDone && (
          <>
            <div style={{ ...s.logLabel, marginTop: '14px' }}>Result</div>
            <div style={s.readout}>{encDone.output}</div>
          </>
        )}

        <div style={s.rule} />

        {/* Step 2 — Serve */}
        <div style={s.stepRow}>
          <span style={s.stepNum}>2</span>
          <span style={s.stepLabel}>Start server</span>
          <span style={s.badge}>
            <span style={{ ...s.dot, background: srvRunning ? c.teal : c.faint }} />
            <span style={{ color: srvRunning ? c.teal : c.dim }}>
              {srvRunning ? `Online${srvStem ? ` · ${srvStem}` : ''}` : 'Offline'}
            </span>
          </span>
        </div>

        {!srvRunning ? (
          <button style={{ ...s.btn, ...s.btnOutline, ...(!canStart || srvBusy ? s.btnOff : {}) }}
            onClick={handleStartServer} disabled={!canStart || srvBusy}>
            <Ico.play /> {srvBusy ? 'Starting…' : 'Start server'}
          </button>
        ) : (
          <button style={{ ...s.btn, ...s.btnGhost }} onClick={handleStop}>
            <Ico.stop /> Stop server
          </button>
        )}

        {srvErr && <div style={s.err}><span>—</span><span>{srvErr}</span></div>}

        {log && (
          <>
            <div style={s.rule} />
            <div style={s.logLabel}>Server output</div>
            <div ref={logRef} style={s.logBox}>{log}</div>
          </>
        )}
      </div>
    </div>
  )
}