"""Constrained final slot after frozen thinking; no new reasoning or tool execution."""
import hashlib
import json
import math
import sys
import time
from pathlib import Path

from . import q4_inputs as base, q4_thinking as trial


def run(source):
    source = Path(source).resolve()
    rows = base.read(source/'probe-inputs.json')
    results = [json.loads(line) for line in (source/'probe-results.jsonl').read_text(encoding='utf8').splitlines()]
    assert len(rows) == len(results) == base.read(source/'probe-summary.json')['completed']
    for path,digest in base.read(source/'frozen.json')['sources'].items():
        assert base.sha(path)==digest,path
    out = source/'readout'
    out.mkdir(exist_ok=True)
    base.save(out/'frozen.json',{'code':base.sha(__file__),'parent_results':base.sha(source/'probe-results.jsonl'),
        'parent_manifest':base.sha(source/'frozen.json'),'policy':'All rows, identical generated thought; append canonical final separator and restrict to declared A/B slots with argmax. No answer-label selection.',
        'limitation':'Post-hoc prefill experiment; actual worker integration and unseen cases remain separate.'})
    model,tok,metadata,backend = base.load_gpu(out/'error.log')
    base.save(out/'gpu.json',metadata)
    close = tok.convert_tokens_to_ids('</think>')
    slots = [tok.encode(letter,add_special_tokens=False) for letter in 'AB']
    assert all(len(s)==1 for s in slots)
    with (out/'results.jsonl').open('x',encoding='utf8') as output:
        for i,(row,result) in enumerate(zip(rows,results),1):
            assert row['id']==result['id']
            prompt=(source/f'probe-{i:02d}.prompt.txt').read_text(encoding='utf8')
            assert hashlib.sha256(prompt.encode()).hexdigest()==result['prompt_sha256']
            tokens=[json.loads(line)['token'] for line in (source/f'probe-{i:02d}.tokens.jsonl').read_text(encoding='utf8').splitlines()]
            assert tok.decode(tokens[:result['output_tokens']],skip_special_tokens=False)==result['text']
            if close not in tokens:
                record={'id':row['id'],'prediction':None,'reason':'thinking_did_not_finish'}
            else:
                prefix=tok.encode(prompt,add_special_tokens=False)+tokens[:tokens.index(close)+1]+tok.encode('\n\n',add_special_tokens=False)
                assert len(prefix)<model.engine.context_tokens
                start=time.monotonic()
                logits=model.engine.full_logits(prefix)
                values=[float(logits[s[0]]) for s in slots]
                assert all(math.isfinite(v) for v in values)
                chosen=max(range(2),key=lambda j:values[j])
                letter='AB'[chosen]
                prediction=trial.final_answer('</think>'+letter,row['options'])
                assert prediction == (row['options'][chosen]['id']=='A')
                record={'id':row['id'],'prediction':prediction,'letter':letter,
                    'conditional_option_scores':backend.softmax(values),'option_ids':[o['id'] for o in row['options']],
                    'input_tokens':len(prefix),'output_tokens':0,'seconds':time.monotonic()-start,
                    'prefix_sha256':hashlib.sha256(base.encode(prefix).encode()).hexdigest()}
            output.write(base.encode(record)+'\n');output.flush();print(base.encode(record),flush=True)


if __name__=='__main__':
    run(sys.argv[1])
