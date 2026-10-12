"""py test_proc_snapshot.py - the one-call process list the reaper and the spawn memory check share."""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L  # noqa: E402


class ProcSnapshot(unittest.TestCase):
    def test_lists_this_process_with_its_parent_and_a_sane_start_time(self):
        table, cmds = L.proc_snapshot()
        me = table[os.getpid()]
        self.assertEqual(me["ppid"], os.getppid())
        self.assertIn("python", me["name"].lower())
        self.assertLess(abs(me["ctime"] - time.time()), 600)
        self.assertGreater(me["rss"], 1_000_000)
        self.assertIn(os.path.basename(sys.orig_argv[-1]), cmds[os.getpid()])

    def test_is_one_call_not_a_per_process_walk(self):
        """psutil.process_iter(create_time, memory_info) took 10-40 s here; the CIM query ~1 s."""
        t = time.time()
        L.proc_snapshot()
        self.assertLess(time.time() - t, 15)

    def test_falls_back_to_psutil_when_powershell_fails(self):
        import psutil
        fake = mock.Mock(info={"pid": 42, "ppid": 1, "name": "x.exe", "create_time": 5.0,
                               "cmdline": ["x.exe", "a b"], "memory_info": mock.Mock(rss=7)})
        with mock.patch.object(L.subprocess, "run", side_effect=OSError("no pwsh")), \
                mock.patch.object(psutil, "process_iter", return_value=[fake]):
            table, cmds = L.proc_snapshot()
        self.assertEqual(table, {42: {"ppid": 1, "name": "x.exe", "ctime": 5.0, "rss": 7}})
        self.assertEqual(cmds, {42: 'x.exe "a b"'})


if __name__ == "__main__":
    unittest.main()
