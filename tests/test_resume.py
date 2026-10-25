import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import os
import runpy

from amphoreus_arena.go_agreement import create_go
from amphoreus_arena.go_rendering import GoRenderer
from amphoreus_arena.harnesses import DshHarness
from amphoreus_arena.runner import MatchRunner


class ResumeTests(unittest.IsolatedAsyncioTestCase):
    def players(self, root):
        config = {'kind':'dsh','command':[sys.executable,str(Path(__file__).with_name('fake_acp.py')), '--go-session']}
        return {side:DshHarness(config,root/side) for side in ('black','white')}

    def runner(self, root, limit):
        return MatchRunner(create_go(board_size=9),self.players(root),GoRenderer(),root,limit,None,prompt_mode='incremental')

    async def test_resume_preserves_sessions_history_and_delta_without_duplicate_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); first=await self.runner(root,4).run()
            original=copy.deepcopy(first)
            second=self.runner(root,6); second.restore(first)
            self.assertEqual(first,original)
            self.assertEqual(second.game.snapshot(),original['snapshots'][-1]['state'])
            expected_seen=second.game._seen.copy()
            result=await second.run()
            self.assertEqual(len(result['turns']),6)
            self.assertEqual(len(result['snapshots']),7)
            self.assertEqual(result['turns'][:4],original['turns'])
            self.assertEqual(result['turns'][4]['input']['kind'],'delta')
            self.assertTrue(expected_seen <= second.game._seen)
            self.assertEqual(result['continuations'][0]['from_ply'],4)
            for side in ('black','white'):
                frames=[json.loads(l)['frame'] for l in (root/side/'protocol.jsonl').read_text(encoding='utf-8').splitlines()]
                self.assertEqual(sum(f.get('method')=='session/new' for f in frames),1)
                resume=next(f for f in frames if f.get('method')=='session/resume')
                self.assertEqual(resume['params']['sessionId'],original['players'][side]['session_id'])
                self.assertEqual(resume['params']['cwd'],str((root/side/'workspace').resolve()))

    async def test_restore_rejects_running_terminal_or_corrupt_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); record=await self.runner(root,2).run()
            for change in ({'status':'running'},{'result':{'winner':'black'}},{'active_turn':{'ply':3}}):
                with self.subTest(change=change),self.assertRaises(ValueError):
                    self.runner(root,None).restore({**record,**change})
            corrupt=copy.deepcopy(record);corrupt['snapshots'][-1]['state']['board'][0]='B........'
            with self.assertRaisesRegex(ValueError,'snapshot'):
                self.runner(root,None).restore(corrupt)


class CapacityPlayer:
    def __init__(self, root, side, fail=False):
        self.root, self.side, self.fail = root, side, fail
        self.metadata = {'kind':'codex' if side=='white' else 'dsh', 'session_id':side+'-session',
                         'model':'fixture-model', 'reasoning_effort':'low'}
        (root/side).mkdir(exist_ok=True)

    async def start(self): pass
    async def close(self): pass
    def configure_game_tools(self, connection): pass

    async def act(self, prompt, schema, ply, images=None):
        (self.root/self.side/f'{ply:04d}.prompt.txt').write_text(prompt,encoding='utf-8')
        if self.fail and ply==4:
            message='Selected model is at capacity. Please try a different model.'
            events=[{'type':'thread.started','thread_id':'white-session'}, {'type':'turn.started'},
                    {'type':'error','message':message}, {'type':'turn.failed','error':{'message':message}}]
            (self.root/'white/0004.events.jsonl').write_text('\n'.join(json.dumps(e) for e in events),encoding='utf-8')
            native=[{'type':'event_msg','payload':{'type':'task_started'}},
                    {'type':'turn_context','payload':{'model':'fixture-model','effort':'low'}},
                    {'type':'response_item','payload':{'type':'message','role':'user','content':[{'text':prompt}]}},
                    {'type':'event_msg','payload':{'type':'task_complete','last_agent_message':None,
                        'error':{'codex_error_info':'server_overloaded','message':message}}}]
            folder=self.root/'native/sessions';folder.mkdir(parents=True,exist_ok=True)
            (folder/'rollout-white-session.jsonl').write_text('\n'.join(json.dumps(e) for e in native),encoding='utf-8')
            raise RuntimeError('Codex exited 1; see 0004.stderr.log / events.jsonl')
        return json.dumps({'move':['A2','A1','B2','J9','C1','J8'][ply-1], 'explanation':'fixture'})


