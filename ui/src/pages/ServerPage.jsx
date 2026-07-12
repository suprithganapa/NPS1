import { useState, useRef, useEffect, useCallback } from 'react'

const s = {
  page: {
    minHeight: '100vh',
    background: 'linear-gradient(135deg, #0d1117 0%, #161b22 100%)',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    padding: '48px 24px',
  },
  card: {
    background: '#161b22',
    border: '1px solid #30363d',
    borderRadius: '12px',
    padding: '32px',
    width: '100%',
    maxWidth: '580px',
    marginBottom: '20px',
  },
  title: {
    fontSize: '22px',
    fontWeight: 700,
    color: '#58a6ff',
    marginBottom: '8px',
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
  },
  subtitle: {
    fontSize: '13px',
    color: '#8b949e',
    marginBottom: '28px',
  },
  fileArea: {
    border: '2px dashed #30363d',
    borderRadius: '8px',
    padding: '28px',
    textAlign: 'center',
    cursor: 'pointer',
    transition: 'border-color 0.2s',
    marginBottom: '14px',
  },
  fileAreaActive: { borderColor: '#58a6ff' },
  fileAreaText: { color: '#8b949e', fontSize: '14px' },
  fileName: { color: '#e6edf3', fontSize: '14px', fontWeight: 600, marginTop: '6px' },
  btn: {
    width: '100%',
    padding: '11px',
    borderRadius: '8px',
    border: 'none',
    fontSize: '14px',
    fontWeight: 600,
    marginBottom: '10px',
    transition: 'opacity 0.15s',
  },
  btnGreen:  { background: '#238636', color: '#fff' },
  btnBlue:   { background: '#1f6feb', color: '#fff' },
  btnRed:    { background: '#b91c1c', color: '#fff' },
  btnOff:    { opacity: 0.38, cursor: 'not-allowed' },
  divider:   { height: '1px', background: '#21262d', margin: '18px 0' },
  stepRow: {
    display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '14px',
  },
  stepNum: {
    display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
    width: '22px', height: '22px', borderRadius: '50%',
    background: '#58a6ff22', color: '#58a6ff', fontSize: '12px', fontWeight: 700,
  },
  stepLabel: { fontSize: '14px', fontWeight: 600, color: '#e6edf3' },
  stepDone:  { fontSize: '12px', fontWeight: 600, color: '#3fb950' },
  badge: {
    display: 'inline-flex', alignItems: 'center', gap: '5px',
    padding: '3px 10px', borderRadius: '20px', fontSize: '12px', fontWeight: 600,
  },
  dot: { width: '7px', height: '7px', borderRadius: '50%' },
  logBox: {
    background: '#010409',
    border: '1px solid #21262d',
    borderRadius: '6px',
    padding: '10px 12px',
    fontSize: '11px',
    color: '#7ee787',
    fontFamily: 'monospace',
    maxHeight: '200px',
    overflowY: 'auto',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-all',
    marginTop: '12px',
  },
  errText: { color: '#f85149', fontSize: '12px', marginTop: '8px', fontFamily: 'monospace' },
}

