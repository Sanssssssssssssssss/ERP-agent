"""Select on development decisions, then report untouched round-held-out judgments."""
from collections import defaultdict
import sys
import time
from .q4_inputs import OUT, VARIANTS, read, save, sha


def evaluate(phase):
    inputs={r['id']:r for r in read(OUT/f'{phase}-inputs.json')}
    labels={(r['id'],r['capability']):r['target'] for r in read(OUT/'labels.json')[phase]}
    results=[__import__('json').loads(line) for line in (OUT/f'{phase}-results.jsonl').read_text(encoding='utf8').splitlines()]
    summary=read(OUT/f'{phase}-summary.json')
    assert len(results)==len({r['id'] for r in results})==summary['rows']
    expected={r['id'] for r in inputs.values() if phase=='tuning' or r['variant'] in {'baseline',read(OUT/'winner.json')['variant']}}
    assert {r['id'] for r in results}==expected
    grouped=defaultdict(list)
    for r in results:
        source=inputs[r['id']]
        assert r['option_ids']==[o['id'] for o in source['options']]
        chosen=r['option_ids'][max(range(len(r['probabilities'])),key=lambda i:r['probabilities'][i])]
        assert r['prediction']=={'A':True,'B':False,'C':None}[chosen]
        grouped[source['variant']].append((source,r))
    metrics={}
    for variant,rows in grouped.items():
        pos=sum(labels[s['case_id'],s['group']] for s,r in rows);neg=len(rows)-pos
        tp=sum(r['prediction'] is True and labels[s['case_id'],s['group']] for s,r in rows)
        tn=sum(r['prediction'] is False and not labels[s['case_id'],s['group']] for s,r in rows)
        pairs=defaultdict(list)
        for s,r in rows:pairs[s['case_id'],s['group']].append(r['prediction'])
        assert all(len(v)==2 for v in pairs.values())
        stable={k:p[0] for k,p in pairs.items() if p[0] is not None and p[0]==p[1]}
        answered=sum(r['prediction'] is not None for s,r in rows)
        metrics[variant]={'correct':tp+tn,'total':len(rows),'true_positive':tp,'positive_total':pos,
            'true_negative':tn,'negative_total':neg,'balanced_accuracy':(tp/pos+tn/neg)/2,
            'abstentions':sum(r['prediction'] is None for s,r in rows),
            'answered':answered,'coverage':answered/len(rows),
            'both_orders_correct':sum(all(x==labels[k] for x in p) for k,p in pairs.items()),
            'order_flips':sum(p[0]!=p[1] for p in pairs.values()),
            'stable_binary_pairs':len(stable),'stable_binary_correct':sum(v==labels[k] for k,v in stable.items()),
            'input_tokens':sum(r['input_tokens'] for s,r in rows),
            'forward_seconds':sum(r['forward_seconds'] for s,r in rows)}
    return metrics


def select():
    metrics=evaluate('tuning')
    candidates=[v for v in VARIANTS if v not in {'baseline','abstain'}]
    winner=max(candidates,key=lambda v:(metrics[v]['balanced_accuracy'],metrics[v]['both_orders_correct'],
                                      -metrics[v]['input_tokens']))
    save(OUT/'tuning-metrics.json',metrics)
    save(OUT/'winner.json',{'variant':winner,'metrics':metrics[winner],'at':time.time(),
         'frozen_sha256':sha(OUT/'frozen.json'),'tuning_results_sha256':sha(OUT/'tuning-results.jsonl'),
         'meaning':'Chosen before holdout; not business acceptance.'})
    print(winner,metrics[winner])


def report():
    tuning=read(OUT/'tuning-metrics.json');holdout=evaluate('holdout');winner=read(OUT/'winner.json')['variant']
    save(OUT/'holdout-metrics.json',holdout)
    names={'baseline':'原基线','plain':'简短问题与选项','roles':'再分开观察与历史','specific':'再明确各能力职责',
           'chinese':'中文问题与选项','abstain':'三选项，允许未知'}
    text=['# Q4 输入与选择实验','','仅Qwen3.5-4B Q4_K_M，GPU；无训练、付费API或ERP调用。',
        '调优集9判断，保护集22判断，每项交换选项顺序。保护集本轮未参与调优，但全部有既往开发曝光，部分节点同一业务；不称盲测。',
        '', '|条件|调优正确|两种顺序都正确|顺序翻转|未知|覆盖率|输入tokens|', '|---|---:|---:|---:|---:|---:|---:|']
    for v in VARIANTS:
        m=tuning[v];text.append(f'|{names[v]}|{m["correct"]}/18|{m["both_orders_correct"]}/9|{m["order_flips"]}/9|{m["abstentions"]}|{m["coverage"]:.0%}|{m["input_tokens"]:,}|')
    text+=['',f'按事先固定的平衡准确率、双顺序正确数、输入量顺序，选择「{names[winner]}」后才运行保护集。',
        '', '|保护集|正确|两种顺序都正确|漏选|误选|', '|---|---:|---:|---:|---:|']
    for v in ['baseline',winner]:
        m=holdout[v];text.append(f'|{names[v]}|{m["correct"]}/44|{m["both_orders_correct"]}/22|{m["positive_total"]-m["true_positive"]}|{m["negative_total"]-m["true_negative"]}|')
    text+=['','三选项UNKNOWN按未答对计，单独报告覆盖率；没有把拒答算成功。无选项分数校准，也不据此放行写入。',
        'roles保留全部目标、观测值/新鲜度、未决动作、历史结果及工具事件；仅分区、展开表格并提升重复元数据。没有从答案或未来trace补入完成状态。',
        '覆盖actions/accounting/attachments/diagnostics/knowledge；其余能力缺少本轮合格保护样本。完整业务尚未验收。',
        '', '证据：frozen.json、independent-review.json、winner.json、tuning/holdout-inputs.json、tuning/holdout-results.jsonl、tuning/holdout-metrics.json。']
    (OUT/'RESULTS.md').write_text('\n'.join(text)+'\n',encoding='utf8')
    print(holdout)


if __name__=='__main__':
    {'select':select,'report':report}[sys.argv[1]]()
