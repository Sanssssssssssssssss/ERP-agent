"""Build a path-only test index. Never imports runners, opens traces, or calls a model."""
import argparse
import ast
from collections import defaultdict
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / 'tests/INDEX.md'
# Multiple labels are intentional: one suite can protect several business capabilities.
GROUPS = {
    '中断恢复与宿主交接': ('interrupted', 'recovery', 'resume', 'hitl', 'workbench_host', 'business_status', 'conversation'),
    '授权、证据与写入安全': ('actions', 'approval', 'hitl', 'evidence', 'guards', 'relation', 'integrity', 'stage_contract', 'business_facts', 'access', 'staff'),
    '开票、邮件与文件': ('invoice', 'mail', 'chatter', 'document', 'material', 'csv'),
    '制造、库存与业务日期': ('manufactur', 'supply', 'stock', 'bom', 'subassembl', 'workcenter'),
    '采购与业务状态': ('purchase', 'buy_only', 'repair_plan', 'business_mvp', 'sale_view', 'enterprise'),
    '工具契约、SOP 与能力选择': ('tool', 'sop', 'routing', 'laya', 'openjev', 'selector', 'q4'),
    '搜索与知识检索': ('retrieval', 'knowledge', 'company_records_rag'),
    '上下文、缓存与长期记忆': ('context', 'history', 'memory', 'caching', 'prompt', 'thinking', 'token'),
    '模型协议与传输': ('provider', 'transport', 'http', 'stream', 'pi_ai', 'multimodal'),
    '会话、账本与持久化': ('session', 'storage', 'snapshot', 'world'),
    'Trace、用量与诊断': ('trace', 'diagnos', 'report', 'budget', 'receipt', 'unbounded'),
    '桌面交互与打包': ('desktop/', 'layout', 'migration', 'workbench', 'check.cjs'),
}


def labels(path):
    value = str(path).lower()
    return [name for name, words in GROUPS.items() if any(w in value for w in words)] or ['运行循环与实验支撑']


