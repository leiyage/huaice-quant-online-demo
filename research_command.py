"""CEO command orchestration. Reuses research experiments; never controls replay."""
from __future__ import annotations
import json
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import research_experiments as research


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def plan_request(text, assets):
    if not isinstance(text, str) or not 4 <= len(text.strip()) <= 1000:
        raise ValueError('请输入4—1000字的历史策略对照研究任务。')
    text = text.strip()
    if not any(word in text for word in ('回测', '策略对照', '历史策略', '历史研究', '历史表现')):
        raise ValueError('MVP仅支持本地历史策略对照研究；不支持宏观事件影响预测。')
    if any(word in text for word in ('美联储', '降息', '加息', '黄金', '纳斯达克', '原油', '预测', '下单', '买入', '卖出')):
        raise ValueError('该请求包含尚未支持的事件分析、资产或交易指令；请提交当前组合的历史策略对照研究。')
    mentioned = set(re.findall(r'\b[A-Za-z][A-Za-z0-9.]{1,11}\b', text.upper()))
    if mentioned - set(assets):
        raise ValueError('包含当前未加载的资产或指令：' + ', '.join(sorted(mentioned - set(assets))))
    if mentioned and mentioned != set(assets):
        raise ValueError('第一版仅研究完整的当前加载组合，不会静默替换或忽略资产。')
    if re.search(r'\d{4}[-/年]|\d+\s*(天|日|月|年)|参数|杠杆|仓位|手续费|滑点', text):
        raise ValueError('第一版不解析自定义日期和参数；使用当前参数快照及完整共同历史区间。')
    return {'title': '当前组合历史策略对照研究', 'description': text, 'department': 'quant',
            'assets': list(assets), 'parser': '规则解析，未接入LLM',
            'method': '买入持有、趋势固定仓位、波动率目标、ATR止损；固定种子区块重采样',
            'steps': ['冻结行情与参数快照', '调用现有策略对照实验', '保存报告和可复现档案'],
            'limitations': '仅描述历史样本，不评估事件因果影响，不生成订单；行情来源以实验清单为准。'}


