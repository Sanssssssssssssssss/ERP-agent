import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { build } from 'esbuild'

const { outputFiles } = await build({ entryPoints: [fileURLToPath(new URL('../src/renderer/features/traces/trace-model.ts', import.meta.url))], bundle: true, write: false, format: 'esm', platform: 'node' })
const { traceNodes } = await import(`data:text/javascript;base64,${Buffer.from(outputFiles[0].text).toString('base64')}`)
const node = event => traceNodes({ run: null, rounds: [], tools: [], events: [event] })[0]
assert.equal(node({ type: 'auto_retry_start', error: 'previous request failed' }).status, 'retrying')
assert.equal(node({ type: 'auto_retry_end', status: 'retried', success: true }).status, 'retried')
assert.equal(node({ type: 'auto_retry_end', status: 'retried', success: true }).issue, false)
assert.equal(node({ type: 'auto_retry_end', status: 'failed', success: false }).status, 'failed')
assert.equal(node({ type: 'auto_retry_end' }).status, 'unknown')
assert.equal(node({ type: 'auto_retry_end', success: true }).status, 'unknown')
assert.equal(node({ type: 'compaction_start' }).status, 'running')
assert.equal(node({ type: 'compaction_end', status: 'completed' }).status, 'completed')
assert.equal(node({ type: 'compaction_end', success: true }).status, 'completed')
assert.equal(node({ type: 'compaction_end', aborted: true }).status, 'interrupted')
assert.equal(node({ type: 'compaction_end', error: 'provider error' }).status, 'failed')
assert.equal(node({ type: 'compaction_end', aborted: false, error: null }).status, 'unknown')
console.log('Trace event checks passed: explicit terminal status, retry start/end, failed retry, compaction abort/error and unknown completion. No API or browser calls.')