def build():
    entries = []
    def add(path, kind, title='', group=None, layer='见入口中的断言'):
        path = path.relative_to(ROOT).as_posix()
        for category in group or labels(path):
            entries.append((category, kind, title or Path(path).name, layer, path))

    for path in sorted((ROOT / 'tests').rglob('test*.py')):
        add(path, '离线测试套件')
    for path in sorted((ROOT / 'desktop/scripts').glob('*check.mjs')):
        add(path, '桌面检查')
    for path in sorted((ROOT / 'bench/reference').rglob('test*.py')):
        add(path, '历史参考测试' if '/mcp/' in path.as_posix() else '基准适配测试', group=['历史参考与基准适配'])
    for path in sorted((ROOT / 'experiments').rglob('*.py')):
        if '__pycache__' in path.parts or path == Path(__file__) or path.name == '__init__.py':
            continue
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        executable = any(isinstance(n, ast.If) and '__name__' in ast.unparse(n.test) for n in tree.body)
        if executable or path.name.startswith('test'):
            add(path, '实验入口（含准备/评估；并非全部付费）')
    for path in sorted((ROOT / 'experiments').rglob('*')):
        if path.is_file() and path.name in {'test.sh', 'check.cjs'}:
            add(path, '实验检查')
    for path in sorted((ROOT / 'tests/fixtures/capability_routing').glob('*')):
        if path.suffix in {'.json', '.jsonl'}:
            add(path, '选择器数据集（训练/留出须看原清单）', group=['工具契约、SOP 与能力选择'])
    # This module contains frozen data only, not an executable runner.
    from experiments.agent_regression.cases import CASES, INCIDENTS
    categories = {'tool_contract': '工具契约、SOP 与能力选择', 'phase_boundary': '授权、证据与写入安全',
                  'failure_recovery': '中断恢复与宿主交接', 'evidence_semantics': '授权、证据与写入安全'}
    for row in CASES + INCIDENTS:
        add(ROOT / 'experiments/agent_regression/cases.py', '真实模型节点', row['id'] + ' · ' + row['boundary'],
            [categories[row['group']]], row['group'])
    import json
    for path in sorted((ROOT / 'experiments/agent_regression').glob('*.json')):
        for row in json.loads(path.read_text(encoding='utf8')):
            declared = row.get('categories', [])
            add(path, '真实模型节点', row['id'], group=[c for c in declared if c in GROUPS] or labels(str(path) + ' ' + ' '.join(declared)),
                layer=row.get('failure_layer', row.get('root_cause', row.get('root', row.get('original_failure', '见冻结清单')))))
    # The R series retains its full frozen requests locally; the tracked README is the provenance index.
    names = ['调用未发布工具', '启用状态冲突', '关系删除参数', '历史 active 状态残留', '能力撤下后多一轮选择',
             '漏预加载', '确认操作名错误', '退货明细缺产品', '旧库存字段', '重复 preview', '制造旧字段',
             '猜子记录 ID', '空查询条件', '未设投产数量', '提前完工', '不存在的工具', '缺 SOP 输入', '财务旧字段']
    for i, title in enumerate(names, 1):
        add(ROOT / 'experiments/agent_regression/README.md', '真实模型节点', f'R{i:02} · {title}',
            ['工具契约、SOP 与能力选择'], '详见原节点因果边界；不把首个报错当根因')
    for path in sorted((ROOT / 'bench/tasks').glob('*/task.toml')):
        row = tomllib.loads(path.read_text())['metadata']
        pattern = row['task_pattern']
        groups = ['采购与业务状态'] if 'buy_only' in pattern or 'repair_plan' in pattern else ['制造、库存与业务日期']
        if any(s in pattern for s in ('invoicing', 'downpayment')):
            groups.append('开票、邮件与文件')
        add(path, 'ERPBench 完整业务', f"{row['scenario_number']} · {row['name']}", groups, pattern)
    for case, title, category in [
        ('S01499', '查单/确认/开票/独立发送/聊天回读', '开票、邮件与文件'),
        ('SALE', '销售确认保护', '采购与业务状态'),
        ('E01', '部分交付与收货', '制造、库存与业务日期'),
        ('E02', '多级制造与采购补料', '制造、库存与业务日期'),
        ('E03', '客户收款核销', '采购与业务状态'),
        ('E04', '供应商付款核销', '采购与业务状态'),
        ('E05', '客户退货退款', '开票、邮件与文件'),
        ('E06', '供应商退货退款', '开票、邮件与文件')]:
        path = 'experiments/agent_regression/RESULTS.md' if case == 'S01499' else 'experiments/enterprise_validation/README.md'
        add(ROOT / path, '隔离完整业务', case + ' · ' + title, [category], '最终状态＋安全约束＋效率；历史通过不代表当前通过')
    grouped = defaultdict(list)
    for entry in sorted(set(entries)):
        grouped[entry[0]].append(entry)
    text = ['# 按能力查找测试', '', '由 `python -m experiments.test_index` 生成；入口见 [TESTING.md](../TESTING.md)。', '',
            '这里列测试资产，不声明通过。一个套件可出现在多个能力下。历史参考不进入当前业务验收。', '',
            '付费入口必须先查看原清单、冻结输入和授权；不得批量执行此索引。', '']
    def clean(s):
        return re.sub(r'\s+', ' ', str(s)).replace('|', '\\|')
    for category, rows in sorted(grouped.items()):
        text += [f'## {category}', '', '| 类型 | 案例 / 文件 | 故障位置 / 检查范围 |', '|---|---|---|']
        for _, kind, title, layer, path in rows:
            assert (ROOT / path).is_file(), path
            text.append(f'| {kind} | [{clean(title)}](../{path}) | {clean(layer)} |')
        text.append('')
    return '\n'.join(text)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    expected = build()
    if args.check:
        assert INDEX.read_text(encoding='utf8') == expected, 'Test index stale; run python -m experiments.test_index'
    else:
        INDEX.write_text(expected, encoding='utf8')
    print('Test index verified; no API calls or business writes.')
