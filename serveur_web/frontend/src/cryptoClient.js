/**
 * Module Cryptographique Client-Side conforme à l'article FGCS 2024 :
 * 'A verifiable data integrity scheme for distributed data sharing in fog computing architecture'
 * (Sellami, Imine, Gallais - Future Generation Computer Systems 150, 2024, pp. 64–77).
 *
 * Implémente :
 * 1. Preuve d'intégrité SIS locale (L'Ecuyer generator + vecteurs binaires {0, 1}^m).
 * 2. Vérification publique Hess-IBS directe dans le navigateur (BN254 bilinear pairing).
 * 3. Vérification de la signature déléguée pour le transcodage Edge.
 * 4. Métriques de télémétrie et audit sans tiers de confiance (Public auditability without TPA).
 */

import { bn254 } from '@noble/curves/bn254.js'
import { sha256 } from '@noble/hashes/sha2.js'
import { bytesToHex } from '@noble/hashes/utils.js'

export const { G1, G2, pairing, fields } = bn254
export { bn254 }
const { Fp2, Fp12, Fr, Fp } = fields
export const CURVE_ORDER = Fr.ORDER
export const FIELD_MODULUS = Fp.ORDER
const EXP_RESIDUE = (FIELD_MODULUS - 1n) / 2n
const EXP_SQRT = (FIELD_MODULUS + 1n) / 4n

function modPow(base, exponent, modulus) {
  let result = 1n
  base = base % modulus
  while (exponent > 0n) {
    if (exponent % 2n === 1n) result = (result * base) % modulus
    exponent = exponent / 2n
    base = (base * base) % modulus
  }
  return result
}

// Paramètres SIS définis dans l'article (Tableau 4)
export const SIS_L = 64       // Nombre de lignes de A
export const SIS_M = 1536     // Nombre de colonnes de A (m >= 2 * l * log2(q))
export const SIS_Q = 4099     // Modulo premier
export const DEFAULT_PARAM_A = {
  I0: 1234,
  a: 3141,
  C0: 567,
}

// Cache mémoire pour la matrice A reconstruite
const matrixCache = new Map()

// =====================================================================
// 1. GÉNÉRATEUR UNIFORME L'ECUYER & MATRICE A (Section 3.4.1 / 3.4.3.1)
// =====================================================================

export function generateMatrixA(i0 = 1234, a = 3141, c0 = 567) {
  const cacheKey = `${i0}_${a}_${c0}`
  if (matrixCache.has(cacheKey)) {
    return matrixCache.get(cacheKey)
  }

  const total = SIS_L * SIS_M
  const values = new Int32Array(total)
  let curI = Number(i0)
  let curC = Number(c0)
  const mult = Number(a)
  const mod = SIS_Q

  for (let i = 0; i < total; i++) {
    const comb = mult * curI + curC
    curI = comb % mod
    curC = Math.floor(comb / mod)
    values[i] = curI
  }

  matrixCache.set(cacheKey, values)
  return values
}

// =====================================================================
// 2. VECTEUR BINAIRE x_i ET PREUVE D'INTÉGRITÉ SIS (Section 3.4.3.2)
// =====================================================================

export function hashToBitVector(dataUint8Array) {
  const digest = sha256(dataUint8Array)
  const bits = new Uint8Array(256)
  for (let i = 0; i < 32; i++) {
    const byte = digest[i]
    for (let b = 0; b < 8; b++) {
      bits[i * 8 + b] = (byte >> (7 - b)) & 1
    }
  }

  // Répéter 6 fois (1536 // 256 = 6) pour former x_i in {0, 1}^m
  const x = new Uint8Array(SIS_M)
  for (let rep = 0; rep < 6; rep++) {
    x.set(bits, rep * 256)
  }
  return x
}

