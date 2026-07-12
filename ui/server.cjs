const express = require('express')
const multer = require('multer')
const { spawn } = require('child_process')
const path = require('path')
const fs = require('fs')
const os = require('os')
const yaml = require('js-yaml')

const app = express()
const PORT = 3001
const ROOT = path.resolve(__dirname, '..')
const WRAPPER_DIR = __dirname
const GENERATED_DIR = path.join(ROOT, 'config', 'generated')

app.use(express.json())

const UPLOAD_TMP = path.join(ROOT, 'uploads_tmp')
fs.mkdirSync(UPLOAD_TMP, { recursive: true })
const upload = multer({ dest: UPLOAD_TMP })

// Running server process + streaming log
let serverProc = null
let serverLog = ''
let currentStem = null
const serverLogListeners = new Set()

function broadcastLog(chunk) {
  serverLog += chunk
  if (serverLog.length > 80000) serverLog = serverLog.slice(-60000)
  for (const send of serverLogListeners) {
    try { send(chunk) } catch (_) {}
  }
}

function getLocalIP() {
  for (const ifaces of Object.values(os.networkInterfaces())) {
    for (const iface of ifaces) {
      if (iface.family === 'IPv4' && !iface.internal) return iface.address
    }
  }
  return '127.0.0.1'
}

function pythonEnv() {
  return {
    ...process.env,
    PYTHONIOENCODING: 'utf-8',
    PYTHONUTF8: '1',
    PYTHONLEGACYWINDOWSSTDIO: '0',
  }
}

function runPython(args, opts = {}) {
  return new Promise((resolve, reject) => {
    const proc = spawn('python', args, { cwd: ROOT, env: pythonEnv(), ...opts })
    let out = '', err = ''
    if (proc.stdout) proc.stdout.on('data', d => { out += d })
    if (proc.stderr) proc.stderr.on('data', d => { err += d })
    proc.on('close', code => code === 0 ? resolve({ stdout: out, stderr: err })
      : reject(new Error((err + out).trim() || `exit ${code}`)))
    proc.on('error', reject)
  })
}

// Read TOTP from generated server.yaml if it exists
function readCurrentTotp() {
  const p = path.join(GENERATED_DIR, 'server.yaml')
  try {
    const cfg = yaml.load(fs.readFileSync(p, 'utf8'))
    return cfg?.auth?.totp_secret || 'JBSWY3DPEHPK3PXP'
  } catch (_) {
    return 'JBSWY3DPEHPK3PXP'
  }
}

// Generate (or regenerate) lab configs with given TOTP
function generateConfigs(totp) {
  const ip = getLocalIP()
  const args = [
    'scripts/generate_lab_configs.py',
    '--server-ip', ip,
    '--knocker-ip', ip,
  ]
  broadcastLog(`Generating lab configs (IP=${ip}, TOTP=...${totp.slice(-4)})\n`)
  return runPython(args).then(async r => {
    // Patch TOTP into both generated files
    for (const name of ['server.yaml', 'knocker.yaml']) {
      const p = path.join(GENERATED_DIR, name)
      if (!fs.existsSync(p)) continue
      const cfg = yaml.load(fs.readFileSync(p, 'utf8'))
      cfg.auth = cfg.auth || {}
      cfg.auth.totp_secret = totp
      fs.writeFileSync(p, yaml.dump(cfg), 'utf8')
    }
    return r
  })
}

async function ensureConfigs(totp) {
  const s = path.join(GENERATED_DIR, 'server.yaml')
  const k = path.join(GENERATED_DIR, 'knocker.yaml')
  if (!fs.existsSync(s) || !fs.existsSync(k)) {
    await generateConfigs(totp || 'JBSWY3DPEHPK3PXP')
  } else if (totp) {
    // If totp provided, make sure it matches both files
    for (const name of ['server.yaml', 'knocker.yaml']) {
      const p = path.join(GENERATED_DIR, name)
      const cfg = yaml.load(fs.readFileSync(p, 'utf8'))
      if (cfg?.auth?.totp_secret !== totp) {
        cfg.auth = cfg.auth || {}
        cfg.auth.totp_secret = totp
        fs.writeFileSync(p, yaml.dump(cfg), 'utf8')
      }
    }
  }
}

// Kill running server and wait
async function killServer() {
  if (serverProc && !serverProc.killed) {
    serverProc.kill('SIGTERM')
    await new Promise(r => setTimeout(r, 1000))
    serverProc = null
  }
}

// Start lab server for a given stem
async function startServer(stem, totp) {
  await killServer()
  serverLog = ''
  currentStem = stem

  await ensureConfigs(totp)
  await runPython(['scripts/sync_lab_video.py', '--stem', stem])

  serverProc = spawn('python', [
    path.join(WRAPPER_DIR, 'server_wrapper.py'),
    '--config', 'config/generated/server.yaml',
  ], { cwd: ROOT, env: pythonEnv() })

  serverProc.stdout.on('data', d => broadcastLog(d.toString()))
  serverProc.stderr.on('data', d => broadcastLog(d.toString()))
  serverProc.on('close', code => {
    broadcastLog(`\n[server exited: code ${code}]\n`)
    if (serverProc && serverProc.exitCode !== null) serverProc = null
  })
  serverProc.on('error', err => {
    broadcastLog(`\n[server error: ${err.message}]\n`)
    serverProc = null
  })

  // Wait up to 5s for server ready
  await new Promise(resolve => {
    const deadline = setTimeout(resolve, 5000)
    const poll = setInterval(() => {
      if (serverLog.includes('Video server ready') || !serverProc) {
        clearTimeout(deadline); clearInterval(poll); resolve()
      }
    }, 200)
  })

  if (!serverProc) throw new Error('Server exited immediately — check server output panel')
}

