const express = require('express')
const multer = require('multer')
const { spawn } = require('child_process')
const path = require('path')
const fs = require('fs')

const app = express()
const PORT = 3001
const ROOT = path.resolve(__dirname, '..')

app.use(express.json())

// -- multer: save uploads to a temp dir inside the project
const upload = multer({ dest: path.join(ROOT, 'uploads_tmp') })

// Track running server process
let serverProc = null

// ------------------------------------------------------------------ helpers

function runPython(args, opts = {}) {
  return new Promise((resolve, reject) => {
    const proc = spawn('python', args, {
      cwd: ROOT,
      env: { ...process.env, PYTHONPATH: path.join(ROOT, 'src') },
      ...opts,
    })
    let stdout = ''
    let stderr = ''
    proc.stdout && proc.stdout.on('data', d => { stdout += d })
    proc.stderr && proc.stderr.on('data', d => { stderr += d })
    proc.on('close', code => {
      if (code === 0) resolve({ stdout, stderr })
      else reject(new Error(stderr || stdout || `exit ${code}`))
    })
    proc.on('error', reject)
  })
}

function ensureGeneratedConfigs() {
  const serverYaml = path.join(ROOT, 'config', 'generated', 'server.yaml')
  const knockerYaml = path.join(ROOT, 'config', 'generated', 'knocker.yaml')
  if (fs.existsSync(serverYaml) && fs.existsSync(knockerYaml)) {
    return Promise.resolve({ stdout: 'configs exist', stderr: '' })
  }
  return runPython(['scripts/generate_lab_configs.py', '--auto'])
}

// ------------------------------------------------------------------ routes

// List available encrypted videos (manifest stems)
app.get('/api/videos', (req, res) => {
  const artifactsDir = path.join(ROOT, 'artifacts')
  if (!fs.existsSync(artifactsDir)) {
    return res.json({ videos: [] })
  }
  const files = fs.readdirSync(artifactsDir)
  const stems = files
    .filter(f => f.endsWith('.manifest.json'))
    .map(f => f.replace('.manifest.json', ''))
  res.json({ videos: stems })
})

// Upload + encrypt a video
app.post('/api/encrypt', upload.single('video'), async (req, res) => {
  if (!req.file) return res.status(400).json({ error: 'No file uploaded' })

  // Move to a named path so the stem is preserved
  const origName = req.file.originalname
  const stem = path.basename(origName, path.extname(origName))
  const destPath = path.join(ROOT, 'uploads_tmp', origName)
  fs.renameSync(req.file.path, destPath)

  try {
    const result = await runPython(['scripts/encrypt_video.py', destPath])
    fs.unlinkSync(destPath)
    res.json({ ok: true, stem, output: result.stdout + result.stderr })
  } catch (err) {
    try { fs.unlinkSync(destPath) } catch (_) {}
    res.status(500).json({ error: err.message })
  }
})

// Start the lab server for a given video stem
app.post('/api/start-server', async (req, res) => {
  const { stem } = req.body
  if (!stem) return res.status(400).json({ error: 'stem required' })

  // Kill existing server if running
  if (serverProc && !serverProc.killed) {
    serverProc.kill('SIGTERM')
    serverProc = null
    await new Promise(r => setTimeout(r, 1000))
  }

  try {
    // Ensure generated configs exist
    await ensureGeneratedConfigs()
    // Sync video stem into configs
    await runPython(['scripts/sync_lab_video.py', '--stem', stem])

    // Start server as a background process (don't await)
    serverProc = spawn('python', ['scripts/run_lab_server.py', '--config', 'config/generated/server.yaml'], {
      cwd: ROOT,
      env: { ...process.env, PYTHONPATH: path.join(ROOT, 'src') },
      detached: false,
    })

    serverProc.on('close', code => {
      console.log(`[server] process exited with code ${code}`)
      serverProc = null
    })

    // Give it 2 seconds to start
    await new Promise(r => setTimeout(r, 2000))

    res.json({ ok: true, pid: serverProc ? serverProc.pid : null })
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

// Server status
app.get('/api/server-status', (req, res) => {
  res.json({ running: serverProc !== null && !serverProc.killed })
})

// Stop server
app.post('/api/stop-server', (req, res) => {
  if (serverProc && !serverProc.killed) {
    serverProc.kill('SIGTERM')
    serverProc = null
  }
  res.json({ ok: true })
})

// Play a video (runs client receiver)
app.post('/api/play', async (req, res) => {
  const { stem } = req.body
  if (!stem) return res.status(400).json({ error: 'stem required' })

  try {
    // Ensure configs exist
    await ensureGeneratedConfigs()
    // Sync knocker config to this stem
    await runPython(['scripts/sync_lab_video.py', '--stem', stem])

    // Spawn receiver as detached background process — it opens VLC on its own
    const receiverProc = spawn(
      'python',
      ['scripts/video_receiver.py', '--config', 'config/generated/knocker.yaml'],
      {
        cwd: ROOT,
        env: { ...process.env, PYTHONPATH: path.join(ROOT, 'src') },
        detached: true,
        stdio: 'ignore',
      }
    )
    receiverProc.unref()

    res.json({ ok: true, pid: receiverProc.pid })
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