export function computeSisVector(dataUint8Array, paramA = DEFAULT_PARAM_A) {
  const i0 = paramA?.I0 ?? DEFAULT_PARAM_A.I0
  const a = paramA?.a ?? DEFAULT_PARAM_A.a
  const c0 = paramA?.C0 ?? DEFAULT_PARAM_A.C0

  const matrixA = generateMatrixA(i0, a, c0)
  const x = hashToBitVector(dataUint8Array)
  const v = new Int32Array(SIS_L)

  for (let r = 0; r < SIS_L; r++) {
    let sum = 0
    const rowOffset = r * SIS_M
    for (let c = 0; c < SIS_M; c++) {
      if (x[c] === 1) {
        sum += matrixA[rowOffset + c]
      }
    }
    v[r] = sum % SIS_Q
  }

  return Array.from(v)
}

// =====================================================================
// 3. SÉRIALISATION & HACHAGE (H1, H2)
// =====================================================================

export function canonicalVRepr(vData) {
  if (Array.isArray(vData)) {
    if (vData.length > 0 && Array.isArray(vData[0])) {
      return '[' + vData.map(col => '[' + col.map(x => String(Math.floor(x))).join(',') + ']').join(',') + ']'
    }
    return '[' + vData.map(x => String(Math.floor(x))).join(',') + ']'
  }
  return String(vData)
}

export function canonicalParamARepr(paramA) {
  const i0 = paramA?.I0 ?? DEFAULT_PARAM_A.I0
  const a = paramA?.a ?? DEFAULT_PARAM_A.a
  const c0 = paramA?.C0 ?? DEFAULT_PARAM_A.C0
  return `${i0},${a},${c0}`
}

export function serializeFp12(fp12) {
  return [
    fp12.c0.c0.c0, fp12.c0.c0.c1, fp12.c0.c1.c0, fp12.c0.c1.c1, fp12.c0.c2.c0, fp12.c0.c2.c1,
    fp12.c1.c0.c0, fp12.c1.c0.c1, fp12.c1.c1.c0, fp12.c1.c1.c1, fp12.c1.c2.c0, fp12.c1.c2.c1
  ].map(x => x.toString()).join(',')
}

export function hashH1ToG1(identity) {
  const cleanId = String(identity || '').trim()
  const baseMsg = new TextEncoder().encode(cleanId)
  let counter = 0
  while (true) {
    const counterBytes = new Uint8Array(4)
    new DataView(counterBytes.buffer).setUint32(0, counter, false) // Big endian
    const combined = new Uint8Array(baseMsg.length + 4)
    combined.set(baseMsg)
    combined.set(counterBytes, baseMsg.length)
    const digest = sha256(combined)
    let x = 0n
    for (let i = 0; i < 32; i++) {
      x = (x << 8n) | BigInt(digest[i])
    }
    x = x % FIELD_MODULUS
    const rhs = (x * x * x + 3n) % FIELD_MODULUS
    // Test du résidu quadratique via le critère d'Euler
    if (modPow(rhs, EXP_RESIDUE, FIELD_MODULUS) === 1n) {
      let y = modPow(rhs, EXP_SQRT, FIELD_MODULUS)
      // Normalisation déterministe : choix de la racine paire
      if (y % 2n !== 0n) {
        y = FIELD_MODULUS - y
      }
      try {
        const pt = new G1.Point(x, y, 1n)
        pt.assertValidity()
        return pt
      } catch (e) {
        // Point non valide sur la courbe, incrémenter
      }
    }
    counter++
  }
}

export function legacyHashH1ToG1(identity) {
  const cleanId = String(identity || '').trim()
  const digest = sha256(new TextEncoder().encode(cleanId))
  const scalar = (BigInt('0x' + bytesToHex(digest)) % (CURVE_ORDER - 1n)) + 1n
  return G1.Point.BASE.multiply(scalar)
}

export function hashH2(vRepr, paramARepr, fp12) {
  const msg = `${vRepr}|${paramARepr}|${serializeFp12(fp12)}`
  const digest = sha256(new TextEncoder().encode(msg))
  const challenge = BigInt('0x' + bytesToHex(digest)) % CURVE_ORDER
  return challenge === 0n ? 1n : challenge
}

