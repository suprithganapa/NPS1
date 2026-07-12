import { useState, useEffect } from 'react'

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
  empty: {
    textAlign: 'center',
    padding: '48px 0',
    color: '#8b949e',
    fontSize: '14px',
  },
  list: {
    display: 'flex',
    flexDirection: 'column',
    gap: '10px',
  },
  item: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    background: '#0d1117',
    border: '1px solid #21262d',
    borderRadius: '8px',
    padding: '14px 16px',
    cursor: 'pointer',
    transition: 'border-color 0.15s, background 0.15s',
  },
  itemHover: {
    borderColor: '#58a6ff',
    background: '#161b22',
  },
  itemName: {
    fontSize: '14px',
    fontWeight: 600,
    color: '#e6edf3',
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
  },
  itemAction: {
    fontSize: '12px',
    color: '#58a6ff',
    fontWeight: 600,
  },
  toast: {
    position: 'fixed',
    bottom: '24px',
    left: '50%',
    transform: 'translateX(-50%)',
    background: '#1a3a2a',
    border: '1px solid #3fb950',
    color: '#7ee787',
    borderRadius: '8px',
    padding: '12px 24px',
    fontSize: '13px',
    fontWeight: 600,
    zIndex: 1000,
    animation: 'fadeIn 0.2s ease',
  },
  toastErr: {
    background: '#2a1a1a',
    borderColor: '#f85149',
    color: '#f85149',
  },
  refreshBtn: {
    background: 'none',
    border: '1px solid #30363d',
    color: '#8b949e',
    borderRadius: '6px',
    padding: '6px 14px',
    fontSize: '12px',
    cursor: 'pointer',
    marginBottom: '20px',
  },
  playingBadge: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: '6px',
    padding: '3px 10px',
    borderRadius: '20px',
    background: '#1a3a2a',
    color: '#3fb950',
    fontSize: '11px',
    fontWeight: 700,
  },
}

export default function ClientPage() {
  const [videos, setVideos] = useState([])
  const [loading, setLoading] = useState(true)
  const [hoverId, setHoverId] = useState(null)
  const [playing, setPlaying] = useState(null)
  const [toast, setToast] = useState(null)

  function showToast(msg, isErr = false) {
    setToast({ msg, isErr })
    setTimeout(() => setToast(null), 4000)
  }

  async function loadVideos() {
    setLoading(true)
    try {
      const res = await fetch('/api/videos')
      const data = await res.json()
      setVideos(data.videos || [])
    } catch {
      setVideos([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadVideos() }, [])

  async function handlePlay(stem) {
    if (playing === stem) return
    setPlaying(stem)
    try {
      const res = await fetch('/api/play', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stem }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || 'play failed')
      showToast(`Launching ${stem} — sending ICMP knock and starting VLC…`)
    } catch (err) {
      showToast(`Error: ${err.message}`, true)
    } finally {
      setTimeout(() => setPlaying(null), 15000)
    }
  }

  return (
    <div style={s.page}>
      <div style={s.card}>
        <div style={s.title}>
          <span>📺</span> Client
        </div>
        <div style={s.subtitle}>
          Click a video to authenticate via ICMP knock and play in VLC
        </div>

        <button style={s.refreshBtn} onClick={loadVideos}>↺ Refresh</button>

        {loading ? (
          <div style={s.empty}>Loading…</div>
        ) : videos.length === 0 ? (
          <div style={s.empty}>
            <div style={{ fontSize: '32px', marginBottom: '12px' }}>📭</div>
            No encrypted videos found.
            <br />
            <span style={{ fontSize: '12px' }}>
              Upload and encrypt a video on the <strong>/server</strong> page first.
            </span>
          </div>
        ) : (
          <div style={s.list}>
            {videos.map(stem => (
              <div
                key={stem}
                style={{
                  ...s.item,
                  ...(hoverId === stem ? s.itemHover : {}),
                  cursor: playing === stem ? 'not-allowed' : 'pointer',
                  opacity: playing && playing !== stem ? 0.5 : 1,
                }}
                onClick={() => handlePlay(stem)}
                onMouseEnter={() => setHoverId(stem)}
                onMouseLeave={() => setHoverId(null)}
              >
                <span style={s.itemName}>
                  <span style={{ fontSize: '18px' }}>🎬</span>
                  {stem}
                </span>
                {playing === stem ? (
                  <span style={s.playingBadge}>⏳ Knocking…</span>
                ) : (
                  <span style={s.itemAction}>▶ Play</span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {toast && (
        <div style={{ ...s.toast, ...(toast.isErr ? s.toastErr : {}) }}>
          {toast.msg}
        </div>
      )}
    </div>
  )
}
