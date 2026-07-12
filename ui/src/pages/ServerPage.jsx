import { useState, useRef, useEffect } from 'react'

const s = {
  page: { minHeight: '100vh', background: 'linear-gradient(135deg,#0d1117 0%,#161b22 100%)',
    display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '48px 24px' },
  card: { background: '#161b22', border: '1px solid #30363d', borderRadius: '12px',
    padding: '28px 32px', width: '100%', maxWidth: '580px', marginBottom: '16px' },
  title: { fontSize: '20px', fontWeight: 700, color: '#58a6ff', marginBottom: '6px',
    display: 'flex', alignItems: 'center', gap: '10px' },
  sub: { fontSize: '12px', color: '#8b949e', marginBottom: '22px' },
  fileArea: { border: '2px dashed #30363d', borderRadius: '8px', padding: '26px',
    textAlign: 'center', cursor: 'pointer', transition: 'border-color .15s', marginBottom: '12px' },
  fileAreaHov: { borderColor: '#58a6ff' },
  fileText: { color: '#8b949e', fontSize: '13px' },
  fileName: { color: '#e6edf3', fontSize: '13px', fontWeight: 600, marginTop: '6px' },
  btn: { width: '100%', padding: '10px', borderRadius: '7px', border: 'none',
    fontSize: '13px', fontWeight: 600, marginBottom: '10px', cursor: 'pointer' },
  green: { background: '#238636', color: '#fff' },
  blue:  { background: '#1f6feb', color: '#fff' },
  red:   { background: '#b91c1c', color: '#fff' },
  off:   { opacity: .38, cursor: 'not-allowed' },
  divider: { height: '1px', background: '#21262d', margin: '16px 0' },
  stepRow: { display: 'flex', alignItems: 'center', gap: '9px', marginBottom: '13px' },
  stepNum: { display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
    width: '21px', height: '21px', borderRadius: '50%', background: '#58a6ff22',
    color: '#58a6ff', fontSize: '11px', fontWeight: 700, flexShrink: 0 },
  stepLabel: { fontSize: '13px', fontWeight: 600, color: '#e6edf3' },
  stepDone: { fontSize: '11px', fontWeight: 600, color: '#3fb950' },
  badge: { display: 'inline-flex', alignItems: 'center', gap: '5px',
    padding: '3px 9px', borderRadius: '20px', fontSize: '11px', fontWeight: 600 },
  dot: { width: '7px', height: '7px', borderRadius: '50%' },
  logBox: { background: '#010409', border: '1px solid #21262d', borderRadius: '6px',
    padding: '10px 12px', fontSize: '11px', color: '#7ee787', fontFamily: 'monospace',
    maxHeight: '200px', overflowY: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all' },
  errText: { color: '#f85149', fontSize: '11px', marginTop: '7px', fontFamily: 'monospace' },
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
        <div style={s.title}> Server</div>
        <div style={s.sub}>Encrypt a video and start the ICMP knock server</div>

        {/* Step 1: Upload & Encrypt */}
        <div style={s.stepRow}>
          <span style={s.stepNum}>1</span>
          <span style={s.stepLabel}>Upload &amp; Encrypt Video</span>
          {encDone && <span style={s.stepDone}>✓ {encDone.stem}</span>}
        </div>

        <div
          style={{ ...s.fileArea, ...(hover ? s.fileAreaHov : {}) }}
          onClick={() => fileRef.current.click()}
          onMouseEnter={() => setHover(true)}
          onMouseLeave={() => setHover(false)}
        >
          <div style={s.fileText}>{file ? '📹' : '📁 Click to select a video file'}</div>
          {file  && <div style={s.fileName}>{file.name}</div>}
          {!file && <div style={{ ...s.fileText, fontSize: '11px', marginTop: '4px' }}>MP4, AVI, MKV, MOV…</div>}
        </div>
        <input ref={fileRef} type="file" accept="video/*" onChange={handleFilePick} />

        <button style={{ ...s.btn, ...s.green, ...(!file || encBusy ? s.off : {}) }}
          onClick={handleEncrypt} disabled={!file || encBusy}>
          {encBusy ? '⏳ Encrypting + Syncing…' : '🔐 Encrypt Video'}
        </button>

        {encErr  && <div style={s.errText}>✗ {encErr}</div>}
        {encDone && (
          <div style={{ ...s.logBox, color: '#3fb950', maxHeight: '100px', marginTop: '4px' }}>
            {encDone.output}
          </div>
        )}

        <div style={s.divider} />

        {/* Step 2: Start Server */}
        <div style={s.stepRow}>
          <span style={s.stepNum}>2</span>
          <span style={s.stepLabel}>Start Server</span>
          <span style={{ ...s.badge, background: srvRunning ? '#1a3a2a' : '#1c1c2e' }}>
            <span style={{ ...s.dot, background: srvRunning ? '#3fb950' : '#555' }} />
            <span style={{ color: srvRunning ? '#3fb950' : '#8b949e' }}>
              {srvRunning ? `Running${srvStem ? ` — ${srvStem}` : ''}` : 'Stopped'}
            </span>
          </span>
        </div>

        {!srvRunning
          ? <button style={{ ...s.btn, ...s.blue, ...(!canStart || srvBusy ? s.off : {}) }}
              onClick={handleStartServer} disabled={!canStart || srvBusy}>
              {srvBusy ? '⏳ Starting…' : `▶ Start Server${encDone ? ` (${encDone.stem})` : ''}`}
            </button>
          : <button style={{ ...s.btn, ...s.red }} onClick={handleStop}>■ Stop Server</button>
        }

        {srvErr && <div style={s.errText}>✗ {srvErr}</div>}

        {log && (
          <>
            <div style={s.divider} />
            <div style={{ fontSize: '11px', color: '#8b949e', marginBottom: '4px' }}>Server output</div>
            <div ref={logRef} style={s.logBox}>{log}</div>
          </>
        )}
      </div>
    </div>
  )
}