// =====================================================================
// 4. CONVERSIONS G1 / G2 JSON
// =====================================================================

export function jsonToG1(coords) {
  if (coords instanceof G1.Point) return coords
  if (Array.isArray(coords) && coords.length === 2) {
    return new G1.Point(BigInt(coords[0]), BigInt(coords[1]), 1n)
  }
  throw new Error('Point G1 invalide (attendu [x, y])')
}

export function g1ToJson(pt) {
  const affine = pt.toAffine()
  return [affine.x.toString(), affine.y.toString()]
}

export function jsonToG2(coords) {
  if (coords instanceof G2.Point) return coords
  if (Array.isArray(coords) && coords.length === 2 && Array.isArray(coords[0]) && Array.isArray(coords[1])) {
    const x = { c0: BigInt(coords[0][0]), c1: BigInt(coords[0][1]) }
    const y = { c0: BigInt(coords[1][0]), c1: BigInt(coords[1][1]) }
    return new G2.Point(x, y, Fp2.ONE)
  }
  throw new Error('Point G2 invalide (attendu [[x0, x1], [y0, y1]])')
}

export function g2ToJson(pt) {
  const affine = pt.toAffine()
  return [
    [affine.x.c0.toString(), affine.x.c1.toString()],
    [affine.y.c0.toString(), affine.y.c1.toString()]
  ]
}

// =====================================================================
// 5. SIGNATURE & VÉRIFICATION HESS-IBS (Section 3.4.3 & 3.4.4)
// =====================================================================

export function signHessIBS(vData, secretKeyG1, paramA = DEFAULT_PARAM_A) {
  const randBytes = new Uint8Array(32)
  if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
    crypto.getRandomValues(randBytes)
  } else {
    for (let i = 0; i < 32; i++) randBytes[i] = Math.floor(Math.random() * 256)
  }
  const k = (BigInt('0x' + bytesToHex(sha256(randBytes))) % (CURVE_ORDER - 1n)) + 1n

  const p1 = G1.Point.BASE
  const kP1 = p1.multiply(k)
  const r = pairing(kP1, G2.Point.BASE)

  const vRepr = canonicalVRepr(vData)
  const paramRepr = canonicalParamARepr(paramA)
  const w1 = hashH2(vRepr, paramRepr, r)

  const skPoint = jsonToG1(secretKeyG1)
  const w2 = skPoint.multiply(w1).add(kP1)

  return {
    scheme: 'Hess-IBS',
    w1: w1.toString(),
    w2: g1ToJson(w2),
    p1: g1ToJson(p1)
  }
}

export function verifyHessIBS(vData, signature, ownerId, zonePk, paramA = DEFAULT_PARAM_A) {
  try {
    const w1 = BigInt(signature.w1)
    const w2 = jsonToG1(signature.w2)
    const pkPoint = jsonToG2(zonePk)

    const vRepr = canonicalVRepr(vData)
    const paramRepr = canonicalParamARepr(paramA)

    const h1Owner = hashH1ToG1(ownerId)
    const negH1Scaled = h1Owner.negate().multiply(w1 % CURVE_ORDER)

    const term1 = pairing(w2, G2.Point.BASE)
    const term2 = pairing(negH1Scaled, pkPoint)
    const rPrime = Fp12.mul(term1, term2)

    const expectedW1 = hashH2(vRepr, paramRepr, rPrime)
    if (expectedW1 === w1) {
      return { valid: true, detail: 'Signature Hess-IBS vérifiée avec succès' }
    }

    // Tolérance de transition : test avec l'ancienne dérivation scalaire H1 (flux non rafraîchis)
    const legH1 = legacyHashH1ToG1(ownerId)
    const legNegH1Scaled = legH1.negate().multiply(w1 % CURVE_ORDER)
    const legTerm2 = pairing(legNegH1Scaled, pkPoint)
    const legRPrime = Fp12.mul(term1, legTerm2)
    if (hashH2(vRepr, paramRepr, legRPrime) === w1) {
      return { valid: true, detail: 'Signature Hess-IBS vérifiée avec succès (mode transition)' }
    }

    return { valid: false, detail: 'Échec du challenge cryptographique H2 (w1 !== w1_prime)' }
  } catch (err) {
    return { valid: false, detail: `Erreur vérification Hess-IBS : ${err.message}` }
  }
}

