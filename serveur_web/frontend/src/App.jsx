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
import {
  PlayIcon,
  PauseIcon,
  VolumeIcon,
  VolumeMuteIcon,
  MaximizeIcon,
  TvIcon,
  ZapIcon,
  ShieldCheckIcon,
  ShieldAlertIcon,
  LockIcon,
  RadioIcon,
  ServerIcon,
  ActivityIcon,
  SearchIcon,
  CloseIcon,
  AlertTriangleIcon,
  VideoIcon,
  VideoOffIcon,
  LogOutIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  ChevronDownIcon,
  CodeIcon,
} from './Icons.jsx'

const API_URL = import.meta.env.VITE_API_URL || ''

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

function Logo({ onClick }) {
  return (
    <a className="brand-link" href="#top" onClick={onClick} aria-label="Pledge accueil">
      <img src="/logo.png" alt="Pledge" className="brand-logo-img" />
      <div className="brand-tag">
        <span className="brand-name">PLEDGE</span>
        <span className="brand-slogan">STREAM BEYOND</span>
      </div>
    </a>
  )
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
        <button className="close-button" type="button" onClick={onClose} aria-label="Fermer">
          <CloseIcon size={18} />
        </button>
        <span className="auth-eyebrow">{isRegister ? 'Rejoindre le Réseau' : 'Connexion'}</span>
        <h2 id="auth-title">{isRegister ? 'Créer un compte' : 'Se connecter'}</h2>
        <p>{isRegister ? 'Accédez au streaming décentralisé Pledge.' : 'Retrouvez votre espace et vos flux en direct.'}</p>
        <form onSubmit={handleSubmit}>
          <label>
            Adresse email
            <input name="email" type="email" value={form.email} onChange={updateField} placeholder="vous@exemple.com" autoComplete="email" required />
          </label>
          <label>
            Mot de passe
            <input name="password" type="password" value={form.password} onChange={updateField} placeholder="••••••••" minLength="6" autoComplete={isRegister ? 'new-password' : 'current-password'} required />
          </label>
          {isRegister && (
            <label>
              Rôle
              <select name="role" value={form.role} onChange={updateField}>
                <option value="viewer">Visionneur (Spectateur)</option>
                <option value="streamer">Diffuseur (Streamer)</option>
              </select>
            </label>
          )}
          <div className="form-error" role="alert">{error}</div>
          <button className="button button-primary button-wide" type="submit" disabled={loading} style={{ marginTop: '10px' }}>
            {loading ? 'Connexion...' : isRegister ? 'Créer mon compte' : 'Se connecter'}
          </button>
        </form>
        <button className="text-button" type="button" onClick={() => setMode(isRegister ? 'login' : 'register')}>
          {isRegister ? 'Déjà un compte ? Se connecter' : 'Nouveau ? Créer un compte'}
        </button>
      </section>
    </div>
  )
}

// =====================================================================
// SIDEBAR ÉPURÉE (CHAÎNES EN DIRECT)
// =====================================================================
function SimpleSidebar({ streams, activeStream, onSelectStream, isCollapsed, onToggleCollapse }) {
  return (
    <nav className={`twitch-sidebar ${isCollapsed ? 'collapsed' : ''}`} aria-label="Chaînes en direct">
      <div className="sidebar-header">
        {!isCollapsed && <span className="sidebar-title">Chaînes en direct</span>}
        <button className="sidebar-toggle-btn" onClick={onToggleCollapse} title={isCollapsed ? 'Développer' : 'Réduire'}>
          {isCollapsed ? <ChevronRightIcon size={18} /> : <ChevronLeftIcon size={18} />}
        </button>
      </div>

      <div className="sidebar-stream-list">
        {streams.length === 0 ? (
          !isCollapsed && (
            <div style={{ padding: '16px 12px', color: 'var(--muted)', fontSize: '11.5px' }}>
              Aucun flux en direct.
            </div>
          )
        ) : (
          streams.map((s) => {
            const isActive = s === activeStream
            const initial = s ? s.charAt(0).toUpperCase() : 'P'
            return (
              <button
                key={s}
                type="button"
                className={`sidebar-stream-item live ${isActive ? 'active' : ''}`}
                onClick={() => onSelectStream(s)}
                title={s}
              >
                <div className="stream-item-avatar-wrapper">
                  <div className="stream-item-avatar">{initial}</div>
                  <div className="stream-item-live-dot" />
                </div>
                {!isCollapsed && (
                  <>
                    <div className="stream-item-info">
                      <span className="stream-item-name">{s.split('@')[0]}</span>
                      <span className="stream-item-category">Edge Fog</span>
                    </div>
                    <span className="live-badge" style={{ padding: '1px 5px', fontSize: '9px' }}>
                      LIVE
                    </span>
                  </>
                )}
              </button>
            )
          })
        )}
      </div>
    </nav>
  )
}

