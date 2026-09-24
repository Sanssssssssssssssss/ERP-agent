import assert from 'node:assert/strict'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { resolve } from 'node:path'
import { build } from 'esbuild'
import { chromium } from 'playwright'

const desktop = fileURLToPath(new URL('../', import.meta.url))
const output = resolve(desktop, '../.runtime/trace-inspector-20260923/trace-check')
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
window.__detailCalls=[]
window.__detailLoader=async(runId,kind,id)=>{
 window.__detailCalls.push({runId,kind,id})
 if(id==='slow-request') await new Promise(resolve=>setTimeout(resolve,250))
 const payload={model:'test-model',messages:[{role:'system',content:'业务执行规则'},{role:'user',content:'确认 S09999 订单'},{role:'tool',tool_call_id:'tool-299',content:'已核对订单。'}],tools:[{type:'function',function:{name:'native_read',parameters:{type:'object'}}}]}
 const data=kind==='request'?{association:window.__traceData.trace.requests?.find(request=>request.id===id)?.association??'linked',input:payload,metadata:{status:'completed'},response:{status:200,request_ids:['provider-check'],output:{text:'公开输出 '+runId,stop_reason:'tool_calls',tool_calls:[{id:'t1'}]}},comparison:{previous_request_id:'legacy-request',unchanged_messages:2,added_messages:1,removed_messages:0,added_chars:29,removed_chars:0,unit:'characters',method:'exact common prefix'}}:kind==='action'?{status:'verified',payload:{model:'sale.order',method:'action_confirm',ids:[299]},approval:{values:{state:'sale'},prestate:{state:'draft'}},result:{ok:true},verification:{state:'sale'}}:kind==='tool'?{arguments:{ids:['订单299']},normalized_arguments:{ids:[299],model:'sale.order'},result:{ok:true,rows:'记录'.repeat(6000)}}:kind==='round'?{text:'',error:'Provider response failed. '.repeat(30)+'HTTP 520',stop_reason:'error'}:{instruction:'读取订单并等待批准。'}
 return {kind,id,data,redaction:{hidden_chars:42,redacted_values:1}}
}
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
  // Summary-only fixture: 57 rounds, 300 tools, missing old metadata, exact new timestamps.
  const largeStarted = Date.now()
  await page.evaluate(() => {
    const run={id:'run-detailed',session_id:'session-check',business_id:'business-check',status:'failed',started_at:'2026-09-23T00:00:00Z',ended_at:'2026-09-23T00:05:00Z',tool_count:300,model_rounds:57,elapsed_seconds:300}
    const rounds=Array.from({length:57},(_,i)=>({index:i+1,status:i===56?'error':'completed',tool_ids:[],usage:{input:100,cache_read:1000,output:20,reasoning:15,total:1120}}))
    const tools=Array.from({length:300},(_,i)=>({id:'large-tool-'+i,name:'native_read_'+i,round:(i%57)+1,status:i===3?'error':'completed',elapsed_seconds:i/10,search_text:i===299?'订单 S09999 客户澄川':'业务读取',...(i===299?{started_at:'2026-09-23T00:02:00Z',ended_at:'2026-09-23T00:02:05Z'}:{})}))
    const requests=[{id:'legacy-request',kind:'unknown',status:'recorded',association:'unlinked'},{id:'slow-request',kind:'model',status:'completed',association:'linked',round:1,started_at:'2026-09-23T00:00:01Z',ended_at:'2026-09-23T00:00:04Z',duration_ms:3000,usage:{input:100,cache_read:200,output:10,reasoning:8,total:310}}]
    const actions=[{id:'action-confirm',kind:'execute',status:'verified',model:'sale.order',operation:'action_confirm',tool_ids:['large-tool-299'],record_ids:[299]}]
    const events=[{id:'0',type:'tool_end',tool_call_id:'large-tool-3',is_error:true,at:'2026-09-23T00:00:08Z'},{id:'1',type:'auto_retry_start',status:'retrying',at:'2026-09-23T00:00:09Z'},{id:'2',type:'compaction_end',at:'2026-09-23T00:00:10Z'}]
    window.__tracePatch({trace:{run,rounds,tools,requests,actions,events,summary_only:true,diagnostics:{first_observed_error:{kind:'event',id:'0'}}},runs:[run],selectedRunId:run.id,target:null,onLoadDetail:window.__detailLoader})
  })
  await page.getByRole('heading',{name:'运行总览',exact:true}).waitFor()
  assert.equal(await page.locator('.trace-tool-node').count(),300)
  const renderMilliseconds=Date.now()-largeStarted
  assert.match(await page.locator('.trace-toolbar option:checked').innerText(), /失败 · 57 轮/)
  assert.equal((await page.locator('.trace-toolbar option:checked').innerText()).includes('run-detailed'), false)
  assert.match(await node('第 57 轮').innerText(), /错误/)
  assert.match(await node('模型请求 · legacy-request').innerText(), /已留档 · 未关联/)
  assert.equal(await page.locator('.trace-row-issue').count(), 3, 'round error, tool error, and retry only; tool_end must not duplicate its tool')
  await node('第 57 轮').click()
  await page.locator('.trace-round-error').getByText(/HTTP 520/).waitFor()
  const roundErrorStyle = await page.locator('.trace-round-error p').evaluate(element => ({ whiteSpace: getComputedStyle(element).whiteSpace, overflow: getComputedStyle(element).textOverflow, height: element.clientHeight, line: parseFloat(getComputedStyle(element).lineHeight) }))
  assert.equal(roundErrorStyle.whiteSpace, 'pre-wrap')
  assert.notEqual(roundErrorStyle.overflow, 'ellipsis')
  assert.ok(roundErrorStyle.height > roundErrorStyle.line * 2, 'long provider errors must wrap visibly')
  await page.getByRole('button',{name:/首个记录异常/}).click()
  await page.getByRole('heading',{name:'native_read_3',exact:true}).waitFor()
  assert.equal(await page.locator('.trace-row-issue').filter({hasText:'legacy-request'}).count(),0)
  await page.getByRole('button',{name:'全部折叠',exact:true}).click()
  assert.equal(await page.locator('.trace-tool-node').count(),0)
  await page.getByRole('textbox',{name:'搜索调用、订单或错误'}).fill('S09999')
  await node('native_read_299').waitFor()
  assert.equal(await page.locator('.trace-tool-node').count(),1)
  await page.getByRole('button',{name:'时间轴',exact:true}).click()
  assert.equal(await page.locator('.trace-timeline-track').count(),1)
  assert.ok(await page.locator('.trace-time-unknown').count()>0)
  await node('native_read_299').click()
  await page.locator('.trace-detail-panel .loading-line').waitFor({state:'hidden'})
  assert.match(await page.locator('.trace-detail-panel').innerText(),/模型原始请求参数/)
  assert.match(await page.locator('.trace-detail-panel').innerText(),/"ids": \[\s*299/)
  await page.getByRole('textbox',{name:'搜索调用、订单或错误'}).fill('')
  await page.getByRole('button',{name:'全部展开',exact:true}).click()
  await node('sale.order · action_confirm').click()
  await page.locator('.trace-detail-panel .loading-line').waitFor({state:'hidden'})
  assert.match(await page.locator('.trace-detail-panel').innerText(),/审批依据/)
  assert.match(await page.locator('.trace-detail-panel').innerText(),/回读核验/)
  await node('模型请求 · legacy-request').click()
  await page.locator('.trace-detail-panel .loading-line').waitFor({state:'hidden'})
  assert.match(await page.locator('.trace-detail-panel').innerText(),/未关联，不推断轮次/)
  assert.match(await page.locator('.trace-detail-panel').innerText(),/字符数，不是 token/)
  await page.locator('.trace-message').filter({hasText:'user'}).locator('summary').first().click()
  assert.match(await page.locator('.trace-message').filter({hasText:'user'}).innerText(),/确认 S09999 订单/)
  assert.match(await page.locator('.trace-detail-panel').innerText(),/tool_calls/)
  const beforeAssociation = await page.evaluate(() => window.__detailCalls.filter(call => call.id === 'legacy-request').length)
  await page.evaluate(()=>window.__tracePatch({trace:{...window.__traceData.trace,requests:window.__traceData.trace.requests.map(request=>request.id==='legacy-request'?{...request,association:'linked',round:2}:request)}}))
  await page.locator('.trace-request-detail').getByText('明确关联',{exact:true}).waitFor()
  assert.equal(await page.evaluate(() => window.__detailCalls.filter(call => call.id === 'legacy-request').length),beforeAssociation+1)
  await page.evaluate(()=>window.__tracePatch({trace:{...window.__traceData.trace}}))
  await page.waitForTimeout(20)
  assert.equal(await page.evaluate(() => window.__detailCalls.filter(call => call.id === 'legacy-request').length),beforeAssociation+1,'unchanged summaries must not reload request body')
  await node('模型请求 · slow-request').click()
  await page.evaluate(()=>window.__tracePatch({trace:{...window.__traceData.trace,run:{...window.__traceData.trace.run,id:'new-run'}},selectedRunId:'new-run'}))
  await page.getByRole('heading',{name:'运行总览',exact:true}).waitFor()
  await page.waitForTimeout(300)
  assert.equal((await page.locator('.trace-detail-panel').innerText()).includes('公开输出 run-detailed'),false)
  assert.ok((await page.evaluate(()=>window.__detailCalls)).every(call=>call.kind==='run'||call.kind==='round'||call.id==='large-tool-3'||call.id==='large-tool-299'||call.id==='action-confirm'||call.kind==='request'),'details must load only selected nodes')
  await page.screenshot({path:resolve(output,'inspector-300-tools.png')})
  assert.deepEqual(errors,[])
  const summary = 'PASS: 80-round legacy regression; 57-round/300-tool summary render '+renderMilliseconds+'ms; search/collapse/true timeline/unknown time; lazy requests role folds/context chars/public stop; normalized tool/action approval-execution-readback; cross-run stale response; independent scroll/follow/evidence; no API or ERP calls.'
  writeFileSync(resolve(output,'result.log'),summary+'\n')
  console.log(summary)
} finally { await browser.close() }
