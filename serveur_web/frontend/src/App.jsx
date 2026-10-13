import { useEffect, useState, useRef } from 'react'
import {
  computeSisVector,
  signHessIBS,
  verifyIncomingBlock,
  deriveStreamerKey,
  uint8ArrayToBase64,
  base64ToUint8Array,
  DEFAULT_PARAM_A,
} from './cryptoClient.js'

const API_URL = import.meta.env.VITE_API_URL || ''
const WS_URL = API_URL
  ? API_URL.replace(/^http/, 'ws')
  : `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.hostname}:8000`

async function apiRequest(path, options = {}) {
  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map((item) => item.msg).filter(Boolean).join(' ')
      : data.detail
    throw new Error(detail || 'Une erreur est survenue.')
  }
  return data
}

function Logo() {
  return (
    <a className="logo" href="#top" aria-label="Pledge accueil">
      <img src="/logo.png" alt="Pledge Stream Beyond" style={{height: '35px', objectFit: 'contain'}} />
    </a>
  )
}

function PlayIcon() {
  return <span className="play-icon" aria-hidden="true">▶</span>
}

function AuthDialog({ mode, setMode, onSuccess, onClose }) {
  const isRegister = mode === 'register'
  const [form, setForm] = useState({ email: '', password: '', role: 'viewer' })
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  function updateField(event) {
    setForm((current) => ({ ...current, [event.target.name]: event.target.value }))
  }

  async function handleSubmit(event) {
    event.preventDefault()
    setError('')
    setLoading(true)
    try {
      const payload = isRegister ? form : { email: form.email, password: form.password }
      let data = await apiRequest(isRegister ? '/auth/register' : '/auth/login', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      if (isRegister) {
        data = await apiRequest('/auth/login', {
          method: 'POST',
          body: JSON.stringify({ email: form.email, password: form.password }),
        })
      }
      onSuccess(data.user || form)
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="auth-dialog" role="dialog" aria-modal="true" aria-labelledby="auth-title">
        <button className="close-button" type="button" onClick={onClose} aria-label="Fermer">×</button>
        <span className="eyebrow">{isRegister ? 'Rejoindre Pledge' : 'Bon retour'}</span>
        <h2 id="auth-title">{isRegister ? 'Créez votre compte' : 'Se connecter'}</h2>
        <p>{isRegister ? 'Lancez votre expérience de streaming sans limites.' : 'Retrouvez votre espace Pledge et vos contenus.'}</p>
        <form onSubmit={handleSubmit}>
          <label>Adresse email<input name="email" type="email" value={form.email} onChange={updateField} placeholder="vous@exemple.com" autoComplete="email" required /></label>
          <label>Mot de passe<input name="password" type="password" value={form.password} onChange={updateField} placeholder="••••••••" minLength="6" autoComplete={isRegister ? 'new-password' : 'current-password'} required /></label>
          {isRegister && <label>Type de compte<select name="role" value={form.role} onChange={updateField}><option value="viewer">Viewer</option><option value="streamer">Streamer</option></select></label>}
          <div className="form-error" role="alert">{error}</div>
          <button className="button button-primary button-wide" type="submit" disabled={loading}>{loading ? 'Connexion...' : isRegister ? 'Créer mon compte' : 'Se connecter'}</button>
        </form>
        <button className="text-button" type="button" onClick={() => setMode(isRegister ? 'login' : 'register')}>{isRegister ? 'J’ai déjà un compte' : 'Créer un compte'}</button>
      </section>
    </div>
  )
}

function ActiveStreamsList({ onSelectStream }) {
  const [streams, setStreams] = useState([])
  
  useEffect(() => {
    function fetchStreams() {
      apiRequest('/api/streams/active')
        .then(data => {
          if (data && Array.isArray(data.streams)) setStreams(data.streams)
        })
        .catch(() => {})
    }
    fetchStreams()
    const interval = setInterval(fetchStreams, 3000)
    return () => clearInterval(interval)
  }, [])

  if (streams.length === 0) return <p style={{color: '#888'}}>Aucun flux en direct actuellement.</p>

  return (
    <div style={{display: 'flex', gap: '1rem', flexWrap: 'wrap'}}>
      {streams.map(s => (
        <button key={s} className="button button-quiet" onClick={() => onSelectStream(s)}>
          <span style={{color: '#70e8b3', marginRight: '8px'}}>●</span> {s}
        </button>
      ))}
    </div>
  )
}

function Home({ openAuth }) {
  return (
    <section className="hero" id="top" style={{display: 'flex', flexDirection: 'column', justifyContent: 'center', textAlign: 'center', minHeight: '75vh'}}>
      <div className="hero-copy" style={{maxWidth: '800px', margin: '0 auto'}}>
        <span className="eyebrow">Play + Edge</span>
        <h1>Streaming Edge<br /><em>Nouvelle génération.</em></h1>
        <p>Un réseau décentralisé. Une sécurité cryptographique. Une expérience ultra-fluide.</p>
        <div className="hero-actions" style={{justifyContent: 'center', marginTop: '40px'}}>
          <button className="button button-primary" onClick={() => openAuth('register')}>Rejoindre le réseau <span>→</span></button>
          <button className="button button-quiet" onClick={() => openAuth('login')}>Se connecter</button>
        </div>
        <div style={{marginTop: '60px'}}>
          <h3 style={{marginBottom: '1rem', color: '#8899b8', fontSize: '12px', textTransform: 'uppercase', letterSpacing: '2px'}}>En direct sur Pledge</h3>
          <div style={{display: 'flex', justifyContent: 'center'}}>
            <ActiveStreamsList onSelectStream={(s) => openAuth('login')} />
          </div>
        </div>
      </div>
    </section>
  )
}

function StreamerInterface({ user }) {
  const [streaming, setStreaming] = useState(false)
  const [chunksSent, setChunksSent] = useState(0)
  const [edgeNode, setEdgeNode] = useState(null)
  const [ws, setWs] = useState(null)
  const [mediaRecorder, setMediaRecorder] = useState(null)
  const blockIndexRef = useRef(0)
  const heartbeatTimerRef = useRef(null)
  const videoRef = useRef()
  const [streamerKey] = useState(() => deriveStreamerKey(user.email))

  async function startStream() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true })
      if (videoRef.current) videoRef.current.srcObject = stream

      // 1. Plan de Signalisation : Allocation du nœud Fog via le backend central
      const alloc = await apiRequest('/api/stream/allocate', {
        method: 'POST',
        body: JSON.stringify({ session_id: user.email }),
      })
      setEdgeNode(alloc.edge_node_id)

      // 2. Plan de Données Décentralisé : Connexion WebSocket DIRECTE au nœud Fog (Phase 4)
      const edgeHost = (alloc.edge_host === '127.0.0.1' || alloc.edge_host === 'localhost')
        ? window.location.hostname
        : alloc.edge_host
      const directUploadUrl = `${alloc.scheme || 'ws'}://${edgeHost}:${alloc.edge_port}/upload/${encodeURIComponent(user.email)}`
      console.log(`[Streamer] Connexion directe Edge P2P : ${directUploadUrl}`)

      const socket = new WebSocket(directUploadUrl)
      blockIndexRef.current = 0

      socket.onopen = () => {
        setStreaming(true)
        setChunksSent(0)
        const mime = MediaRecorder.isTypeSupported('video/webm; codecs=vp8,opus') ? 'video/webm; codecs=vp8,opus' : 'video/webm'
        const recorderOptions = { mimeType: mime }
        try {
          recorderOptions.videoKeyFrameIntervalDuration = 1000
        } catch (_) {}
        const recorder = new MediaRecorder(stream, recorderOptions)

        recorder.ondataavailable = async (e) => {
          if (e.data.size > 0 && socket.readyState === WebSocket.OPEN) {
            const buffer = await e.data.arrayBuffer()
            const chunkBytes = new Uint8Array(buffer)
            const payloadB64 = uint8ArrayToBase64(chunkBytes)
            const currentIdx = blockIndexRef.current
            blockIndexRef.current += 1

            // Signature réelle côté diffuseur Ow (Phase 5 - Section 3.4.3)
            const paramA = alloc.paramA || DEFAULT_PARAM_A
            const vColumn = computeSisVector(chunkBytes, paramA)
            const signature = signHessIBS(vColumn, streamerKey.secretKey, paramA)

            // Paquet complet conforme à l'article scientifique
            const packet = {
              block_index: currentIdx,
              payload_b64: payloadB64,
              paramA: paramA,
              V_column: vColumn,
              signature: signature,
              signer_id: user.email,
              zone_pk: streamerKey.zonePk,
              is_delegated: false,
            }

            socket.send(JSON.stringify(packet))
            setChunksSent(c => c + 1)
          }
        }

        recorder.start(600)
        setMediaRecorder(recorder)

        // Heartbeat périodique vers le backend pour maintenir le statut en direct
        heartbeatTimerRef.current = setInterval(() => {
          apiRequest(`/api/stream/heartbeat/${encodeURIComponent(user.email)}`, { method: 'POST' }).catch(() => {})
        }, 8000)
      }

      socket.onclose = (e) => {
        console.log('Connexion WSS fermée', e)
        setStreaming(false)
        if (heartbeatTimerRef.current) clearInterval(heartbeatTimerRef.current)
        if (e.code !== 1000 && e.code !== 1005) {
          alert(`La diffusion s'est arrêtée : ${e.reason || 'Erreur réseau'}`)
        }
        if (videoRef.current && videoRef.current.srcObject) {
          videoRef.current.srcObject.getTracks().forEach(t => t.stop())
          videoRef.current.srcObject = null
        }
      }

      setWs(socket)
    } catch (e) {
      console.error('Erreur démarrage stream:', e)
      alert("Erreur d'accès à la caméra ou connexion Edge")
    }
  }

  function stopStream() {
    if (heartbeatTimerRef.current) clearInterval(heartbeatTimerRef.current)
    if (mediaRecorder && mediaRecorder.state !== 'inactive') mediaRecorder.stop()
    if (ws) ws.close()
    setStreaming(false)
    apiRequest(`/api/stream/stop/${encodeURIComponent(user.email)}`, { method: 'POST' }).catch(() => {})
    if (videoRef.current && videoRef.current.srcObject) {
      videoRef.current.srcObject.getTracks().forEach(t => t.stop())
      videoRef.current.srcObject = null
    }
  }

  return (
    <article className="dashboard-card stream-card">
      <div className="card-heading">
        <div><span className="eyebrow">Studio Edge (Owner Ow)</span><h2>Votre diffusion</h2></div>
        <div style={{display: 'flex', gap: '8px', alignItems: 'center'}}>
          {streaming && edgeNode && (
            <span className="connection-badge" style={{background: '#1a2638', color: '#70e8b3'}}>
              Fog: {edgeNode}
            </span>
          )}
          <span className="connection-badge">
            <i style={{background: streaming ? '#22c55e' : '#ef4444'}} />
            {streaming ? `En direct (${chunksSent} blocs signés)` : 'Hors ligne'}
          </span>
        </div>
      </div>
      <video ref={videoRef} autoPlay muted style={{width: '100%', background: '#000', borderRadius: '12px', minHeight: '200px'}} />
      {streaming && (
        <div style={{marginTop: '0.6rem', display: 'flex', flexDirection: 'column', gap: '6px'}}>
          <div style={{padding: '6px 10px', borderRadius: '6px', background: 'rgba(34, 197, 94, 0.1)', border: '1px solid rgba(34, 197, 94, 0.3)', fontSize: '11px', color: '#4ade80', display: 'flex', alignItems: 'center', gap: '8px'}}>
            <span>🔒</span>
            <span>Cryptographie active : Blocs vérifiables signés avec Hess-IBS (Clé secrète <em>S_w</em> liée à {user.email})</span>
          </div>
          <div style={{padding: '6px 10px', borderRadius: '6px', background: 'rgba(56, 189, 248, 0.1)', border: '1px solid rgba(56, 189, 248, 0.3)', fontSize: '11px', color: '#38bdf8', display: 'flex', alignItems: 'center', gap: '8px'}}>
            <span>⚡</span>
            <span>Délégation Fog active : Le nœud <strong>{edgeNode || 'Edge'}</strong> peut transcoder légitimement via clé de délégation <em>DK_d</em></span>
          </div>
        </div>
      )}
      <div style={{marginTop: '1rem'}}>
        {streaming ? (
          <button className="button button-quiet" onClick={stopStream}>Arrêter la diffusion</button>
        ) : (
          <button className="button button-primary" onClick={startStream}>Démarrer la caméra</button>
        )}
      </div>
    </article>
  )
}

