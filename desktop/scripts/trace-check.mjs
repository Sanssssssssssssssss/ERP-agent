import assert from 'node:assert/strict'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { resolve } from 'node:path'
import { build } from 'esbuild'
import { chromium } from 'playwright'

const desktop = fileURLToPath(new URL('../', import.meta.url))
const output = resolve(desktop, '../.runtime/run-experience-20260923/trace-check')
mkdirSync(output, { recursive: true })
const fixture = `
import { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { TracePage } from './src/renderer/features/traces/TracePage'
const run = { id:'run-check', session_id:'session-check', business_id:'business-check', status:'running', started_at:'2026-09-23T00:00:00Z', model_rounds:80, tool_count:81 }
const rounds = Array.from({length:80}, (_,i)=>({ index:i+1,status:'completed',text:'公开记录 '+(i+1)+'\\n\\n'+('只展示公开业务回复。\\n\\n'.repeat(120)),tool_ids:['tool-'+(i+1)] }))
const tools = rounds.map(row=>({id:'tool-'+row.index,name:'read_order_'+row.index,round:row.index,status:'completed',arguments:{name:'S01499'},result:{text:'业务材料'.repeat(8000),rows:Array.from({length:80},(_,i)=>({id:i,name:'记录'}))}}))
tools.push({id:'in-flight',name:'query_current_order',round:81,status:'running',arguments:{name:'S01499'}})
const initial = { trace:{run,rounds,tools,events:[{type:'tool_start',tool_call_id:'in-flight',tool_name:'query_current_order'},{type:'tool_start',tool_call_id:'event-only',tool_name:'receipt_pending'},{type:'tool_end',tool_call_id:'event-only',tool_name:'receipt_pending'}]},runs:[run],selectedRunId:run.id,loading:false,target:null,liveMessages:[] }
function App(){const [props,setProps]=useState(initial);window.__tracePatch=patch=>setProps(old=>({...old,...patch}));window.__traceData=props;return <div className="app-shell"><header className="window-bar">运行详情测试</header><main className="business-workspace"><div className="business-tabs-bar">已选业务</div><header className="business-header"><h2>确认销售订单 S01499</h2></header><div className="business-tabs-root"><div className="business-page-tabs">运行详情</div><div className="business-content"><div data-state="active"><TracePage {...props} onRunSelect={()=>{}} /></div></div></div></main></div>}
createRoot(document.getElementById('root')).render(<App />)
`
const { outputFiles } = await build({ stdin: { contents: fixture, resolveDir: desktop, loader: 'tsx' }, bundle: true, write: false, format: 'iife', platform: 'browser', jsx: 'automatic', define: { 'process.env.NODE_ENV': '"production"' } })
const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ viewport: { width: 1366, height: 900 } })
const errors = []
page.on('pageerror', error => errors.push(error.message))
await page.route('**/*', route => route.abort())
try {
  await page.setContent('<html lang="zh-CN"><body><div id="root"></div></body></html>')
  await page.addStyleTag({ content: readFileSync(resolve(desktop, 'src/renderer/styles.css'), 'utf8') })
  await page.addScriptTag({ content: outputFiles[0].text })
  await page.locator('.trace-tree').waitFor()
  const node = title => page.locator('.trace-tree button').filter({ has: page.locator('strong', { hasText: new RegExp('^' + title + '$') }) })
  const layout = async () => page.evaluate(() => {
    const measure = selector => { const e = document.querySelector(selector); return { height:e.clientHeight, scroll:e.scrollHeight, width:e.clientWidth, scrollWidth:e.scrollWidth, bottom:e.getBoundingClientRect().bottom } }
    return { page:measure('.trace-page'), content:measure('.business-content'), tree:measure('.trace-tree'), detail:measure('.trace-detail-panel'), height:innerHeight }
  })
  for (const [width,height] of [[1366,900],[768,640],[420,720]]) {
    await page.setViewportSize({width,height})
    const dimensions = await layout()
    assert.ok(dimensions.tree.scroll > dimensions.tree.height, 'round tree must have its own scroll area')
    assert.ok(dimensions.content.scroll <= dimensions.content.height + 1, 'trace must not stretch its parent')
    assert.ok(dimensions.page.bottom <= dimensions.height + 1, 'trace must stay inside viewport')
    assert.ok(dimensions.detail.scrollWidth <= dimensions.detail.width + 1, 'detail must not overflow horizontally')
    await page.screenshot({path:resolve(output, 'trace-'+width+'.png')})
  }
  await page.setViewportSize({width:1366,height:900})
  await node('query_current_order').click()
  assert.equal(await page.locator('.trace-detail-panel h3').innerText(), 'query_current_order')
  assert.match(await node('receipt_pending').innerText(), /已结束 · 回执未到达/)
  await node('receipt_pending').click()
  assert.match(await page.locator('.trace-detail-panel').innerText(), /工具已结束，完整回执尚未到达/)
  await node('read_order_80').click()
  const json = await page.locator('.trace-detail-panel pre').last().evaluate(e=>({height:e.clientHeight,scroll:e.scrollHeight,width:e.clientWidth,scrollWidth:e.scrollWidth}))
  assert.ok(json.scroll > json.height && json.height <= 320, 'large JSON must scroll independently')
  assert.ok(json.scrollWidth <= json.width + 1, 'long values must wrap')
  await node('第 1 轮').click()
  await page.evaluate(()=>window.__tracePatch({liveMessages:[{id:'live-other',run_id:'other-run',text:'其他运行不能出现',role:'assistant',status:'streaming',sequence:1},{id:'live-now',run_id:'run-check',text:'正在核对 S01499。',role:'assistant',status:'streaming',sequence:1}]}))
  await page.waitForFunction(()=>document.querySelector('.trace-tree').textContent.includes('公开回复'))
  assert.equal(await page.locator('.trace-detail-panel h3').innerText(), '第 1 轮', 'public delta must not steal historical selection')
  await page.getByRole('button',{name:'跟随最新',exact:true}).click()
  await page.waitForFunction(()=>document.querySelector('.trace-detail-panel h3').textContent==='公开回复')
  assert.match(await page.locator('.trace-detail-panel').innerText(), /正在核对 S01499。/)
  await page.evaluate(()=>window.__tracePatch({liveMessages:[{id:'live-now',run_id:'run-check',text:'正在核对 S01499。已读取客户与金额。',role:'assistant',status:'streaming',sequence:2}]}))
  await page.waitForFunction(()=>document.querySelector('.trace-detail-panel').textContent.includes('已读取客户与金额'))
  assert.equal((await page.locator('.trace-page').innerText()).includes('其他运行不能出现'), false)
  await page.locator('.trace-detail-panel').dispatchEvent('wheel',{deltaY:-100})
  assert.equal(await page.getByRole('button',{name:'跟随最新',exact:true}).getAttribute('aria-pressed'),'false')
  await node('第 2 轮').click()
  const beforeScroll = await page.locator('.trace-detail-panel').evaluate(e=>{e.scrollTop=200;return e.scrollTop})
  await page.evaluate(()=>window.__tracePatch({liveMessages:[{id:'live-now',run_id:'run-check',text:'公开回复继续更新。',role:'assistant',status:'streaming',sequence:3}]}))
  assert.equal(await page.locator('.trace-detail-panel h3').innerText(),'第 2 轮')
  assert.equal(await page.locator('.trace-detail-panel').evaluate(e=>e.scrollTop),beforeScroll)
  await page.getByRole('button',{name:'跳到最新',exact:true}).click()
  assert.equal(await page.locator('.trace-detail-panel h3').innerText(),'公开回复')
  await page.emulateMedia({reducedMotion:'reduce'})
  assert.equal(await page.locator('.trace-stream-caret').evaluate(e=>getComputedStyle(e).animationName),'none')
  await page.getByRole('button',{name:'跟随最新',exact:true}).click()
  await page.evaluate(()=>window.__tracePatch({liveMessages:[],trace:{...window.__traceData.trace,rounds:[...window.__traceData.trace.rounds,{index:81,status:'completed',text:'订单已核对。',tool_ids:['in-flight']}],events:[{type:'round_end',round:81}],tools:window.__traceData.trace.tools.map(t=>t.id==='in-flight'?{...t,status:'completed',action_id:'approved-action',result:{ok:true}}:t)}}))
  await page.waitForFunction(()=>document.querySelector('.trace-detail-panel h3').textContent==='第 81 轮')
  await page.evaluate(()=>window.__tracePatch({target:{actionId:'approved-action'}}))
  await page.waitForFunction(()=>document.querySelector('.trace-detail-panel h3').textContent==='query_current_order')
  assert.match(await page.locator('.trace-detail-panel').innerText(),/approved-action/)
  await page.evaluate(()=>window.__tracePatch({target:{toolId:'missing-tool'}}))
  await page.waitForFunction(()=>document.querySelector('.trace-detail-panel h3').textContent==='工具回执不可用')
  await page.evaluate(()=>window.__tracePatch({target:{kind:'readback',runId:'run-check'},readback:{latest_run_id:'run-check',observed_at:'2026-09-23T00:00:00Z',checks:[]}}))
  await page.waitForFunction(()=>document.querySelector('.trace-detail-panel h3').textContent==='独立回读快照')
  assert.match(await page.locator('.trace-detail-panel').innerText(),/独立 Odoo 回读快照/)
  await page.evaluate(()=>window.__tracePatch({target:null,trace:{...window.__traceData.trace,run:{...window.__traceData.trace.run,status:'completed'}}}))
  await page.waitForFunction(()=>document.querySelector('.trace-detail-panel h3').textContent==='运行总览')
  assert.equal(await page.locator('.trace-stream-caret').count(),0)
  await page.getByRole('button',{name:'跟随最新',exact:true}).click()
  await page.evaluate(()=>window.__tracePatch({trace:{...window.__traceData.trace,run:{...window.__traceData.trace.run,id:'other-run'}}}))
  await page.waitForFunction(()=>document.querySelector('.trace-detail-panel .section-heading > span').textContent==='other-run' && document.querySelector('.trace-detail-panel h3').textContent==='运行总览')
  assert.equal(await page.getByRole('button',{name:'跟随最新',exact:true}).getAttribute('aria-pressed'),'false')
  assert.deepEqual(errors,[])
  const summary = 'PASS: 80 rounds; bounded 1366/768/420 layouts; independent JSON scroll; unfinished-round tools; ended-event truth; public deltas scoped to run; follow/pause/history position; reduced motion; missing receipts; readback target; terminal state. No API or ERP calls.'
  writeFileSync(resolve(output,'result.log'),summary+'\n')
  console.log(summary)
} finally { await browser.close() }