class FailedResumeTests(unittest.IsolatedAsyncioTestCase):
    def runner(self,root,fail=False,limit=6):
        players={s:CapacityPlayer(root,s,fail=fail) for s in ('black','white')}
        runner=MatchRunner(create_go(board_size=9,rules='area-positional-agreement-v1'),players,GoRenderer(),root,limit,None,prompt_mode='incremental')
        runner.record['game_options']={'board_size':9,'komi':7.5,'rules':'area-positional-agreement-v1'}
        return runner

    async def test_capacity_recovery_archives_attempt_and_resynchronizes_once(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'CODEX_HOME':str(Path(tmp)/'native')}):
            root=Path(tmp); first=await self.runner(root,fail=True).run()
            self.assertEqual(first['status'],'failed');self.assertEqual(len(first['turns']),3)
            failed_prompt=(root/'observations/0004.prompt.txt').read_bytes()
            second=self.runner(root);second.restore(first,retry_failed=True)
            archive=root/second.record['continuations'][-1]['archive']
            self.assertEqual((archive/'observations/0004.prompt.txt').read_bytes(),failed_prompt)
            self.assertEqual(json.loads((archive/'match.json').read_text(encoding='utf-8')),first)
            result=await second.run()
            self.assertEqual(result['status'],'stopped');self.assertEqual(len(result['snapshots']),7)
            self.assertEqual(result['turns'][:3],first['turns'])
            self.assertEqual([t['input']['kind'] for t in result['turns'][3:]],['recovery','delta','delta'])
            prompt=(root/'observations/0004.prompt.txt').read_text(encoding='utf-8')
            payload=json.loads(prompt.split('\n',1)[1])
            self.assertEqual(payload['board'],first['snapshots'][-1]['state']['board'])
            self.assertNotIn('opponent_updates',payload)
            self.assertEqual(payload['your_last_turn']['ply'],2)
            audit=runpy.run_path(str(Path(__file__).parents[1]/'scripts/audit-go-match.py'))['audit']
            self.assertTrue(audit(root)['passed'])

    async def test_recovery_rejects_partial_output_tools_and_changed_context(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'CODEX_HOME':str(Path(tmp)/'native')}):
            root=Path(tmp);record=await self.runner(root,fail=True).run()
            with self.assertRaises(ValueError):self.runner(root).restore(record)
            response=root/'white/0004.response.txt';response.write_text('{"move":"J9"}',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'response file'):self.runner(root).restore(record,retry_failed=True)
            response.unlink()
            native=root/'native/sessions/rollout-white-session.jsonl';old=native.read_text(encoding='utf-8')
            for injected in [
                {'type':'response_item','payload':{'type':'function_call','name':'shell'}},
                {'type':'response_item','payload':{'type':'message','role':'assistant','content':[]}},
                {'type':'turn_context','payload':{'model':'changed','effort':'low'}}]:
                native.write_text(old+'\n'+json.dumps(injected),encoding='utf-8')
                with self.assertRaises(ValueError):self.runner(root).restore(record,retry_failed=True)
            native.write_text(old,encoding='utf-8')
            events=root/'white/0004.events.jsonl';items=[json.loads(l) for l in events.read_text(encoding='utf-8').splitlines()]
            items.insert(2,{'type':'item.completed','item':{'type':'mcp_tool_call'}})
            events.write_text('\n'.join(json.dumps(e) for e in items),encoding='utf-8')
            with self.assertRaises(ValueError):self.runner(root).restore(record,retry_failed=True)
            self.assertFalse((root/'attempts').exists())