// ------------------------------------------------------------------ API

// List available encrypted videos
app.get('/api/videos', (req, res) => {
  const dir = path.join(ROOT, 'artifacts')
  if (!fs.existsSync(dir)) return res.json({ videos: [] })
  const stems = fs.readdirSync(dir)
    .filter(f => f.endsWith('.manifest.json'))
    .map(f => f.replace('.manifest.json', ''))
  res.json({ videos: stems })
})

// Config info (which files, current TOTP)
app.get('/api/config-info', (req, res) => {
  const totp = readCurrentTotp()
  const serverYaml = path.join(GENERATED_DIR, 'server.yaml')
  const knockerYaml = path.join(GENERATED_DIR, 'knocker.yaml')
  res.json({
    serverYaml: path.relative(ROOT, serverYaml).replace(/\\/g, '/'),
    knockerYaml: path.relative(ROOT, knockerYaml).replace(/\\/g, '/'),
    totp,
    configsExist: fs.existsSync(serverYaml) && fs.existsSync(knockerYaml),
    currentStem,
    serverRunning: serverProc !== null && !serverProc.killed,
  })
})

// Update TOTP (writes to both generated yamls)
app.post('/api/set-totp', async (req, res) => {
  const { totp } = req.body || {}
  if (!totp || totp.length < 8) return res.status(400).json({ error: 'TOTP secret too short' })
  try {
    await ensureConfigs(totp)
    res.json({ ok: true, totp })
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

// Upload + encrypt + sync (two scripts run in sequence)
app.post('/api/encrypt', upload.single('video'), async (req, res) => {
  if (!req.file) return res.status(400).json({ error: 'No file uploaded' })
  const origName = req.file.originalname
  const stem = path.basename(origName, path.extname(origName))
  const destPath = path.join(UPLOAD_TMP, origName)
  fs.renameSync(req.file.path, destPath)

  let output = ''
  try {
    output += `=== Step 1: Encrypting ${origName} ===\n`
    const enc = await runPython(['scripts/encrypt_video.py', destPath])
    output += enc.stdout + enc.stderr

    output += `\n=== Step 2: Syncing configs to stem "${stem}" ===\n`
    const totp = readCurrentTotp()
    await ensureConfigs(totp)
    const sync = await runPython(['scripts/sync_lab_video.py', '--stem', stem])
    output += sync.stdout + sync.stderr
    output += `\nDone. Video "${stem}" is ready to serve.\n`

    try { fs.unlinkSync(destPath) } catch (_) {}
    res.json({ ok: true, stem, output })
  } catch (err) {
    try { fs.unlinkSync(destPath) } catch (_) {}
    res.status(500).json({ error: err.message, output })
  }
})

// Start server manually (from server page)
app.post('/api/start-server', async (req, res) => {
  const { stem, totp } = req.body || {}
  if (!stem) return res.status(400).json({ error: 'stem required' })
  try {
    await startServer(stem, totp || readCurrentTotp())
    res.json({ ok: true, pid: serverProc?.pid, stem })
  } catch (err) {
    res.status(500).json({ error: err.message, output: serverLog })
  }
})

// Server status
app.get('/api/server-status', (req, res) => {
  res.json({
    running: serverProc !== null && !serverProc.killed,
    stem: currentStem,
  })
})

// Stop server
app.post('/api/stop-server', async (req, res) => {
  await killServer()
  currentStem = null
  res.json({ ok: true })
})

// SSE: real-time server output
app.get('/api/server-log', (req, res) => {
  res.setHeader('Content-Type', 'text/event-stream')
  res.setHeader('Cache-Control', 'no-cache')
  res.setHeader('Connection', 'keep-alive')
  res.flushHeaders()
  if (serverLog) res.write(`data: ${JSON.stringify(serverLog)}\n\n`)
  const send = chunk => res.write(`data: ${JSON.stringify(chunk)}\n\n`)
  serverLogListeners.add(send)
  req.on('close', () => serverLogListeners.delete(send))
})

// PLAY: stop server → switch stem → restart server → launch receiver
app.post('/api/play', async (req, res) => {
  const { stem } = req.body || {}
  if (!stem) return res.status(400).json({ error: 'stem required' })

  // Verify the video files exist before doing anything
  const manifest = path.join(ROOT, 'artifacts', `${stem}.manifest.json`)
  const segkeys = path.join(ROOT, 'server_secrets', `${stem}.segkeys`)
  if (!fs.existsSync(manifest)) return res.status(404).json({ error: `manifest not found: ${stem}.manifest.json` })
  if (!fs.existsSync(segkeys)) return res.status(404).json({ error: `segment keys not found: ${stem}.segkeys` })

  try {
    const totp = readCurrentTotp()

    // If server is already serving this stem, skip restart
    if (currentStem !== stem || !serverProc || serverProc.killed) {
      broadcastLog(`\n=== Switching to video: ${stem} ===\n`)
      await startServer(stem, totp)
    }

    // Launch receiver (opens ffplay/vlc automatically)
    const receiverProc = spawn('python', [
      path.join(WRAPPER_DIR, 'receiver_wrapper.py'),
      '--config', 'config/generated/knocker.yaml',
    ], {
      cwd: ROOT,
      env: pythonEnv(),
      detached: true,
      stdio: 'ignore',
    })
    receiverProc.unref()

    res.json({ ok: true, stem, serverPid: serverProc?.pid, receiverPid: receiverProc.pid })
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

// Serve Vite build in production
const distPath = path.join(__dirname, 'dist')
if (fs.existsSync(distPath)) {
  app.use(express.static(distPath))
  app.get('*', (req, res) => res.sendFile(path.join(distPath, 'index.html')))
}

app.listen(PORT, () => console.log(`NPS backend on http://localhost:${PORT}`))