export default function ServerPage() {
  const [file, setFile] = useState(null)
  const [hover, setHover] = useState(false)
  const [encBusy, setEncBusy] = useState(false)
  const [encDone, setEncDone] = useState(null)   // { stem, output }
  const [encError, setEncError] = useState(null)
  const [srvRunning, setSrvRunning] = useState(false)
  const [srvBusy, setSrvBusy] = useState(false)
  const [srvError, setSrvError] = useState(null)
  const [log, setLog] = useState('')
  const fileRef = useRef(null)
  const logRef = useRef(null)
  const evtSourceRef = useRef(null)

  // Poll server status on mount
  useEffect(() => {
    fetch('/api/server-status').then(r => r.json()).then(d => setSrvRunning(d.running)).catch(() => {})
  }, [])

  // Subscribe to server SSE log
  const subscribeLog = useCallback(() => {
    if (evtSourceRef.current) evtSourceRef.current.close()
    const es = new EventSource('/api/server-log')
    es.onmessage = e => {
      try {
        const chunk = JSON.parse(e.data)
        setLog(prev => (prev + chunk).slice(-60000))
      } catch (_) {}
    }
    evtSourceRef.current = es
  }, [])

  useEffect(() => {
    subscribeLog()
    return () => evtSourceRef.current && evtSourceRef.current.close()
  }, [subscribeLog])

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [log])

  function handleFilePick(e) {
    const f = e.target.files[0]
    if (f) { setFile(f); setEncDone(null); setEncError(null) }
  }

  async function handleEncrypt() {
    if (!file) return
    setEncBusy(true)
    setEncError(null)
    setEncDone(null)
    const form = new FormData()
    form.append('video', file)
    try {
      const res = await fetch('/api/encrypt', { method: 'POST', body: form })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || 'encrypt failed')
      setEncDone({ stem: data.stem, output: data.output })
    } catch (err) {
      setEncError(err.message)
    } finally {
      setEncBusy(false)
    }
  }

  async function handleStartServer() {
    if (!encDone) return
    setSrvBusy(true)
    setSrvError(null)
    setLog('')
    try {
      const res = await fetch('/api/start-server', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stem: encDone.stem }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || 'start failed')
      setSrvRunning(true)
    } catch (err) {
      setSrvError(err.message)
    } finally {
      setSrvBusy(false)
    }
  }

  async function handleStop() {
    await fetch('/api/stop-server', { method: 'POST' })
    setSrvRunning(false)
  }

  return (
    <div style={s.page}>
      <div style={s.card}>
        <div style={s.title}><span>⚙️</span> Server</div>
        <div style={s.subtitle}>Encrypt a video file, then start the ICMP knock server</div>

        {/* Step 1 */}
        <div style={s.stepRow}>
          <span style={s.stepNum}>1</span>
          <span style={s.stepLabel}>Upload &amp; Encrypt Video</span>
          {encDone && <span style={s.stepDone}>✓ {encDone.stem}</span>}
        </div>

        <div
          style={{ ...s.fileArea, ...(hover ? s.fileAreaActive : {}) }}
          onClick={() => fileRef.current.click()}
          onMouseEnter={() => setHover(true)}
          onMouseLeave={() => setHover(false)}
        >
          <div style={s.fileAreaText}>{file ? '📹' : '📁 Click to select a video file'}</div>
          {file && <div style={s.fileName}>{file.name}</div>}
          {!file && <div style={{ ...s.fileAreaText, fontSize: '11px', marginTop: '4px' }}>MP4, AVI, MKV…</div>}
        </div>
        <input ref={fileRef} type="file" accept="video/*" onChange={handleFilePick} />

        <button
          style={{ ...s.btn, ...s.btnGreen, ...(!file || encBusy ? s.btnOff : {}) }}
          onClick={handleEncrypt}
          disabled={!file || encBusy}
        >
          {encBusy ? '⏳ Encrypting + Syncing…' : '🔐 Encrypt Video'}
        </button>

        {encError && <div style={s.errText}>✗ {encError}</div>}

        {encDone && (
          <div style={{ ...s.logBox, marginTop: '8px', color: '#3fb950' }}>
            {encDone.output}
          </div>
        )}

        <div style={s.divider} />

        {/* Step 2 */}
        <div style={s.stepRow}>
          <span style={s.stepNum}>2</span>
          <span style={s.stepLabel}>Start Server</span>
          <span style={{ ...s.badge, background: srvRunning ? '#1a3a2a' : '#1c1c2e' }}>
            <span style={{ ...s.dot, background: srvRunning ? '#3fb950' : '#555' }} />
            <span style={{ color: srvRunning ? '#3fb950' : '#8b949e' }}>
              {srvRunning ? 'Running' : 'Stopped'}
            </span>
          </span>
        </div>

        {!srvRunning
          ? <button
              style={{ ...s.btn, ...s.btnBlue, ...(!encDone || srvBusy ? s.btnOff : {}) }}
              onClick={handleStartServer}
              disabled={!encDone || srvBusy}
            >
              {srvBusy ? '⏳ Starting…' : '▶ Start Server'}
            </button>
          : <button style={{ ...s.btn, ...s.btnRed }} onClick={handleStop}>■ Stop Server</button>
        }

        {srvError && <div style={s.errText}>✗ {srvError}</div>}

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