// =====================================================================
// INTERFACE VISIONNEUR ÉPURÉE ET SIMPLE
// =====================================================================
function ViewerInterface({ user, selectedStreamId, onSelectStream, streams }) {
  const [streamId, setStreamId] = useState(selectedStreamId || '')
  const [watching, setWatching] = useState(false)
  const [quality, setQuality] = useState('1080p')
  const [chunksReceived, setChunksReceived] = useState(0)
  const [connectedNode, setConnectedNode] = useState(null)
  const [isPlaying, setIsPlaying] = useState(true)
  const [isMuted, setIsMuted] = useState(true)
  const [volume, setVolume] = useState(1)
  const [showTechnicalDetails, setShowTechnicalDetails] = useState(false)
  const videoRef = useRef(null)
  const [ws, setWs] = useState(null)

  // Audit Public d'Intégrité sans TPA (Phase 5)
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
  const [lastPacketData, setLastPacketData] = useState(null)

  useEffect(() => {
    if (selectedStreamId && selectedStreamId !== streamId) {
      setStreamId(selectedStreamId)
      startWatching(selectedStreamId, quality)
    }
  }, [selectedStreamId])

  function toggleTamperSimulation() {
    const nextVal = !simulateTamper
    setSimulateTamper(nextVal)
    simulateTamperRef.current = nextVal
  }

  function handlePlayPause() {
    if (!videoRef.current) return
    if (videoRef.current.paused) {
      videoRef.current.play().then(() => setIsPlaying(true)).catch(() => {})
    } else {
      videoRef.current.pause()
      setIsPlaying(false)
    }
  }

  function handleToggleMute() {
    if (!videoRef.current) return
    const nextMuted = !videoRef.current.muted
    videoRef.current.muted = nextMuted
    setIsMuted(nextMuted)
  }

  function handleVolumeChange(e) {
    const val = parseFloat(e.target.value)
    setVolume(val)
    if (videoRef.current) {
      videoRef.current.volume = val
      videoRef.current.muted = val === 0
      setIsMuted(val === 0)
    }
  }

  function handleFullscreen() {
    if (!videoRef.current) return
    if (videoRef.current.requestFullscreen) {
      videoRef.current.requestFullscreen()
    }
  }

  async function startWatching(idToWatch, q = quality) {
    const targetId = idToWatch || streamId
    if (!targetId) return

    if (ws) {
      try {
        ws.close()
      } catch (_) {}
      setWs(null)
    }

    setStreamId(targetId)
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
      // 1. Découverte du nœud Edge Fog via le backend
      const discovery = await apiRequest(`/api/stream/discover/${encodeURIComponent(targetId)}?mode=start&quality=${q}`)
      setConnectedNode(discovery.edge_node_id)

      const defaultZonePk = deriveStreamerKey(targetId).zonePk

      // 2. Connexion directe WebSocket au nœud Edge Fog
      const isLocalHost =
        !discovery.edge_host ||
        discovery.edge_host === '127.0.0.1' ||
        discovery.edge_host === 'localhost' ||
        discovery.edge_host === 'host.docker.internal'
      const edgeHost = isLocalHost ? window.location.hostname : discovery.edge_host
      const directDownloadUrl = `${discovery.scheme || 'ws'}://${edgeHost}:${discovery.edge_port}/download/${encodeURIComponent(
        targetId
      )}?mode=start&quality=${q}`

      const socket = new WebSocket(directDownloadUrl)
      socket.binaryType = 'arraybuffer'

      const mediaSource = new MediaSource()
      setTimeout(() => {
        if (videoRef.current) {
          videoRef.current.src = URL.createObjectURL(mediaSource)
        }
      }, 50)

      mediaSource.addEventListener('sourceopen', () => {
        const mime = MediaRecorder.isTypeSupported('video/webm; codecs=vp8,opus')
          ? 'video/webm; codecs=vp8,opus'
          : 'video/webm'
        let sourceBuffer
        try {
          sourceBuffer = mediaSource.addSourceBuffer(mime)
        } catch (err) {
          console.error('Erreur addSourceBuffer:', err)
          return
        }

        const queue = []
        let retryTimeout = null
        let appendRetryCount = 0

        function processQueue() {
          if (!sourceBuffer || sourceBuffer.updating || queue.length === 0) return
          if (mediaSource.readyState !== 'open') return

          const next = queue.shift()
          try {
            sourceBuffer.appendBuffer(next)
            appendRetryCount = 0
          } catch (e) {
            appendRetryCount++
            if (appendRetryCount < 3) {
              queue.unshift(next)
              if (!retryTimeout) {
                retryTimeout = setTimeout(() => {
                  retryTimeout = null
                  processQueue()
                }, 50)
              }
            } else {
              appendRetryCount = 0
            }
          }
        }

        const handleGapNudge = () => {
          if (!videoRef.current || !sourceBuffer) return
          try {
            const cur = videoRef.current.currentTime
            const bufs = sourceBuffer.buffered
            for (let i = 0; i < bufs.length; i++) {
              const bStart = bufs.start(i)
              const bEnd = bufs.end(i)
              if (cur < bStart && bStart - cur < 0.6) {
                videoRef.current.currentTime = bStart + 0.03
                videoRef.current.play().catch(() => {})
                return
              }
              if (Math.abs(cur - bEnd) < 0.25 && i + 1 < bufs.length) {
                const nextStart = bufs.start(i + 1)
                videoRef.current.currentTime = nextStart + 0.03
                videoRef.current.play().catch(() => {})
                return
              }
            }
          } catch (_) {}
        }

        if (videoRef.current) {
          videoRef.current.addEventListener('waiting', handleGapNudge)
          videoRef.current.addEventListener('stalled', handleGapNudge)
        }

        sourceBuffer.addEventListener('updateend', () => {
          processQueue()
          if (videoRef.current) {
            try {
              if (sourceBuffer.buffered.length > 0) {
                const bufferStart = sourceBuffer.buffered.start(0)
                if (videoRef.current.currentTime < bufferStart) {
                  videoRef.current.currentTime = bufferStart
                }
              }
              handleGapNudge()
              if (auditStatus !== 'tampered') {
                videoRef.current.play().catch(() => {})
              }
            } catch (_) {}
          }
        })

        socket.onmessage = async (event) => {
          let chunk = null
          let packet = null

          if (typeof event.data === 'string') {
            try {
              packet = JSON.parse(event.data)
              const b64 = packet?.payload_b64 || packet?.chunk_b64
              if (b64) chunk = base64ToUint8Array(b64)
            } catch (_) {}
          } else if (event.data instanceof ArrayBuffer) {
            try {
              const text = new TextDecoder().decode(event.data)
              if (text.trim().startsWith('{')) {
                packet = JSON.parse(text)
                const b64 = packet?.payload_b64 || packet?.chunk_b64
                if (b64) chunk = base64ToUint8Array(b64)
              }
            } catch (_) {}
            if (!chunk) chunk = new Uint8Array(event.data)
          }

          if (!chunk) return

          // Simulation d'altération en transit pour démonstration
          if (simulateTamperRef.current) {
            chunk[0] ^= 0x55
          }

          // VÉRIFICATION D'INTÉGRITÉ CÔTÉ SPECTATEUR (Phase 5)
          if (packet) {
            setLastPacketData(packet)
            const audit = verifyIncomingBlock(chunk, packet, defaultZonePk)

            if (!audit.isValid) {
              setAuditStatus('tampered')
              setAuditMetrics((m) => ({
                ...m,
                tamperedCount: m.tamperedCount + 1,
                sisTimeMs: audit.sisTimeMs,
                sigTimeMs: audit.sigTimeMs,
                totalTimeMs: audit.totalTimeMs,
                lastDetail: audit.detail,
              }))
              if (videoRef.current) {
                videoRef.current.pause()
              }
              return
            } else {
              setAuditStatus(audit.isDelegated ? 'delegated' : 'intact')
              setAuditMetrics((m) => ({
                ...m,
                verifiedCount: m.verifiedCount + 1,
                sisTimeMs: audit.sisTimeMs,
                sigTimeMs: audit.sigTimeMs,
                totalTimeMs: audit.totalTimeMs,
                lastDetail: audit.detail,
              }))
            }
          }

          setChunksReceived((c) => c + 1)
          queue.push(chunk)
          processQueue()

          if (videoRef.current && videoRef.current.paused && auditStatus !== 'tampered') {
            videoRef.current.play().catch(() => {
              if (videoRef.current) {
                videoRef.current.muted = true
                videoRef.current.play().catch(() => {})
              }
            })
          }
        }
      })

      socket.onclose = () => {
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
    if (videoRef.current) videoRef.current.src = ''
    setWatching(false)
    setAuditStatus('idle')
  }

  const streamerName = streamId ? streamId.split('@')[0] : 'Pledge'
  const totalAudited = auditMetrics.verifiedCount + auditMetrics.tamperedCount
  const integrityRate = totalAudited > 0 ? ((auditMetrics.verifiedCount / totalAudited) * 100).toFixed(1) : '100.0'

  return (
    <div className="twitch-main-stage">
      <div className="viewer-clean-container">
        {/* LECTEUR VIDÉO ÉPURÉ 16:9 */}
        <div className="twitch-player-wrapper">
          {watching ? (
            <>
              <video ref={videoRef} autoPlay muted playsInline className="twitch-video-element" />

              {/* Incrustations discrètes sur le lecteur */}
              <div className="player-top-overlay">
                <div className="player-top-left-badges">
                  <span className="live-badge">
                    <span className="pulse-dot" /> EN DIRECT
                  </span>
                  {connectedNode && (
                    <span className="player-overlay-pill fog">
                      <ServerIcon size={12} /> {connectedNode}
                    </span>
                  )}
                </div>

                <div>
                  {auditStatus === 'intact' && (
                    <span className="player-overlay-pill audit-intact">
                      <ShieldCheckIcon size={13} /> Flux intègre
                    </span>
                  )}
                  {auditStatus === 'delegated' && (
                    <span className="player-overlay-pill audit-delegated">
                      <ZapIcon size={13} /> 720p Transcodé
                    </span>
                  )}
                  {auditStatus === 'tampered' && (
                    <span className="player-overlay-pill audit-tampered">
                      <AlertTriangleIcon size={13} /> Altération détectée
                    </span>
                  )}
                </div>
              </div>

              {/* Barre de contrôles propre */}
              <div className="player-controls-bar">
                <div className="player-controls-left">
                  <button className="player-control-btn" onClick={handlePlayPause} title={isPlaying ? 'Pause' : 'Lecture'}>
                    {isPlaying ? <PauseIcon size={18} /> : <PlayIcon size={18} />}
                  </button>
                  <div className="volume-slider-container">
                    <button className="player-control-btn" onClick={handleToggleMute} title={isMuted ? 'Activer le son' : 'Couper le son'}>
                      {isMuted ? <VolumeMuteIcon size={18} /> : <VolumeIcon size={18} />}
                    </button>
                    <input
                      type="range"
                      min="0"
                      max="1"
                      step="0.05"
                      value={isMuted ? 0 : volume}
                      onChange={handleVolumeChange}
                      className="volume-slider"
                      aria-label="Volume"
                    />
                  </div>
                </div>

                <div className="player-controls-right">
                  <div className="quality-selector-group">
                    <button
                      type="button"
                      className={`quality-button ${quality === '1080p' ? 'active' : ''}`}
                      onClick={() => startWatching(streamId, '1080p')}
                    >
                      1080p
                    </button>
                    <button
                      type="button"
                      className={`quality-button ${quality === '720p' ? 'active' : ''}`}
                      onClick={() => startWatching(streamId, '720p')}
                    >
                      720p Fog
                    </button>
                  </div>

                  <button className="player-control-btn" onClick={handleFullscreen} title="Plein écran">
                    <MaximizeIcon size={17} />
                  </button>
                </div>
              </div>
            </>
          ) : (
            <div className="player-offline-screen">
              <div className="offline-avatar">{streamerName.charAt(0).toUpperCase()}</div>
              <h2 className="offline-title">
                {streamId ? `Chaîne de ${streamerName}` : 'Sélectionnez un flux en direct'}
              </h2>
              <p className="offline-subtitle">
                {streamId
                  ? 'Diffusion prête. Cliquez sur le bouton pour démarrer le visionnage.'
                  : 'Choisissez une diffusion dans la liste à gauche.'}
              </p>

              {streamId ? (
                <button className="button button-primary" onClick={() => startWatching(streamId, quality)}>
                  <PlayIcon size={16} /> Lancer la vidéo
                </button>
              ) : streams.length > 0 ? (
                <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', justifyContent: 'center' }}>
                  {streams.map((s) => (
                    <button key={s} className="button button-quiet button-small" onClick={() => startWatching(s, '1080p')}>
                      <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: '#22c55e', marginRight: '6px' }} />
                      {s.split('@')[0]}
                    </button>
                  ))}
                </div>
              ) : (
                <p style={{ color: '#64748b', fontSize: '12px' }}>Aucun flux actif actuellement.</p>
              )}
            </div>
          )}
        </div>

        {/* EN-TÊTE ÉPURÉ DE LA CHAÎNE */}
        <div className="clean-channel-header">
          <div className="channel-info-left">
            <div className="channel-avatar-large">{streamerName.charAt(0).toUpperCase()}</div>
            <div className="channel-text-content">
              <h1 className="channel-stream-title">
                {streamId ? `Diffusion de ${streamerName}` : 'Pledge Live Stream'}
              </h1>
              <div className="channel-meta-row">
                <span className="channel-streamer-name">
                  {streamId || 'Pledge'}
                  <span className="channel-verified-badge" title="Sécurité cryptographique active">
                    <ShieldCheckIcon size={14} />
                  </span>
                </span>
                <span className="channel-tag-pill accent">Hess-IBS & SIS</span>
                <span className="channel-tag-pill">Sans TPA</span>
                {connectedNode && <span className="channel-tag-pill">{connectedNode}</span>}
              </div>
            </div>
          </div>

          <div className="channel-actions-right">
            {watching && (
              <button className="button button-quiet button-small" onClick={stopWatching}>
                Arrêter
              </button>
            )}
          </div>
        </div>

        {/* CARTE D'INTÉGRITÉ ÉPURÉE ET COMPACTE (SANS SURCHARGE) */}
        <div className="clean-integrity-card">
          <div className="clean-integrity-row">
            <div className="integrity-summary-left">
              {auditStatus === 'tampered' ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#f87171', fontWeight: 600 }}>
                  <AlertTriangleIcon size={18} />
                  <span>Altération détectée en transit ! La lecture a été suspendue.</span>
                </div>
              ) : (
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#4ade80' }}>
                  <ShieldCheckIcon size={18} />
                  <span style={{ fontWeight: 500 }}>
                    {auditStatus === 'intact'
                      ? 'Flux vidéo intègre et vérifié en temps réel'
                      : auditStatus === 'delegated'
                      ? 'Flux transcodé avec délégation vérifiée'
                      : 'Sécurité mathématique active (Hess-IBS & SIS sans TPA)'}
                  </span>
                </div>
              )}
            </div>

            <div className="integrity-summary-actions">
              <button
                type="button"
                className={`button-attack-toggle ${simulateTamper ? 'active' : ''}`}
                onClick={toggleTamperSimulation}
                title="Simule une corruption de trame pour tester le rejet immédiat"
              >
                <ZapIcon size={13} />
                <span>{simulateTamper ? 'Attaque active' : 'Tester altération'}</span>
              </button>

              <button
                type="button"
                className="button button-quiet button-small"
                onClick={() => setShowTechnicalDetails(!showTechnicalDetails)}
              >
                <CodeIcon size={13} />
                <span>{showTechnicalDetails ? 'Masquer détails' : 'Détails techniques'}</span>
              </button>
            </div>
          </div>

          {/* DÉTAILS TECHNIQUES DÉPLIABLES (SEULEMENT SI DEMANDÉ) */}
          {showTechnicalDetails && (
            <div className="clean-technical-drawer">
              <div className="integrity-metrics-grid">
                <div className="integrity-metric-box">
                  <span className="metric-box-label">Calcul SIS Local</span>
                  <span className="metric-box-val green">{auditMetrics.sisTimeMs.toFixed(2)} ms</span>
                </div>
                <div className="integrity-metric-box">
                  <span className="metric-box-label">Couplage BN254</span>
                  <span className="metric-box-val purple">{auditMetrics.sigTimeMs.toFixed(2)} ms</span>
                </div>
                <div className="integrity-metric-box">
                  <span className="metric-box-label">Latence d'Audit</span>
                  <span className="metric-box-val cyan">{auditMetrics.totalTimeMs.toFixed(2)} ms</span>
                </div>
                <div className="integrity-metric-box">
                  <span className="metric-box-label">Taux d'Intégrité</span>
                  <span className={`metric-box-val ${auditMetrics.tamperedCount > 0 ? 'tampered' : 'green'}`}>{integrityRate}%</span>
                </div>
              </div>

              {lastPacketData && (
                <div className="crypto-inspector-box" style={{ marginTop: '12px' }}>
                  <div className="inspector-header-label">Paramètres Cryptographiques</div>
                  <div><strong>Signataire :</strong> {lastPacketData.signer_id}</div>
                  <div><strong>SIS :</strong> q=4099, l=64, m=1536 (I0={lastPacketData.paramA?.I0}, a={lastPacketData.paramA?.a}, C0={lastPacketData.paramA?.C0})</div>
                  <div><strong>Signature Hess-IBS w1 :</strong> {lastPacketData.signature?.w1}</div>
                  <div><strong>Délégation :</strong> {lastPacketData.is_delegated ? `Délégué à ${lastPacketData.delegate_id}` : 'Direct'}</div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// =====================================================================
// STUDIO DIFFUSEUR SIMPLE ET EFFICACE
// =====================================================================
function StreamerInterface({ user }) {
  const [streaming, setStreaming] = useState(false)
  const [chunksSent, setChunksSent] = useState(0)
  const [edgeNode, setEdgeNode] = useState(null)
  const [streamTitle, setStreamTitle] = useState('Diffusion Edge Direct')
  const [selectedZone, setSelectedZone] = useState('Zone_1')
  const [ws, setWs] = useState(null)
  const [mediaRecorder, setMediaRecorder] = useState(null)
  const blockIndexRef = useRef(0)
  const heartbeatTimerRef = useRef(null)
  const videoRef = useRef(null)
  const [streamerKey] = useState(() => deriveStreamerKey(user.email))

  async function startStream() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true })
      if (videoRef.current) videoRef.current.srcObject = stream

      const alloc = await apiRequest('/api/stream/allocate', {
        method: 'POST',
        body: JSON.stringify({ session_id: user.email, zone_id: selectedZone }),
      })
      setEdgeNode(alloc.edge_node_id)

      const isLocalHost =
        !alloc.edge_host ||
        alloc.edge_host === '127.0.0.1' ||
        alloc.edge_host === 'localhost' ||
        alloc.edge_host === 'host.docker.internal'
      const edgeHost = isLocalHost ? window.location.hostname : alloc.edge_host
      const directUploadUrl = `${alloc.scheme || 'ws'}://${edgeHost}:${alloc.edge_port}/upload/${encodeURIComponent(
        user.email
      )}`

      const socket = new WebSocket(directUploadUrl)
      blockIndexRef.current = 0

      socket.onopen = () => {
        setStreaming(true)
        setChunksSent(0)
        const mime = MediaRecorder.isTypeSupported('video/webm; codecs=vp8,opus')
          ? 'video/webm; codecs=vp8,opus'
          : 'video/webm'
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

            const paramA = alloc.paramA || DEFAULT_PARAM_A
            const vColumn = computeSisVector(chunkBytes, paramA)
            const signature = signHessIBS(vColumn, streamerKey.secretKey, paramA)

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
            setChunksSent((c) => c + 1)
          }
        }

        recorder.start(1000)
        setMediaRecorder(recorder)

        heartbeatTimerRef.current = setInterval(() => {
          apiRequest(`/api/stream/heartbeat/${encodeURIComponent(user.email)}`, { method: 'POST' }).catch(() => {})
        }, 8000)
      }

      socket.onclose = () => {
        setStreaming(false)
        if (heartbeatTimerRef.current) clearInterval(heartbeatTimerRef.current)
        if (videoRef.current && videoRef.current.srcObject) {
          videoRef.current.srcObject.getTracks().forEach((t) => t.stop())
          videoRef.current.srcObject = null
        }
      }

      setWs(socket)
    } catch (e) {
      console.error('Erreur démarrage stream:', e)
      alert("Accès caméra refusé ou nœud Edge injoignable.")
    }
  }

  function stopStream() {
    if (heartbeatTimerRef.current) clearInterval(heartbeatTimerRef.current)
    if (mediaRecorder && mediaRecorder.state !== 'inactive') mediaRecorder.stop()
    if (ws) ws.close()
    setStreaming(false)
    apiRequest(`/api/stream/stop/${encodeURIComponent(user.email)}`, { method: 'POST' }).catch(() => {})
    if (videoRef.current && videoRef.current.srcObject) {
      videoRef.current.srcObject.getTracks().forEach((t) => t.stop())
      videoRef.current.srcObject = null
    }
  }

  return (
    <div className="twitch-main-stage">
      <div className="viewer-clean-container">
        <div className="studio-top-bar" style={{ marginBottom: '16px' }}>
          <div className="studio-title-group">
            <RadioIcon size={20} className="green" />
            <h2 style={{ fontSize: '16px', margin: 0 }}>Studio Diffuseur</h2>
          </div>
          <div className="studio-stats-pills">
            <span className={streaming ? 'live-badge' : 'button button-quiet button-small'}>
              {streaming && <span className="pulse-dot" />}
              {streaming ? `EN DIRECT (${chunksSent} blocs)` : 'HORS LIGNE'}
            </span>
            {streaming && edgeNode && (
              <span className="player-overlay-pill fog">
                <ServerIcon size={12} /> {edgeNode}
              </span>
            )}
          </div>
        </div>

        <div className="twitch-player-wrapper" style={{ maxHeight: '55vh' }}>
          <video ref={videoRef} autoPlay muted playsInline className="twitch-video-element" />
          {!streaming && (
            <div className="player-offline-screen">
              <VideoIcon size={40} style={{ opacity: 0.4, marginBottom: '8px' }} />
              <p style={{ color: 'var(--muted)', margin: 0 }}>Caméra en veille</p>
            </div>
          )}
        </div>

        <div style={{ marginTop: '16px', display: 'flex', gap: '12px', alignItems: 'center' }}>
          {streaming ? (
            <button type="button" className="button button-danger" onClick={stopStream} style={{ padding: '10px 20px' }}>
              <VideoOffIcon size={16} /> Arrêter la diffusion
            </button>
          ) : (
            <button type="button" className="button button-primary" onClick={startStream} style={{ padding: '10px 20px' }}>
              <RadioIcon size={16} /> Lancer la diffusion
            </button>
          )}

          <div style={{ marginLeft: 'auto', display: 'flex', gap: '10px', alignItems: 'center' }}>
            <span style={{ fontSize: '12px', color: 'var(--muted)' }}>Zone :</span>
            <select
              value={selectedZone}
              onChange={(e) => setSelectedZone(e.target.value)}
              disabled={streaming}
              style={{
                background: '#090e1c',
                border: '1px solid var(--line)',
                borderRadius: '6px',
                padding: '6px 10px',
                color: 'var(--white)',
                fontSize: '12px',
              }}
            >
              <option value="Zone_1">Zone 1</option>
              <option value="Zone_2">Zone 2</option>
            </select>
          </div>
        </div>
      </div>
    </div>
  )
}