/**
 * Génération de la clé de délégation par l'Owner vers un nœud Fog (Section 3.6.1 de l'article).
 * y in Z_p*, PKd = y * G2
 * DKd = Sw + y * H1(IDd) in G1
 */
export function generateDelegationKey(ownerSecretKeyJson, delegateId) {
  const Sw = jsonToG1(ownerSecretKeyJson)
  const randBytes = new Uint8Array(32)
  if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
    crypto.getRandomValues(randBytes)
  } else {
    for (let i = 0; i < 32; i++) randBytes[i] = Math.floor(Math.random() * 256)
  }
  const y = (BigInt('0x' + bytesToHex(sha256(randBytes))) % (CURVE_ORDER - 1n)) + 1n

  const pkD = G2.Point.BASE.multiply(y)
  const h1Delegate = hashH1ToG1(delegateId)
  const yH1 = h1Delegate.multiply(y)
  const dkD = Sw.add(yH1)

  return {
    delegationKey: g1ToJson(dkD),
    delegatePk: g2ToJson(pkD),
  }
}

export function verifyDelegatedHessIBS(vData, signature, ownerId, zonePk, delegateId, delegatePk, paramA = DEFAULT_PARAM_A) {
  try {
    const w1 = BigInt(signature.w1)
    const w2 = jsonToG1(signature.w2)
    const pkPoint = jsonToG2(zonePk)
    const delPkPoint = jsonToG2(delegatePk)

    const vRepr = canonicalVRepr(vData)
    const paramRepr = canonicalParamARepr(paramA)

    const h1Owner = hashH1ToG1(ownerId)
    const negH1OwnerScaled = h1Owner.negate().multiply(w1 % CURVE_ORDER)

    const h1Delegate = hashH1ToG1(delegateId)
    const negH1DelegateScaled = h1Delegate.negate().multiply(w1 % CURVE_ORDER)

    const term1 = pairing(w2, G2.Point.BASE)
    const term2 = pairing(negH1OwnerScaled, pkPoint)
    const term3 = pairing(negH1DelegateScaled, delPkPoint)
    const rPrime = Fp12.mul(Fp12.mul(term1, term2), term3)

    const expectedW1 = hashH2(vRepr, paramRepr, rPrime)
    if (expectedW1 === w1) {
      return { valid: true, detail: 'Signature déléguée Fog vérifiée avec succès' }
    }

    // Tolérance de transition : test avec l'ancienne dérivation scalaire H1
    const legH1Owner = legacyHashH1ToG1(ownerId)
    const legNegOwner = legH1Owner.negate().multiply(w1 % CURVE_ORDER)
    const legH1Del = legacyHashH1ToG1(delegateId)
    const legNegDel = legH1Del.negate().multiply(w1 % CURVE_ORDER)
    const legTerm2 = pairing(legNegOwner, pkPoint)
    const legTerm3 = pairing(legNegDel, delPkPoint)
    const legRPrime = Fp12.mul(Fp12.mul(term1, legTerm2), legTerm3)
    if (hashH2(vRepr, paramRepr, legRPrime) === w1) {
      return { valid: true, detail: 'Signature déléguée Fog vérifiée avec succès (mode transition)' }
    }

    return { valid: false, detail: 'Échec du challenge cryptographique H2 délégué' }
  } catch (err) {
    return { valid: false, detail: `Erreur vérification déléguée : ${err.message}` }
  }
}

// =====================================================================
// 6. PIPELINE D'AUDIT COMPLET SANS TPA (Section 3.4.4)
// =====================================================================