class IllegalResumeTests(unittest.IsolatedAsyncioTestCase):
    def runner(self,root,fail=False):
        command=[sys.executable,str(Path(__file__).with_name('fake_acp.py')),'--go-session']
        if fail:command.append('--illegal-go')
        players={s:DshHarness({'kind':'dsh','command':command},root/s) for s in ('black','white')}
        runner=MatchRunner(create_go(board_size=9,rules='area-positional-agreement-v1'),players,
                           GoRenderer(),root,8,None,prompt_mode='incremental')
        runner.record['game_options']={'board_size':9,'komi':7.5,'rules':'area-positional-agreement-v1'}
        return runner

    async def test_illegal_retry_preserves_attempt_explains_rejection_and_commits_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); first=await self.runner(root,fail=True).run()
            self.assertEqual(first['error']['message'],'Go intersection is occupied')
            self.assertEqual(len(first['turns']),4)
            raw=(root/'black/0005.response.txt').read_bytes()
            second=self.runner(root);second.restore(first,retry_illegal=True)
            evidence=second.record['continuations'][-1]['recovery']
            self.assertEqual(evidence['action'],{'move':'A2'})
            self.assertEqual(evidence['point_stone'],'B')
            archive=root/second.record['continuations'][-1]['archive']
            self.assertEqual((archive/'black/0005.response.txt').read_bytes(),raw)
            self.assertEqual(json.loads((archive/'match.json').read_text(encoding='utf-8')),first)
            result=await second.run()
            self.assertEqual(result['turns'][:4],first['turns'])
            self.assertEqual(result['snapshots'][:5],first['snapshots'])
            self.assertEqual(len(result['turns']),8);self.assertEqual(len(result['snapshots']),9)
            self.assertEqual(result['turns'][4]['action']['move'],'C1')
            self.assertEqual([t['input']['kind'] for t in result['turns'][4:]],['recovery','delta','delta','delta'])
            prompt=(root/'black/0005.prompt.txt').read_text(encoding='utf-8')
            self.assertIn('was rejected',prompt);self.assertIn('NOT committed',prompt)
            self.assertIn('A2 currently has black stone',prompt)
            data=json.loads(prompt.split('\n',1)[1])
            self.assertEqual(data['board'],first['snapshots'][-1]['state']['board'])
            self.assertEqual(data['rejected_action'],{'move':'A2'})
            self.assertNotIn('opponent_updates',data)
            audit=runpy.run_path(str(Path(__file__).parents[1]/'scripts/audit-go-match.py'))['audit']
            self.assertTrue(audit(root)['passed'])

    async def test_illegal_retry_refuses_legal_substitution_native_mismatch_tools_or_commit(self):
        from amphoreus_arena.recovery import verify_illegal_attempt
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);record=await self.runner(root,fail=True).run()
            with self.assertRaises(ValueError):self.runner(root).restore(record,retry_failed=True)
            with self.assertRaises(ValueError):self.runner(root).restore(record,retry_failed=True,retry_illegal=True)
            response=root/'black/0005.response.txt';raw=response.read_text(encoding='utf-8')
            response.write_text('{"move":"C1"}',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'legal action'):verify_illegal_attempt(root,record)
            response.write_text(raw,encoding='utf-8')
            path=root/'black/protocol.jsonl';native=path.read_text(encoding='utf-8')
            path.write_text(native+'\n'+json.dumps({'direction':'in','frame':{'method':'session/update','params':{
                'sessionId':'stable-test-session','update':{'sessionUpdate':'tool_call'}}}}),encoding='utf-8')
            with self.assertRaises(ValueError):verify_illegal_attempt(root,record)
            path.write_text(native.replace('"stopReason": "end_turn"','"stopReason": "cancelled"'),encoding='utf-8')
            with self.assertRaises(ValueError):verify_illegal_attempt(root,record)
            path.write_text(native,encoding='utf-8')
            arena=root/'events.jsonl';lines=arena.read_text(encoding='utf-8').splitlines()
            lines.insert(-1,json.dumps({'type':'turn.applied','ply':5}))
            arena.write_text('\n'.join(lines),encoding='utf-8')
            with self.assertRaises(ValueError):verify_illegal_attempt(root,record)
            self.assertFalse((root/'attempts').exists())


if __name__=='__main__': unittest.main()
