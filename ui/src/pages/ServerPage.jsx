import { useState, useRef, useEffect } from 'react'

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
    maxWidth: '560px',
    marginBottom: '24px',
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
  label: {
    display: 'block',
    fontSize: '13px',
    color: '#8b949e',
    marginBottom: '8px',
    fontWeight: 500,
  },
  fileArea: {
    border: '2px dashed #30363d',
    borderRadius: '8px',
    padding: '32px',
    textAlign: 'center',
    cursor: 'pointer',
    transition: 'border-color 0.2s',
    marginBottom: '16px',
  },
  fileAreaHover: {
    borderColor: '#58a6ff',
  },
  fileAreaText: {
    color: '#8b949e',
    fontSize: '14px',
  },
  fileName: {
    color: '#e6edf3',
    fontSize: '14px',
    fontWeight: 600,
    marginTop: '8px',
  },
  btn: {
    width: '100%',
    padding: '12px',
    borderRadius: '8px',
    border: 'none',
    fontSize: '14px',
    fontWeight: 600,
    transition: 'opacity 0.2s',
    marginBottom: '12px',
  },
  btnPrimary: {
    background: '#238636',
    color: '#fff',
  },
  btnBlue: {
    background: '#1f6feb',
    color: '#fff',
  },
  btnDanger: {
    background: '#b91c1c',
    color: '#fff',
  },
  btnDisabled: {
    opacity: 0.4,
    cursor: 'not-allowed',
  },
  statusBadge: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: '6px',
    padding: '4px 12px',
    borderRadius: '20px',
    fontSize: '12px',
    fontWeight: 600,
    marginBottom: '16px',
  },
  dot: {
    width: '8px',
    height: '8px',
    borderRadius: '50%',
  },
  log: {
    background: '#010409',
    border: '1px solid #21262d',
    borderRadius: '6px',
    padding: '12px',
    fontSize: '12px',
    color: '#7ee787',
    fontFamily: 'monospace',
    maxHeight: '160px',
    overflowY: 'auto',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-all',
  },
  divider: {
    height: '1px',
    background: '#21262d',
    margin: '20px 0',
  },
  stepNum: {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    width: '22px',
    height: '22px',
    borderRadius: '50%',
    background: '#58a6ff22',
    color: '#58a6ff',
    fontSize: '12px',
    fontWeight: 700,
    flexShrink: 0,
  },
  stepRow: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    marginBottom: '16px',
  },
  stepLabel: {
    fontSize: '14px',
    fontWeight: 600,
    color: '#e6edf3',
  },
  stepDone: {
    color: '#3fb950',
    fontSize: '13px',
    fontWeight: 600,
  },
}

export default function ServerPage() {
  const [file, setFile] = useState(null)
  const [hover, setHover] = useState(false)
  const [encryptBusy, setEncryptBusy] = useState(false)
  const [encryptedStem, setEncryptedStem] = useState(null)
  const [serverRunning, setServerRunning] = useState(false)
  const [serverBusy, setServerBusy] = useState(false)
  const [log, setLog] = useState('')
  const fileRef = useRef(null)
  const logRef = useRef(null)

  useEffect(() => {
    fetch('/api/server-status')
      .then(r => r.json())
      .then(d => setServerRunning(d.running))
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [log])

  function appendLog(text) {
    setLog(prev => prev + text + '\n')
  }

  function handleFilePick(e) {
    const f = e.target.files[0]
    if (f) setFile(f)
  }

  async function handleEncrypt() {
    if (!file) return
    setEncryptBusy(true)
    setLog('')
    appendLog(`Encrypting: ${file.name} …`)
    const form = new FormData()
    form.append('video', file)
    try {
      const res = await fetch('/api/encrypt', { method: 'POST', body: form })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || 'encrypt failed')
      appendLog(data.output || '')
      appendLog(`✓ Encrypted. Stem: ${data.stem}`)
      setEncryptedStem(data.stem)
    } catch (err) {
      appendLog(`✗ Error: ${err.message}`)
    } finally {
      setEncryptBusy(false)
    }
  }

  async function handleStartServer() {
    if (!encryptedStem) return
    setServerBusy(true)
    appendLog('Generating configs and starting server …')
    try {
      const res = await fetch('/api/start-server', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stem: encryptedStem }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || 'start failed')
      appendLog(`✓ Server started (PID ${data.pid})`)
      setServerRunning(true)
    } catch (err) {
      appendLog(`✗ Error: ${err.message}`)
    } finally {
      setServerBusy(false)
    }
  }

  async function handleStopServer() {
    await fetch('/api/stop-server', { method: 'POST' })
    setServerRunning(false)
    appendLog('Server stopped.')
  }

  const runningColor = serverRunning ? '#3fb950' : '#8b949e'

  return (
    <div style={s.page}>
      <div style={s.card}>
        <div style={s.title}>
          <span>⚙️</span> Server
        </div>
        <div style={s.subtitle}>Encrypt a video and run the ICMP knock server</div>

        {/* Step 1 – Upload & Encrypt */}
        <div style={s.stepRow}>
          <span style={s.stepNum}>1</span>
          <span style={s.stepLabel}>Upload &amp; Encrypt Video</span>
          {encryptedStem && <span style={s.stepDone}>✓ {encryptedStem}</span>}
        </div>

        <div
          style={{ ...s.fileArea, ...(hover ? s.fileAreaHover : {}) }}
          onClick={() => fileRef.current.click()}
          onMouseEnter={() => setHover(true)}
          onMouseLeave={() => setHover(false)}
        >
          <div style={s.fileAreaText}>
            {file ? '📹' : '📁'} {file ? '' : 'Click to select a video file'}
          </div>
          {file && <div style={s.fileName}>{file.name}</div>}
          {!file && (
            <div style={{ ...s.fileAreaText, fontSize: '11px', marginTop: '6px' }}>
              MP4, AVI, MKV, MOV…
            </div>
          )}
        </div>
        <input ref={fileRef} type="file" accept="video/*" onChange={handleFilePick} />

        <button
          style={{
            ...s.btn,
            ...s.btnPrimary,
            ...((!file || encryptBusy) ? s.btnDisabled : {}),
          }}
          onClick={handleEncrypt}
          disabled={!file || encryptBusy}
        >
          {encryptBusy ? '⏳ Encrypting…' : '🔐 Encrypt Video'}
        </button>

        <div style={s.divider} />

        {/* Step 2 – Start Server */}
        <div style={s.stepRow}>
          <span style={s.stepNum}>2</span>
          <span style={s.stepLabel}>Start Server</span>
          <span style={{ ...s.statusBadge, background: serverRunning ? '#1a3a2a' : '#1c1c2e' }}>
            <span style={{ ...s.dot, background: runningColor }} />
            <span style={{ color: runningColor }}>{serverRunning ? 'Running' : 'Stopped'}</span>
          </span>
        </div>

        {!serverRunning ? (
          <button
            style={{
              ...s.btn,
              ...s.btnBlue,
              ...(!encryptedStem || serverBusy ? s.btnDisabled : {}),
            }}
            onClick={handleStartServer}
            disabled={!encryptedStem || serverBusy}
          >
            {serverBusy ? '⏳ Starting…' : '▶ Start Server'}
          </button>
        ) : (
          <button style={{ ...s.btn, ...s.btnDanger }} onClick={handleStopServer}>
            ■ Stop Server
          </button>
        )}

        {log && (
          <>
            <div style={s.divider} />
            <div ref={logRef} style={s.log}>{log}</div>
          </>
        )}
      </div>
    </div>
  )
}