export function verifyIncomingBlock(chunkBytes, packet, defaultZonePk = null) {
  const t0 = performance.now()

  const paramA = packet.paramA || DEFAULT_PARAM_A
  const expectedV = packet.V_column || packet.V_matrix || []
  const signature = packet.signature || null
  const signerId = packet.signer_id || packet.owner_email || 'streamer@pledge.local'
  const ownerIdentity = packet.owner_identity || packet.owner_id || signerId
  const isDelegated = Boolean(packet.is_delegated)
  const zonePk = packet.zone_pk || defaultZonePk

  // 1. Calcul et vérification SIS locale
  const tSis0 = performance.now()
  const localV = computeSisVector(chunkBytes, paramA)
  const tSis1 = performance.now()
  const sisTimeMs = tSis1 - tSis0

  let isSisValid = true
  if (expectedV && expectedV.length > 0) {
    if (localV.length !== expectedV.length) {
      isSisValid = false
    } else {
      for (let i = 0; i < localV.length; i++) {
        if (localV[i] !== expectedV[i]) {
          isSisValid = false
          break
        }
      }
    }
  }

  // 2. Vérification cryptographique Hess-IBS (couplage bilinéaire)
  let isSigValid = true
  let sigDetail = 'Aucune signature'
  let sigTimeMs = 0

  if (signature && signature.w1 && signature.w2 && zonePk) {
    const tSig0 = performance.now()
    if (isDelegated && packet.delegate_id && packet.delegate_pk) {
      const res = verifyDelegatedHessIBS(
        localV,
        signature,
        ownerIdentity,
        zonePk,
        packet.delegate_id,
        packet.delegate_pk,
        paramA
      )
      isSigValid = res.valid
      sigDetail = res.detail
    } else {
      const res = verifyHessIBS(localV, signature, signerId, zonePk, paramA)
      isSigValid = res.valid
      sigDetail = res.detail
    }
    const tSig1 = performance.now()
    sigTimeMs = tSig1 - tSig0
  }

  const tEnd = performance.now()
  const totalTimeMs = tEnd - t0

  const isValid = isSisValid && isSigValid

  return {
    isValid,
    isSisValid,
    isSigValid,
    isDelegated,
    sisTimeMs,
    sigTimeMs,
    totalTimeMs,
    detail: !isSisValid
      ? 'Échec vérification SIS : altération détectée dans le bloc vidéo !'
      : !isSigValid
      ? `Échec signature : ${sigDetail}`
      : isDelegated
      ? 'Transcodé légitime (preuve SIS & signature déléguée validées)'
      : 'Intègre (preuve SIS & signature Hess-IBS validées)',
    localV,
    expectedV,
  }
}

// =====================================================================
// 7. UTILITAIRES D'ENCODAGE & GESTION DE CLÉS CLIENT
// =====================================================================

export function uint8ArrayToBase64(bytes) {
  let binary = ''
  const len = bytes.byteLength
  for (let i = 0; i < len; i++) {
    binary += String.fromCharCode(bytes[i])
  }
  return btoa(binary)
}

export function base64ToUint8Array(b64) {
  const binary = atob(b64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i)
  }
  return bytes
}

/**
 * Récupère ou dérive de façon déterministe la part secrète de l'utilisateur Sw = ts * H1(IDw)
 * et la clé publique de zone PK = ts * G2 pour le diffuseur.
 */
export function deriveStreamerKey(identity, masterScalar = 123456789n) {
  let ts
  if (typeof masterScalar === 'string' && ZONE_MASTER_SCALARS[masterScalar]) {
    ts = BigInt(ZONE_MASTER_SCALARS[masterScalar])
  } else {
    ts = BigInt(masterScalar)
  }
  const zonePkPoint = G2.Point.BASE.multiply(ts)
  const zonePk = g2ToJson(zonePkPoint)
  const h1Owner = hashH1ToG1(identity)
  const swPoint = h1Owner.multiply(ts)
  const secretKey = g1ToJson(swPoint)
  return { secretKey, zonePk, identity }
}