function ViewerInterface({ user }) {
  const [streamId, setStreamId] = useState('')
  const [watching, setWatching] = useState(false)
  const [watchMode, setWatchMode] = useState('live')
  const [quality, setQuality] = useState('1080p')
  const [chunksReceived, setChunksReceived] = useState(0)
  const [connectedNode, setConnectedNode] = useState(null)
  const videoRef = useRef()
  const [ws, setWs] = useState(null)

  // États pour le Tableau de Bord d'Intégrité en Direct (Phase 5)
  const [auditStatus, setAuditStatus] = useState('idle') // 'idle' | 'intact' | 'delegated' | 'tampered'
  const [auditMetrics, setAuditMetrics] = useState({
    sisTimeMs: 0,
    sigTimeMs: 0,
    totalTimeMs: 0,
    verifiedCount: 0,
    tamperedCount: 0,
    lastDetail: '',
  })
  const [simulateTamper, setSimulateTamper] = useState(false)
  const simulateTamperRef = useRef(false)
  const [showInspector, setShowInspector] = useState(false)
  const [lastPacketData, setLastPacketData] = useState(null)

  function toggleTamperSimulation() {
    const nextVal = !simulateTamper
    setSimulateTamper(nextVal)
    simulateTamperRef.current = nextVal
  }

  async function startWatching(idToWatch, mode = watchMode, q = quality) {
    const targetId = idToWatch || streamId
    if (!targetId) return

    if (ws) {
      try {
        ws.close()
      } catch (_) {}
      setWs(null)
    }

    setStreamId(targetId)
    setWatchMode(mode)
    setQuality(q)
    setWatching(true)
    setChunksReceived(0)
    setAuditStatus('idle')
    setAuditMetrics({
      sisTimeMs: 0,
      sigTimeMs: 0,
      totalTimeMs: 0,
      verifiedCount: 0,
      tamperedCount: 0,
      lastDetail: '',
    })

    try {
      // 1. Plan d'Annuaire : Découverte du nœud hôte via le backend central
      const discovery = await apiRequest(`/api/stream/discover/${encodeURIComponent(targetId)}?mode=${mode}&quality=${q}`)
      setConnectedNode(discovery.edge_node_id)

      // Clé publique de zone par défaut
      const defaultZonePk = deriveStreamerKey(targetId).zonePk

      // 2. Plan de Données Décentralisé : Connexion directe au nœud Edge (Phase 4)
      const edgeHost = (discovery.edge_host === '127.0.0.1' || discovery.edge_host === 'localhost')
        ? window.location.hostname
        : discovery.edge_host
      const directDownloadUrl = `${discovery.scheme || 'ws'}://${edgeHost}:${discovery.edge_port}/download/${encodeURIComponent(targetId)}?mode=${mode}&quality=${q}`
      console.log(`[Viewer] Visionnage direct Edge P2P (Qualité: ${q}) : ${directDownloadUrl}`)

      const socket = new WebSocket(directDownloadUrl)
      socket.binaryType = "arraybuffer"

      const mediaSource = new MediaSource()
      setTimeout(() => {
        if (videoRef.current) {
          videoRef.current.src = URL.createObjectURL(mediaSource)
        }
      }, 50)

      mediaSource.addEventListener('sourceopen', () => {
        const mime = MediaRecorder.isTypeSupported('video/webm; codecs=vp8,opus') ? 'video/webm; codecs=vp8,opus' : 'video/webm'
        let sourceBuffer
        try {
          sourceBuffer = mediaSource.addSourceBuffer(mime)
        } catch (err) {
          console.error("Erreur addSourceBuffer:", err)
          return
        }

        const queue = []

        function processQueue() {
          if (queue.length > 0 && sourceBuffer && !sourceBuffer.updating) {
            const next = queue.shift()
            try {
              sourceBuffer.appendBuffer(next)
            } catch (e) {
              console.warn("Erreur buffer append:", e)
            }
          }
        }

        socket.onmessage = async (event) => {
          let chunk = null
          let packet = null

          // Support des paquets Phase 4/5 (format JSON avec métadonnées) et binaires bruts
          if (typeof event.data === 'string') {
            try {
              packet = JSON.parse(event.data)
              if (packet && packet.payload_b64) {
                chunk = base64ToUint8Array(packet.payload_b64)
              }
            } catch (_) {}
          } else if (event.data instanceof ArrayBuffer) {
            try {
              const text = new TextDecoder().decode(event.data)
              if (text.trim().startsWith('{')) {
                packet = JSON.parse(text)
                if (packet && packet.payload_b64) {
                  chunk = base64ToUint8Array(packet.payload_b64)
                }
              }
            } catch (_) {}

            if (!chunk) {
              chunk = new Uint8Array(event.data)
            }
          }

          if (!chunk) return

          // Simulation d'une altération de trame (Attaque en transit) pour démonstration
          if (simulateTamperRef.current) {
            chunk[0] ^= 0x55 // Altération d'octets
          }

          // VÉRIFICATION D'INTÉGRITÉ CÔTÉ SPECTATEUR (Audit Public sans TPA - Phase 5)
          if (packet) {
            setLastPacketData(packet)
            const audit = verifyIncomingBlock(chunk, packet, defaultZonePk)

            if (!audit.isValid) {
              // ÉCHEC DE LA PREUVE : corruption ou altération détectée !
              console.warn(`[Audit Public] ALERTE : ${audit.detail}`)
              setAuditStatus('tampered')
              setAuditMetrics(m => ({
                ...m,
                tamperedCount: m.tamperedCount + 1,
                sisTimeMs: audit.sisTimeMs,
                sigTimeMs: audit.sigTimeMs,
                totalTimeMs: audit.totalTimeMs,
                lastDetail: audit.detail,
              }))

              // Coupure immédiate de la lecture vidéo
              if (videoRef.current) {
                videoRef.current.pause()
              }
              return // Bloquer l'injection du fragment corrompu dans le buffer vidéo
            } else {
              // PREUVE RÉUSSIE : fragment vérifié mathématiquement sans TPA
              setAuditStatus(audit.isDelegated ? 'delegated' : 'intact')
              setAuditMetrics(m => ({
                ...m,
                verifiedCount: m.verifiedCount + 1,
                sisTimeMs: audit.sisTimeMs,
                sigTimeMs: audit.sigTimeMs,
                totalTimeMs: audit.totalTimeMs,
                lastDetail: audit.detail,
              }))
            }
          }

          setChunksReceived(c => c + 1)
          if (sourceBuffer && !sourceBuffer.updating && queue.length === 0) {
            try {
              sourceBuffer.appendBuffer(chunk)
            } catch (e) {
              queue.push(chunk)
            }
          } else {
            if (queue.length < 50) {
              queue.push(chunk)
            }
          }

          if (videoRef.current && videoRef.current.paused && auditStatus !== 'tampered') {
            videoRef.current.play().catch(() => {
              if (videoRef.current) {
                videoRef.current.muted = true
                videoRef.current.play().catch(() => {})
              }
            })
          }
        }

        sourceBuffer.addEventListener('updateend', () => {
          processQueue()

          if (videoRef.current) {
            if (mode === 'live' && sourceBuffer.buffered.length > 0) {
              const livePosition = sourceBuffer.buffered.end(sourceBuffer.buffered.length - 1)
              if (livePosition - videoRef.current.currentTime > 1.4) {
                videoRef.current.currentTime = Math.max(0, livePosition - 0.3)
              }
            }

            if (sourceBuffer.buffered.length > 0 && !sourceBuffer.updating) {
              const bufferStart = sourceBuffer.buffered.start(0)
              if (videoRef.current.currentTime - bufferStart > 60) {
                try {
                  sourceBuffer.remove(bufferStart, videoRef.current.currentTime - 30)
                } catch (_) {}
              }
            }

            if (videoRef.current.paused && auditStatus !== 'tampered') {
              videoRef.current.play().catch(() => {})
            }
          }
        })
      })

      socket.onclose = (e) => {
        console.log('Connexion WSS fermée', e)
        setWatching(false)
      }

      setWs(socket)
    } catch (err) {
      console.error('Erreur découverte flux:', err)
      alert(`Erreur de localisation du flux : ${err.message || err}`)
      setWatching(false)
    }
  }

  function stopWatching() {
    if (ws) ws.close()
    setWatching(false)
    setAuditStatus('idle')
  }

  const totalAudited = auditMetrics.verifiedCount + auditMetrics.tamperedCount
  const integrityRate = totalAudited > 0
    ? ((auditMetrics.verifiedCount / totalAudited) * 100).toFixed(1)
    : '100.0'

  return (
    <article className="dashboard-card stream-card">
      <div className="card-heading">
        <div><span className="eyebrow">Lecteur Edge & Audit Public (Phase 5)</span><h2>Regarder un flux</h2></div>
        {watching && (
          <div style={{display: 'flex', gap: '8px', alignItems: 'center'}}>
            {connectedNode && (
              <span className="connection-badge" style={{background: '#1a2638', color: '#70e8b3'}}>
                Fog: {connectedNode}
              </span>
            )}
            <span className="connection-badge">
              <i style={{background: watchMode === 'live' ? '#22c55e' : '#3b82f6'}} />
              {watchMode === 'live' ? `Direct (${chunksReceived} blocs)` : `Replay (${chunksReceived} blocs)`}
            </span>
          </div>
        )}
      </div>

      {!watching ? (
        <div style={{marginTop: '1rem'}}>
          <div style={{display: 'flex', gap: '0.6rem', marginBottom: '0.8rem', alignItems: 'center'}}>
            <span style={{fontSize: '13px', color: '#8899b8', fontWeight: 500}}>Option de démarrage :</span>
            <button
              type="button"
              className={`button ${watchMode === 'live' ? 'button-primary' : 'button-quiet'}`}
              style={{padding: '5px 12px', fontSize: '12px'}}
              onClick={() => setWatchMode('live')}
            >
              🔴 En direct
            </button>
            <button
              type="button"
              className={`button ${watchMode === 'start' ? 'button-primary' : 'button-quiet'}`}
              style={{padding: '5px 12px', fontSize: '12px'}}
              onClick={() => setWatchMode('start')}
            >
              ⏮ Depuis le début
            </button>
          </div>

          <div style={{display: 'flex', gap: '0.6rem', marginBottom: '1.2rem', alignItems: 'center'}}>
            <span style={{fontSize: '13px', color: '#8899b8', fontWeight: 500}}>Résolution / Qualité :</span>
            <button
              type="button"
              className={`button ${quality === '1080p' ? 'button-primary' : 'button-quiet'}`}
              style={{padding: '5px 12px', fontSize: '12px'}}
              onClick={() => setQuality('1080p')}
            >
              📺 1080p (Original)
            </button>
            <button
              type="button"
              className={`button ${quality === '720p' ? 'button-primary' : 'button-quiet'}`}
              style={{padding: '5px 12px', fontSize: '12px'}}
              onClick={() => setQuality('720p')}
            >
              ⚡ 720p Fog (Délégation Section 3.6)
            </button>
          </div>

          <h3 style={{marginBottom: '1rem', fontSize: '14px'}}>Flux en direct disponibles</h3>
          <ActiveStreamsList onSelectStream={(s) => { setStreamId(s); startWatching(s, watchMode, quality) }} />
        </div>
      ) : (
        <>
          <video ref={videoRef} autoPlay controls playsInline style={{width: '100%', background: '#000', borderRadius: '12px', minHeight: '200px', marginTop: '1rem'}} />

          {/* TABLEAU DE BORD D'INTÉGRITÉ EN DIRECT (PHASE 5) */}
          <section className="integrity-dashboard">
            <div className="integrity-header">
              <div className="integrity-title">
                <span>🛡️</span>
                <span>Audit Public d'Intégrité en Direct (Sans TPA)</span>
              </div>
              <div>
                {auditStatus === 'intact' && (
                  <span className="integrity-badge intact">
                    <span>🟢</span> Intègre (SIS & Hess-IBS validés)
                  </span>
                )}
                {auditStatus === 'delegated' && (
                  <span className="integrity-badge delegated">
                    <span>🟡</span> Transcodé Légitime (Délégation 3 accouplements)
                  </span>
                )}
                {auditStatus === 'tampered' && (
                  <span className="integrity-badge tampered">
                    <span>🔴</span> Altéré / Corrompu (Échec de la preuve)
                  </span>
                )}
                {auditStatus === 'idle' && (
                  <span className="integrity-badge verifying">
                    <span>🔄</span> En attente de preuve
                  </span>
                )}
              </div>
            </div>

            {/* Bannière d'alerte en cas de corruption détectée */}
            {auditStatus === 'tampered' && (
              <div className="integrity-alert-banner">
                <strong>⚠️ ALERTE SÉCURITÉ : Altération détectée !</strong>
                <span>{auditMetrics.lastDetail || 'La vérification mathématique du bloc vidéo a échoué. La trame ne correspond pas à la signature de l’émetteur.'}</span>
                <span>La lecture vidéo a été coupée instantanément par précaution pour préserver l’auditabilité publique.</span>
              </div>
            )}

            {/* Grille de métriques de performance cryptographiques */}
            <div className="integrity-metrics-grid">
              <div className="integrity-metric-card">
                <span className="integrity-metric-label">Calcul SIS Local</span>
                <span className="integrity-metric-value green">{auditMetrics.sisTimeMs.toFixed(2)} ms</span>
              </div>
              <div className="integrity-metric-card">
                <span className="integrity-metric-label">Couplage BN254</span>
                <span className="integrity-metric-value purple">{auditMetrics.sigTimeMs.toFixed(2)} ms</span>
              </div>
              <div className="integrity-metric-card">
                <span className="integrity-metric-label">Latence d'Audit</span>
                <span className="integrity-metric-value cyan">{auditMetrics.totalTimeMs.toFixed(2)} ms</span>
              </div>
              <div className="integrity-metric-card">
                <span className="integrity-metric-label">Taux d'Intégrité</span>
                <span className={`integrity-metric-value ${auditMetrics.tamperedCount > 0 ? 'tampered' : 'green'}`}>{integrityRate}%</span>
              </div>
              <div className="integrity-metric-card">
                <span className="integrity-metric-label">Blocs Audités</span>
                <span className="integrity-metric-value">{auditMetrics.verifiedCount} vérifiés</span>
              </div>
            </div>

            {/* Actions & Simulateur d'attaque interactif */}
            <div className="integrity-actions">
              <button
                type="button"
                className={`button-attack ${simulateTamper ? 'active' : ''}`}
                onClick={toggleTamperSimulation}
                title="Altère un octet du fragment en transit pour tester la réaction du schéma SIS / Hess-IBS"
              >
                <span>⚡</span>
                <span>{simulateTamper ? 'Attaque Active (Octet corrompu en transit)' : 'Simuler une altération de trame (Attaque)'}</span>
              </button>

              <button
                type="button"
                className="button button-quiet button-small"
                onClick={() => setShowInspector(!showInspector)}
              >
                {showInspector ? 'Masquer inspecteur' : '🔍 Inspecteur mathématique'}
              </button>
            </div>

            {/* Inspecteur cryptographique détaillé (Section 3.4 de l'article) */}
            {showInspector && lastPacketData && (
              <div className="crypto-inspector">
                <div className="crypto-inspector-title">Paramètres Cryptographiques du Dernier Bloc Audité</div>
                <div><strong>Diffuseur (Owner ID) :</strong> {lastPacketData.signer_id}</div>
                <div><strong>Schéma SIS :</strong> q=4099, l=64, m=1536 (L'Ecuyer: I0={lastPacketData.paramA?.I0}, a={lastPacketData.paramA?.a}, C0={lastPacketData.paramA?.C0})</div>
                <div><strong>Vecteur SIS V (éch.) :</strong> [{Array.isArray(lastPacketData.V_column) ? lastPacketData.V_column.slice(0, 6).join(', ') : ''}...]</div>
                <div><strong>Signature Hess-IBS w1 :</strong> {lastPacketData.signature?.w1}</div>
                <div><strong>Signature Point w2 in G1 :</strong> [{Array.isArray(lastPacketData.signature?.w2) ? lastPacketData.signature.w2[0]?.slice(0, 16) : ''}..., ...]</div>
                <div><strong>Courbe :</strong> Alt-bn128 / BN254 (Accouplement bilinéaire optimal e: G1 × G2 → GT)</div>
                <div><strong>Type de flux :</strong> {lastPacketData.is_delegated ? `Délégué Fog (${lastPacketData.delegate_id})` : 'Direct Owner (Sans intermédiaire)'}</div>
              </div>
            )}
          </section>

          <div style={{marginTop: '1rem', display: 'flex', gap: '0.75rem', flexWrap: 'wrap', alignItems: 'center'}}>
            <button className="button button-quiet" onClick={stopWatching}>Arrêter de regarder</button>
            {watchMode === 'live' ? (
              <button
                className="button button-quiet"
                title="Reprendre le flux depuis le tout premier bloc enregistré"
                onClick={() => startWatching(streamId, 'start', quality)}
              >
                ⏮ Revoir depuis le début
              </button>
            ) : (
              <button
                className="button button-primary"
                title="Sauter directement au flux en direct temps réel"
                onClick={() => startWatching(streamId, 'live', quality)}
              >
                🔴 Rejoindre le direct
              </button>
            )}

            <div style={{display: 'flex', gap: '6px', marginLeft: 'auto', alignItems: 'center'}}>
              <span style={{fontSize: '11px', color: '#8899b8'}}>Qualité :</span>
              <button
                className={`button ${quality === '1080p' ? 'button-primary' : 'button-quiet'}`}
                style={{padding: '4px 8px', fontSize: '11px'}}
                onClick={() => startWatching(streamId, watchMode, '1080p')}
              >
                1080p
              </button>
              <button
                className={`button ${quality === '720p' ? 'button-primary' : 'button-quiet'}`}
                style={{padding: '4px 8px', fontSize: '11px'}}
                onClick={() => startWatching(streamId, watchMode, '720p')}
              >
                ⚡ 720p Fog
              </button>
            </div>
          </div>
        </>
      )}
    </article>
  )
}


function AdminDashboard({ user }) {
  const [activeTab, setActiveTab] = useState('docker')
  const [containers, setContainers] = useState([])
  const [nodesData, setNodesData] = useState({})
  const [logs, setLogs] = useState(null)
  const [loadingLogs, setLoadingLogs] = useState(false)
  const logsEndRef = useRef(null)

  useEffect(() => {
    if (logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [logs])

  useEffect(() => {
    if (activeTab === 'docker') {
      apiRequest('/api/admin/containers').then(data => {
        if (!data.error) setContainers(data)
        else console.error(data.error)
      })
    } else {
      apiRequest('/api/admin/nodes').then(data => {
        if (!data.error) setNodesData(data.zones || {})
      })
    }
  }, [activeTab])

  async function fetchLogs(containerName) {
    setLoadingLogs(true)
    setLogs(null)
    const data = await apiRequest(`/api/admin/containers/${containerName}/logs`)
    setLogs(data.logs || data.error)
    setLoadingLogs(false)
  }

  return (
    <div style={{gridColumn: '1 / -1'}}>
      <div style={{display: 'flex', gap: '1rem', marginBottom: '2rem'}}>
        <button className={activeTab === 'docker' ? 'button button-primary' : 'button button-quiet'} onClick={() => setActiveTab('docker')}>Conteneurs Docker</button>
        <button className={activeTab === 'nodes' ? 'button button-primary' : 'button button-quiet'} onClick={() => setActiveTab('nodes')}>Zones & Nœuds Edge</button>
        <button className={activeTab === 'streams' ? 'button button-primary' : 'button button-quiet'} onClick={() => setActiveTab('streams')}>Flux Vidéos</button>
      </div>

      {activeTab === 'docker' && (
        <div style={{display: 'flex', gap: '2rem'}}>
          <div style={{flex: 1}}>
            <h3 style={{marginBottom: '1rem'}}>Conteneurs en cours</h3>
            {containers.length === 0 ? <p>Aucun conteneur ou Docker inactif.</p> : (
              <div style={{display: 'flex', flexDirection: 'column', gap: '1rem'}}>
                {containers.map(c => (
                  <div key={c.id} style={{background: '#111', padding: '1rem', borderRadius: '8px', border: '1px solid #333', display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
                    <div>
                      <strong>{c.name}</strong><br />
                      <small style={{color: '#888'}}>{c.image} - <span style={{color: c.status.includes('Up') ? '#22c55e' : '#ef4444'}}>{c.status}</span></small>
                    </div>
                    <button className="button button-quiet button-small" onClick={() => fetchLogs(c.name)}>Logs</button>
                  </div>
                ))}
              </div>
            )}
          </div>
          <div style={{flex: 1, background: '#0a0a0a', border: '1px solid #222', borderRadius: '8px', padding: '1rem', maxHeight: '600px', overflowY: 'auto'}}>
            <h3 style={{marginBottom: '1rem'}}>Logs Console</h3>
            {loadingLogs ? <p>Chargement...</p> : (
              <pre style={{fontSize: '0.8rem', whiteSpace: 'pre-wrap', color: '#00ff00', fontFamily: 'monospace'}}>
                {logs || "Sélectionnez un conteneur pour voir ses logs."}
                <div ref={logsEndRef} />
              </pre>
            )}
          </div>
        </div>
      )}

      {activeTab === 'nodes' && (
        <div>
          <h3 style={{marginBottom: '1rem'}}>Topologie du Réseau Edge</h3>
          {Object.keys(nodesData).length === 0 ? <p>Aucune zone ou noeud détecté.</p> : (
            <div style={{display: 'flex', flexDirection: 'column', gap: '2rem'}}>
              {Object.entries(nodesData).map(([zoneId, nodes]) => (
                <div key={zoneId} style={{background: '#111', padding: '1.5rem', borderRadius: '12px', border: '1px solid #333'}}>
                  <h4 style={{marginBottom: '1rem', color: '#60a5fa'}}>{zoneId}</h4>
                  <div className="catalog-grid" style={{gridTemplateColumns: 'repeat(auto-fill, minmax(250px, 1fr))'}}>
                    {nodes.map(n => (
                      <div key={n.node_id} style={{background: '#000', padding: '1rem', borderRadius: '8px', border: '1px solid #222'}}>
                        <h5 style={{marginBottom: '0.5rem'}}>{n.node_id}</h5>
                        <ul style={{listStyle: 'none', padding: 0, margin: 0, fontSize: '0.9rem', color: '#aaa'}}>
                          <li><strong>IP:</strong> {n.ip_address}:{n.wss_port}</li>
                          <li><strong>Stockage:</strong> {n.storage_available_gb} Go</li>
                          <li><strong>CPU:</strong> {n.cpu_usage_pct}%</li>
                          <li><strong>Vu le:</strong> {n.last_seen ? new Date(n.last_seen).toLocaleTimeString() : 'N/A'}</li>
                        </ul>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {activeTab === 'streams' && (
        <div>
          <h3 style={{marginBottom: '1rem'}}>Flux Vidéos en cours de diffusion</h3>
          <ActiveStreamsList onSelectStream={() => {}} />
        </div>
      )}
    </div>
  )
}

function Dashboard({ user, onLogout }) {
  return (
    <section className="dashboard">
      <div className="dashboard-grid">
        {user.role === 'admin' ? <AdminDashboard user={user} /> : user.role === 'streamer' ? <StreamerInterface user={user} /> : <ViewerInterface user={user} />}
        {user.role !== 'admin' && (
          <aside className="dashboard-card profile-card">
          <span className="avatar">{user.email?.charAt(0).toUpperCase()}</span>
          <span className="eyebrow">Votre profil</span>
          <h2>{user.email}</h2>
          <dl>
            <div><dt>Rôle</dt><dd>{user.role || 'viewer'}</dd></div>
            <div><dt>Statut</dt><dd className="green">Actif</dd></div>
          </dl>
        </aside>
        )}
      </div>
    </section>
  )
}

export default function App() {
  const [authMode, setAuthMode] = useState(null)
  const [user, setUser] = useState(null)

  useEffect(() => {
    apiRequest('/auth/me')
      .then(data => { if (data.user) setUser(data.user) })
      .catch(() => {}) // Ignore errors if not logged in
  }, [])

  async function logout() { try { await apiRequest('/auth/logout', { method: 'POST' }) } finally { setUser(null) } }

  return <div className="app-shell"><div className="ambient ambient-blue" /><div className="ambient ambient-violet" /><header className="site-header"><Logo /><nav>{user ? <button className="nav-account" type="button" onClick={logout}>Quitter l’espace</button> : <><button className="nav-login" type="button" onClick={() => setAuthMode('login')}>Se connecter</button><button className="button button-primary button-small" type="button" onClick={() => setAuthMode('register')}>Créer un compte</button></>}</nav></header><main>{user ? <Dashboard user={user} onLogout={logout} /> : <Home openAuth={setAuthMode} />}</main>{authMode && <AuthDialog mode={authMode} setMode={setAuthMode} onSuccess={(nextUser) => { setUser(nextUser); setAuthMode(null) }} onClose={() => setAuthMode(null)} />}</div>
}
