"""Routine-agent script runner: protocol, limits, stop and timeout."""
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'backend'), str(Path(__file__).resolve().parents[1] / 'backend/agent')]
from agent_profiles import AgentProfile
from agent_runner import run_agent_script


class AgentRunnerTests(unittest.TestCase):
    def run_script(self, source, cancel_after=None, timeout=5, grace=.2):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'run.py').write_text(source)
            profile = AgentProfile(name='fake', path=root, entry='run.py', timeout_seconds=timeout, on_stop={'grace_seconds':grace})
            events=[]; cancel=threading.Event()
            timer = threading.Timer(cancel_after, cancel.set) if cancel_after else None
            if timer: timer.start()
            try:
                with patch('utils.is_path_allowed',return_value=True):
                    output=run_agent_script(profile, {}, False, cwd=tmp, run_id='test', cancel_event=cancel, emit=lambda t,d: events.append((t,d)))
            finally:
                if timer: timer.cancel()
            return json.loads(output), events
    def test_protocol_upsert_environment_and_result(self):
        result, events = self.run_script('''import json,os,sys
assert os.environ['MYHARNESS_AGENT']=='1'
assert json.load(open(sys.argv[2]))=={}
print('plain log')
print('stderr',file=sys.stderr)
for status in ['running','ok']: print(json.dumps({'wj':'item','key':'a','status':status}))
print(json.dumps({'wj':'result','summary':'Done','table':{'columns':['A'],'rows':[[1]]}}))
''')
        self.assertEqual(result['status'],'completed'); self.assertEqual(len(result['items']),1)
        self.assertEqual(result['items'][0]['status'],'ok'); self.assertIn('stderr',result['log_tail'])
        self.assertEqual(events[-1][1]['table']['rows'],[[1]])
        self.assertEqual(len({d['tool_call_id'] for _,d in events}),1)
    def test_nonzero_and_generated_result(self):
        result,events=self.run_script('raise SystemExit(7)')
        self.assertEqual(result['status'],'failed'); self.assertEqual(result['exit_code'],7); self.assertTrue(events[-1][1]['summary'])
    def test_timeout_and_force_kill(self):
        start=time.monotonic()
        result,_=self.run_script('import signal,time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)',timeout=.2)
        self.assertEqual(result['status'],'timed_out'); self.assertLess(time.monotonic()-start,4)
    def test_cooperative_stop_keeps_last_record(self):
        result,events=self.run_script('''import signal,time,json
stop=False
def done(*args):
 print(json.dumps({'wj':'item','key':'in-flight','status':'ok','detail':'submitted'}),flush=True)
 raise SystemExit(0)
signal.signal(signal.SIGTERM,done)
time.sleep(60)
''',cancel_after=.3,grace=1)
        self.assertEqual(result['status'],'stopped'); self.assertEqual(result['items'][0]['status'],'ok')
    def test_validation_errors_never_start_the_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); marker = root/'ran'; (root/'run.py').write_text(f'open({str(marker)!r},"w").write("x")')
            profile = AgentProfile(name='fake', path=root, entry='run.py', params={'n': {'type': 'int', 'required': True}})
            out = run_agent_script(profile, {}, False, cwd=tmp, run_id='t', cancel_event=threading.Event(), emit=lambda t, d: None)
            self.assertTrue(out.startswith('ERROR: n: required')); self.assertFalse(marker.exists())
            out = run_agent_script(AgentProfile(name='fake', path=root, entry='run.py'), {}, True, cwd=tmp, run_id='t', cancel_event=threading.Event(), emit=lambda t, d: None)
            self.assertIn('Dry run is not supported', out); self.assertFalse(marker.exists())
    @unittest.skipIf(os.name=='nt','POSIX process-group check')
    def test_child_process_tree_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = str(Path(tmp)/'pid')
            result,_=self.run_script(f'import subprocess,sys,time\np=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])\nopen({pidfile!r},"w").write(str(p.pid))\ntime.sleep(60)',cancel_after=.3)
            self.assertEqual(result['status'],'stopped')
            pid=int(Path(pidfile).read_text())
            # A killed orphan can briefly remain a zombie until reaped by init.
            import subprocess
            info=subprocess.run(['ps','-o','stat=','-p',str(pid)],capture_output=True,text=True).stdout.strip()
            self.assertTrue(not info or info.startswith('Z'), info)
    def test_protocol_limits_and_progress_throttle(self):
        result,events=self.run_script('''import json
print('x'*70000)
for i in range(510): print(json.dumps({'wj':'item','key':str(i),'status':'ok'}))
for i in range(100): print(json.dumps({'wj':'progress','done':i,'total':100}))
''')
        self.assertEqual(len(result['items']),500)
        self.assertEqual(len(events[-1][1]['items_snapshot']),500)
        self.assertLessEqual(sum(t=='agent_run_progress' for t,_ in events),3)
        self.assertEqual([d['done'] for t,d in events if t=='agent_run_progress'][-1],99)
        self.assertTrue(any('64 KB' in s for s in result['log_tail']))


if __name__ == '__main__':
    unittest.main()