// =====================================================================
// 8. MOBILITÉ MULTI-ZONE & ACCUMULATION HOMOMORPHE (Section 3.4)
// =====================================================================

export const ZONE_MASTER_SCALARS = {
  Zone_1: 123456789n,
  Zone_2: 987654321n,
}

export function getZoneMasterScalar(zoneId) {
  if (zoneId && ZONE_MASTER_SCALARS[zoneId]) {
    return ZONE_MASTER_SCALARS[zoneId]
  }
  return ZONE_MASTER_SCALARS.Zone_1
}

/**
 * Accumulation homomorphe des parts secrètes dans G1 (Section 3.4.2 & 3.4.3) :
 * S_w <- sum(S_w^k)
 */
export function accumulateSecretKeys(keys) {
  if (!keys || keys.length === 0) return null
  let total = jsonToG1(keys[0])
  for (let i = 1; i < keys.length; i++) {
    total = total.add(jsonToG1(keys[i]))
  }
  return g1ToJson(total)
}

/**
 * Accumulation homomorphe des clés publiques de zones dans G2 (Section 3.4.2 & 3.4.3) :
 * PK <- sum(PK_k)
 */
export function accumulatePublicKeys(pks) {
  if (!pks || pks.length === 0) return null
  let total = jsonToG2(pks[0])
  for (let i = 1; i < pks.length; i++) {
    total = total.add(jsonToG2(pks[i]))
  }
  return g2ToJson(total)
}

/**
 * Addition homomorphe de clés entre deux zones (S_w <- S_w + S_w^k, PK <- PK + PK_k).
 */
export function accumulateHomomorphicKeys(existingCreds, newZoneCreds) {
  const swPoint1 = jsonToG1(existingCreds.secretKey)
  const swPoint2 = jsonToG1(newZoneCreds.secretKey)
  const accumulatedSw = g1ToJson(swPoint1.add(swPoint2))

  const pkPoint1 = jsonToG2(existingCreds.zonePk)
  const pkPoint2 = jsonToG2(newZoneCreds.zonePk)
  const accumulatedPk = g2ToJson(pkPoint1.add(pkPoint2))

  return {
    secretKey: accumulatedSw,
    zonePk: accumulatedPk,
  }
}

/**
 * Client de gestion de mobilité multi-zone pour le diffuseur (Data Owner Ow).
 * Implémente la Section 3.4 du papier Sellami et al. (FGCS 2024).
 */
export class ClientMobilityManager {
  constructor(identity, initialZone = 'Zone_1') {
    this.identity = String(identity || '').trim()
    this.visitedZones = new Set()
    this.secretKey = null
    this.zonePk = null
    this.currentZone = initialZone || 'Zone_1'
    if (initialZone) {
      this.addZone(initialZone)
    }
  }

  addZone(zoneId) {
    const cleanZone = zoneId || 'Zone_1'
    this.currentZone = cleanZone
    if (this.visitedZones.has(cleanZone)) {
      return this.getCredentials()
    }

    const scalar = getZoneMasterScalar(cleanZone)
    const newCreds = deriveStreamerKey(this.identity, scalar)
    this.visitedZones.add(cleanZone)

    if (!this.secretKey) {
      this.secretKey = newCreds.secretKey
      this.zonePk = newCreds.zonePk
    } else {
      const accumulated = accumulateHomomorphicKeys(
        { secretKey: this.secretKey, zonePk: this.zonePk },
        newCreds
      )
      this.secretKey = accumulated.secretKey
      this.zonePk = accumulated.zonePk
    }

    return this.getCredentials()
  }

  getCredentials() {
    return {
      identity: this.identity,
      secretKey: this.secretKey,
      zonePk: this.zonePk,
      visitedZones: Array.from(this.visitedZones),
      currentZone: this.currentZone,
    }
  }
}