// =====================================================================
// PAGE D'ACCUEIL / DÉCOUVERTE SIMPLE
// =====================================================================
function TwitchBrowse({ streams, onSelectStream, openAuth }) {
  return (
    <div className="twitch-main-stage">
      <div className="viewer-clean-container" style={{ padding: '32px 0' }}>
        <div className="twitch-hero-banner" style={{ marginBottom: '32px' }}>
          <div className="hero-banner-copy">
            <span className="live-badge" style={{ marginBottom: '10px' }}>
              <span className="pulse-dot" /> STREAM BEYOND
            </span>
            <h1>Streaming Décentralisé Nouvelle Génération</h1>
            <p>
              Plateforme haute fidélité propulsée par le Fog computing avec intégrité cryptographique prouvée sans tiers de confiance.
            </p>
            <div style={{ display: 'flex', gap: '10px' }}>
              <button className="button button-primary" onClick={() => openAuth('register')}>
                Rejoindre le réseau
              </button>
              <button className="button button-quiet" onClick={() => openAuth('login')}>
                Se connecter
              </button>
            </div>
          </div>
        </div>

        <div>
          <h2 style={{ fontSize: '16px', marginBottom: '14px', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <RadioIcon size={18} className="green" /> Flux en direct
          </h2>

          {streams.length === 0 ? (
            <div style={{ padding: '24px', textAlign: 'center', background: 'var(--panel)', borderRadius: '8px', border: '1px solid var(--line)' }}>
              <p style={{ color: 'var(--muted)', margin: 0 }}>Aucune diffusion active en ce moment.</p>
            </div>
          ) : (
            <div className="twitch-channels-grid">
              {streams.map((s) => {
                const initial = s ? s.charAt(0).toUpperCase() : 'P'
                return (
                  <div key={s} className="channel-card-item" onClick={() => onSelectStream(s)}>
                    <div className="card-thumbnail-stage">
                      <div className="card-thumbnail-art">
                        <PlayIcon size={34} className="cyan" style={{ opacity: 0.8 }} />
                      </div>
                      <span className="live-badge card-live-pill">
                        <span className="pulse-dot" /> LIVE
                      </span>
                    </div>

                    <div className="card-details-row">
                      <div className="card-channel-avatar">{initial}</div>
                      <div className="card-channel-texts">
                        <span className="card-title-line">Flux {s.split('@')[0]}</span>
                        <span className="card-streamer-line">{s}</span>
                        <span className="card-tag-line">Hess-IBS • 1080p</span>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// =====================================================================
// TABLEAU DE BORD ADMIN SIMPLE
// =====================================================================
function AdminDashboard({ user }) {
  const [activeTab, setActiveTab] = useState('docker')
  const [containers, setContainers] = useState([])
  const [nodesData, setNodesData] = useState({})
  const [logs, setLogs] = useState(null)

  useEffect(() => {
    if (activeTab === 'docker') {
      apiRequest('/api/admin/containers').then((data) => {
        if (!data.error) setContainers(data)
      })
    } else {
      apiRequest('/api/admin/nodes').then((data) => {
        if (!data.error) setNodesData(data.zones || {})
      })
    }
  }, [activeTab])

  async function fetchLogs(name) {
    const data = await apiRequest(`/api/admin/containers/${name}/logs`)
    setLogs(data.logs || data.error)
  }

  return (
    <div className="twitch-main-stage">
      <div className="viewer-clean-container" style={{ padding: '24px 0' }}>
        <div style={{ display: 'flex', gap: '10px', marginBottom: '20px' }}>
          <button className={activeTab === 'docker' ? 'button button-primary' : 'button button-quiet'} onClick={() => setActiveTab('docker')}>
            Conteneurs Docker
          </button>
          <button className={activeTab === 'nodes' ? 'button button-primary' : 'button button-quiet'} onClick={() => setActiveTab('nodes')}>
            Nœuds Fog
          </button>
        </div>

        {activeTab === 'docker' && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {containers.map((c) => (
                <div key={c.id} style={{ background: 'var(--panel)', padding: '10px 14px', borderRadius: '6px', border: '1px solid var(--line)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div>
                    <strong>{c.name}</strong><br />
                    <small style={{ color: 'var(--muted)' }}>{c.status}</small>
                  </div>
                  <button className="button button-quiet button-small" onClick={() => fetchLogs(c.name)}>Logs</button>
                </div>
              ))}
            </div>
            <pre style={{ background: '#050811', border: '1px solid var(--line)', borderRadius: '6px', padding: '12px', fontSize: '11px', color: '#4ade80', maxHeight: '400px', overflowY: 'auto' }}>
              {logs || 'Cliquez sur Logs pour afficher la sortie.'}
            </pre>
          </div>
        )}

        {activeTab === 'nodes' && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: '14px' }}>
            {Object.entries(nodesData).map(([zoneId, nodes]) => (
              <div key={zoneId} style={{ background: 'var(--panel)', padding: '14px', borderRadius: '8px', border: '1px solid var(--line)' }}>
                <h4 style={{ margin: '0 0 10px', color: 'var(--blue)' }}>{zoneId}</h4>
                {nodes.map((n) => (
                  <div key={n.node_id} style={{ fontSize: '12px', color: 'var(--muted)', marginBottom: '6px' }}>
                    <strong>{n.node_id}</strong> - {n.ip_address}:{n.wss_port}
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// =====================================================================
// COMPOSANT PRINCIPAL
// =====================================================================
export default function App() {
  const [authMode, setAuthMode] = useState(null)
  const [user, setUser] = useState(null)
  const [streams, setStreams] = useState([])
  const [selectedStream, setSelectedStream] = useState('')
  const [currentView, setCurrentView] = useState('browse') // 'browse' | 'live' | 'studio' | 'admin'
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)

  useEffect(() => {
    apiRequest('/auth/me')
      .then((data) => {
        if (data && data.user) {
          setUser(data.user)
          if (data.user.role === 'admin') setCurrentView('admin')
          else if (data.user.role === 'streamer') setCurrentView('studio')
          else setCurrentView('live')
        }
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    function fetchStreams() {
      apiRequest('/api/streams/active')
        .then((data) => {
          if (data && Array.isArray(data.streams)) {
            setStreams(data.streams)
            if (data.streams.length > 0 && !selectedStream) {
              setSelectedStream(data.streams[0])
            }
          }
        })
        .catch(() => {})
    }
    fetchStreams()
    const interval = setInterval(fetchStreams, 4000)
    return () => clearInterval(interval)
  }, [selectedStream])

  async function handleLogout() {
    try {
      await apiRequest('/auth/logout', { method: 'POST' })
    } finally {
      setUser(null)
      setCurrentView('browse')
    }
  }

  function handleSelectStream(streamId) {
    setSelectedStream(streamId)
    setCurrentView('live')
  }

  return (
    <div className="twitch-app">
      {/* HEADER DE NAVIGATION TWITCH ÉPURÉ */}
      <header className="twitch-header">
        <div className="twitch-header-left">
          <Logo onClick={() => setCurrentView('browse')} />

          <nav className="twitch-nav-links">
            <button
              type="button"
              className={`nav-tab-button ${currentView === 'browse' ? 'active' : ''}`}
              onClick={() => setCurrentView('browse')}
            >
              Découvrir
            </button>
            <button
              type="button"
              className={`nav-tab-button ${currentView === 'live' ? 'active' : ''}`}
              onClick={() => setCurrentView('live')}
            >
              En direct
            </button>
            {user?.role === 'streamer' && (
              <button
                type="button"
                className={`nav-tab-button ${currentView === 'studio' ? 'active' : ''}`}
                onClick={() => setCurrentView('studio')}
              >
                Studio Diffuseur
              </button>
            )}
            {user?.role === 'admin' && (
              <button
                type="button"
                className={`nav-tab-button ${currentView === 'admin' ? 'active' : ''}`}
                onClick={() => setCurrentView('admin')}
              >
                Administration
              </button>
            )}
          </nav>
        </div>

        {/* Barre de recherche discrète */}
        <div className="twitch-header-center">
          <div className="twitch-search-box">
            <SearchIcon size={14} className="twitch-search-icon" />
            <input
              type="text"
              className="twitch-search-input"
              placeholder="Rechercher une chaîne..."
              onKeyDown={(e) => {
                if (e.key === 'Enter' && e.target.value.trim()) {
                  handleSelectStream(e.target.value.trim())
                }
              }}
            />
          </div>
        </div>

        {/* Actions utilisateur & connexion */}
        <div className="twitch-header-right">
          {streams.length > 0 && (
            <div className="live-indicator-pill">
              <span className="live-indicator-dot" />
              <span>{streams.length} en direct</span>
            </div>
          )}

          {user ? (
            <>
              {user.role === 'streamer' && (
                <button
                  type="button"
                  className="role-switcher-button"
                  onClick={() => setCurrentView(currentView === 'studio' ? 'live' : 'studio')}
                >
                  {currentView === 'studio' ? 'Mode Spectateur' : 'Studio Diffuseur'}
                </button>
              )}

              <div className="user-profile-capsule">
                <div className="user-avatar-circle">{user.email ? user.email.charAt(0).toUpperCase() : 'U'}</div>
                <span className={`user-role-badge ${user.role || 'viewer'}`}>{user.role || 'viewer'}</span>
                <button
                  type="button"
                  className="logout-icon-button"
                  onClick={handleLogout}
                  title="Déconnexion"
                  aria-label="Déconnexion"
                >
                  <LogOutIcon size={15} />
                </button>
              </div>
            </>
          ) : (
            <div style={{ display: 'flex', gap: '8px' }}>
              <button className="button button-quiet button-small" onClick={() => setAuthMode('login')}>
                Se connecter
              </button>
              <button className="button button-primary button-small" onClick={() => setAuthMode('register')}>
                S'inscrire
              </button>
            </div>
          )}
        </div>
      </header>

      {/* DISPOSITION SIMPLIFIÉE : SIDEBAR À GAUCHE + SCÈNE CENTRALE (PAS DE CHAT) */}
      <div className="twitch-body-container">
        <SimpleSidebar
          streams={streams}
          activeStream={selectedStream}
          onSelectStream={handleSelectStream}
          isCollapsed={sidebarCollapsed}
          onToggleCollapse={() => setSidebarCollapsed(!sidebarCollapsed)}
        />

        {currentView === 'studio' && user?.role === 'streamer' ? (
          <StreamerInterface user={user} />
        ) : currentView === 'admin' && user?.role === 'admin' ? (
          <AdminDashboard user={user} />
        ) : currentView === 'live' ? (
          <ViewerInterface
            user={user}
            selectedStreamId={selectedStream}
            onSelectStream={handleSelectStream}
            streams={streams}
          />
        ) : (
          <TwitchBrowse
            streams={streams}
            onSelectStream={handleSelectStream}
            openAuth={setAuthMode}
          />
        )}
      </div>

      {authMode && (
        <AuthDialog
          mode={authMode}
          setMode={setAuthMode}
          onSuccess={(nextUser) => {
            setUser(nextUser)
            setAuthMode(null)
            if (nextUser.role === 'streamer') setCurrentView('studio')
            else setCurrentView('live')
          }}
          onClose={() => setAuthMode(null)}
        />
      )}
    </div>
  )
}
