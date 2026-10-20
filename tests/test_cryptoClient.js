import assert from 'node:assert'
import {
  SIS_L,
  SIS_M,
  SIS_Q,
  DEFAULT_PARAM_A,
  generateMatrixA,
  hashToBitVector,
  computeSisVector,
  hashH1ToG1,
  signHessIBS,
  verifyHessIBS,
  verifyDelegatedHessIBS,
  verifyIncomingBlock,
  g1ToJson,
  g2ToJson,
  CURVE_ORDER,
  G1,
  G2,
} from '../serveur_web/frontend/src/cryptoClient.js'

console.log('=== DÉBUT DES TESTS UNITAIRES FRONTEND CRYPTO (PHASE 5) ===')

// Test 1: Génération déterministe de la matrice A
console.log('[Test 1] Génération déterministe de la matrice A (L\'Ecuyer 1994)...')
const matrix1 = generateMatrixA(1234, 3141, 567)
const matrix2 = generateMatrixA(1234, 3141, 567)
assert.strictEqual(matrix1.length, SIS_L * SIS_M)
for (let i = 0; i < matrix1.length; i++) {
  assert.strictEqual(matrix1[i], matrix2[i])
  assert(matrix1[i] >= 0 && matrix1[i] < SIS_Q)
}
console.log('  -> Succès : Matrice A 64x1536 correctement reconstruite en O(1).')

// Test 2: Vecteur binaire strictement {0, 1}^m
console.log('[Test 2] Vecteur binaire x_i in {0, 1}^m...')
const blockBytes = new TextEncoder().encode('Test video block content 42')
const xVector = hashToBitVector(blockBytes)
assert.strictEqual(xVector.length, SIS_M)
for (let i = 0; i < xVector.length; i++) {
  assert(xVector[i] === 0 || xVector[i] === 1)
}
console.log('  -> Succès : x_i est un vecteur binaire de dimension 1536 (SHA-256 répété 6x).')

// Test 3: Calcul de la preuve SIS
console.log('[Test 3] Calcul SIS local v_i = (A * x_i) mod q...')
const v1 = computeSisVector(blockBytes)
assert.strictEqual(v1.length, SIS_L)
for (let i = 0; i < v1.length; i++) {
  assert(v1[i] >= 0 && v1[i] < SIS_Q)
}
const v1Bis = computeSisVector(blockBytes)
assert.deepStrictEqual(v1, v1Bis)
const differentBlock = new TextEncoder().encode('Modified block content!')
const v2 = computeSisVector(differentBlock)
assert.notDeepStrictEqual(v1, v2)
console.log('  -> Succès : Preuve SIS v_i calculée avec résistance aux collisions.')

// Test 4: Cycle complet de signature & vérification Hess-IBS
console.log('[Test 4] Cycle complet Hess-IBS (Setup, Sign, Public Verify)...')
const masterScalar = 987654321n
const zonePkPoint = G2.Point.BASE.multiply(masterScalar)
const zonePk = g2ToJson(zonePkPoint)

const ownerId = 'alice@pledge.local'
const h1Owner = hashH1ToG1(ownerId)
const secretSharePoint = h1Owner.multiply(masterScalar)
const secretShare = g1ToJson(secretSharePoint)

const signature = signHessIBS(v1, secretShare)
assert.strictEqual(signature.scheme, 'Hess-IBS')
assert(signature.w1 && signature.w2)

const verifResult = verifyHessIBS(v1, signature, ownerId, zonePk)
assert.strictEqual(verifResult.valid, true)
console.log('  -> Succès : Vérification publique Hess-IBS confirmée.')

// Test 5: Détection de corruption (Preuve SIS altérée)
console.log('[Test 5] Détection de corruption de trame...')
const corruptedResult = verifyHessIBS(v2, signature, ownerId, zonePk)
assert.strictEqual(corruptedResult.valid, false)

const fakeIdResult = verifyHessIBS(v1, signature, 'mallory@evil.com', zonePk)
assert.strictEqual(fakeIdResult.valid, false)
console.log('  -> Succès : Altérations et usurpations d\'identité immédiatement rejetées.')

// Test 6: Signature déléguée pour le Fog
console.log('[Test 6] Vérification de signature déléguée Fog (Transcodage)...')
const delegateId = 'Edge_Node_Zone1_Transcoder'
const randomY = 44444n
const delegatePkPoint = G2.Point.BASE.multiply(randomY)
const delegatePk = g2ToJson(delegatePkPoint)
const delegationKeyPoint = secretSharePoint.add(hashH1ToG1(delegateId).multiply(randomY))
const delegationKey = g1ToJson(delegationKeyPoint)

// Le Fog transcode le bloc et le signe avec DKd
const transcodedBlock = new TextEncoder().encode('Transcoded 720p frame')
const vTranscoded = computeSisVector(transcodedBlock)
const sigDelegated = signHessIBS(vTranscoded, delegationKey)

const delVerif = verifyDelegatedHessIBS(
  vTranscoded,
  sigDelegated,
  ownerId,
  zonePk,
  delegateId,
  delegatePk
)
assert.strictEqual(delVerif.valid, true)

// Mauvais délégué doit échouer
const badDelVerif = verifyDelegatedHessIBS(
  vTranscoded,
  sigDelegated,
  ownerId,
  zonePk,
  'Rogue_Node_666',
  delegatePk
)
assert.strictEqual(badDelVerif.valid, false)
console.log('  -> Succès : Signature déléguée validée et vérifiée avec 3 accouplements.')

// Test 7: Pipeline de bloc complet
console.log('[Test 7] Audit d\'un paquet bloc vidéo reçu par le viewer...')
const packetValid = {
  block_index: 10,
  V_column: v1,
  signature: signature,
  signer_id: ownerId,
  zone_pk: zonePk,
  paramA: DEFAULT_PARAM_A,
}
const auditValid = verifyIncomingBlock(blockBytes, packetValid)
assert.strictEqual(auditValid.isValid, true)
assert.strictEqual(auditValid.isSisValid, true)
assert.strictEqual(auditValid.isSigValid, true)
console.log(`  -> Temps SIS: ${auditValid.sisTimeMs.toFixed(2)} ms | Temps Sig: ${auditValid.sigTimeMs.toFixed(2)} ms | Total: ${auditValid.totalTimeMs.toFixed(2)} ms`)

// Altération d'un seul octet dans le bloc
const tamperedBytes = new Uint8Array(blockBytes)
tamperedBytes[0] ^= 0x01
const auditTampered = verifyIncomingBlock(tamperedBytes, packetValid)
assert.strictEqual(auditTampered.isValid, false)
assert.strictEqual(auditTampered.isSisValid, false)
console.log('  -> Succès : Altération d\'un seul bit détectée par le viewer local !')

console.log('=== TOUS LES TESTS DU MODULE FRONTEND PASSENT AVEC SUCCÈS (7/7) ===')