class CommandCenter:
    def __init__(self, path, engine, result_dir):
        self.path, self.engine, self.result_dir = Path(path), engine, Path(result_dir)
        self.guard = threading.RLock()
        self.active = set()
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS command_tasks (id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, data TEXT NOT NULL)')
            for row in db.execute('SELECT id,data FROM command_tasks').fetchall():
                task = json.loads(row[1])
                if task['status'] in ('PENDING', 'RUNNING'):
                    self.finish(task, 'FAILED', error='服务重新启动，未自动重跑；请检查后重新提交。')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, task):
        task['updated_at'] = now()
        with self.connect() as db:
            db.execute('UPDATE command_tasks SET data=? WHERE id=?', (json.dumps(task, ensure_ascii=False, allow_nan=False), task['task_id']))

    def get(self, task_id):
        with self.connect() as db:
            row = db.execute('SELECT data FROM command_tasks WHERE id=?', (task_id,)).fetchone()
        if row is None:
            raise KeyError('研究任务不存在。')
        return json.loads(row[0])

    def list(self):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM command_tasks ORDER BY rowid DESC LIMIT 30')]

    def plan(self, description):
        with self.engine.lock:
            return plan_request(description, self.engine._frames.keys()) | {'generation': self.engine._generation}

    def submit(self, description, request_key, expected_generation=None):
        if not isinstance(description, str):
            raise ValueError('研究任务描述必须为文本。')
        if not isinstance(request_key, str) or not re.fullmatch(r'[a-zA-Z0-9-]{8,80}', request_key):
            raise ValueError('提交需要有效的幂等请求ID。')
        with self.guard:
            with self.connect() as db:
                old = db.execute('SELECT data FROM command_tasks WHERE request_key=?', (request_key,)).fetchone()
            if old:
                task = json.loads(old[0])
                if task['description'] != description.strip():
                    raise ValueError('同一请求ID不能用于不同研究内容。')
                return task
            if self.active:
                raise RuntimeError('已有中央研究任务执行或排队，请完成后再提交。')
            with self.engine.lock:
                if expected_generation is not None and expected_generation != self.engine._generation:
                    raise ValueError('当前行情或参数已变化，请重新生成研究计划。')
                plan = plan_request(description, self.engine._frames.keys())
                frames = {name: frame.copy(deep=True) for name, frame in self.engine._frames.items()}
                params = replace(self.engine._params)
                generation = self.engine._generation
                source = self.engine._data_source
                is_demo = any(label in source.upper() for label in ('合成', '演示', 'SYNTHETIC'))
                unverified = [name for name, frame in frames.items() if not frame.attrs.get('provenance', {}).get('verified')]
                data_warning = ('DEMO：合成数据计算，不是真实行情。' if is_demo else '本地历史计算；' + ('来源尚未验证：' + ', '.join(unverified) if unverified else '来源记录已通过文件哈希校验。'))
            task = dict(plan, task_id=uuid.uuid4().hex, status='PENDING', created_at=now(), updated_at=now(),
                        progress='已排队；未开始计算', result=None, error=None, logs=[], cancel_requested=False,
                        generation=generation, data_source=source, is_demo=is_demo, data_warning=data_warning)
            with self.connect() as db:
                db.execute('INSERT INTO command_tasks VALUES(?,?,?)', (task['task_id'], request_key, json.dumps(task, ensure_ascii=False)))
            self.active.add(task['task_id'])
            try:
                threading.Thread(target=self.execute, args=(task['task_id'], frames, params), name='ceo-quant-research', daemon=True).start()
            except Exception as exc:
                self.active.discard(task['task_id'])
                self.finish(task, 'FAILED', error=f'无法启动执行线程：{exc}')
                raise
            return task

    def log(self, task, status, message):
        task['logs'].append({'stage': 'quant', 'status': status, 'message': message, 'created_at': now()})

    def finish(self, task, status, result=None, error=None):
        task.update(status=status, result=result, error=error,
                    progress={'COMPLETED':'计算及报告归档完成', 'FAILED':'任务失败', 'CANCELLED':'已取消'}[status])
        self.log(task, status.lower(), error or task['progress'])
        self.save(task)

    def cancel(self, task_id):
        with self.guard:
            task = self.get(task_id)
            if task['status'] == 'PENDING':
                self.finish(task, 'CANCELLED')
            elif task['status'] == 'RUNNING':
                task['cancel_requested'] = True
                task['progress'] = '已请求取消；等待当前计算安全结束'
                self.save(task)
            return self.get(task_id)

    def execute(self, task_id, frames, params):
        acquired = False
        try:
            with self.guard:
                task = self.get(task_id)
                if task['status'] == 'CANCELLED':
                    return
                task.update(status='RUNNING', progress='正在执行策略对照；模块未提供内部百分比')
                self.log(task, 'running', '使用提交时的行情和参数副本执行；不改变模拟账户或回放游标。')
                self.save(task)
            acquired = self.engine._research_lock.acquire(blocking=False)
            if not acquired:
                raise RuntimeError('已有其他量化实验占用研究资源，请稍后重新提交。')
            experiment = research.run_experiment(frames, params, simulations=100, block=10, seed=20261012)
            with self.guard:
                task = self.get(task_id)
                if task['cancel_requested']:
                    self.finish(task, 'CANCELLED')
                    return
                task['progress'] = '计算完成；正在归档报告'
                self.save(task)
            folder = research.save_experiment(experiment, self.result_dir / 'experiments')
            payload = research.experiment_payload(experiment)
            manifest = payload['manifest']
            result = {'objective': task['description'], 'method': task['method'], 'data_source': task['data_source'],
                      'department_results': {'quant': payload},
                      'findings': '已完成四种历史策略方案的同区间对照；具体指标与差异区间见报告，未据此判断未来优劣。',
                      'conclusion': ('DEMO：实际计算合成数据，仅用于演示。' if task['is_demo'] else '这是实际运行的本地历史实验，不是AI事件预测或交易建议。'),
                      'risk_warnings': manifest['limitations'] + [task['data_warning']],
                      'is_demo': task['is_demo'],
                      'report_url': f'/api/experiment-file?run={folder.name}&file=report',
                      'archive_url': f'/api/experiment-file?run={folder.name}&file=archive'}
            with self.guard:
                task = self.get(task_id)
                if task['cancel_requested']:
                    self.finish(task, 'CANCELLED', result={'note':'取消发生在归档期间；实验档案已保存，但不发布为完成任务。'})
                else:
                    self.finish(task, 'COMPLETED', result=result)
        except Exception as exc:
            with self.guard:
                task = self.get(task_id)
                self.finish(task, 'FAILED', error=f'{type(exc).__name__}: {exc}')
        finally:
            if acquired:
                self.engine._research_lock.release()
            with self.guard:
                self.active.discard(task_id)
