# -*- coding=utf-8 -*-
import logging
import re
import threading

from zettarepl.utils.shlex import implode

from .interface import *

logger = logging.getLogger(__name__)

__all__ = ["ProgressReportMixin"]


class ProgressReportMixin:
    stop_progress_observer = None

    def _get_send_shell(self):
        raise NotImplementedError

    def _zfs_send_can_report_progress(self):
        send_shell = self._get_send_shell()

        try:
            send_shell.exec(["zfs", "send", "-V"])
        except ExecException as e:
            if "missing snapshot argument" in e.stdout:
                # Option is supported (patched zfs on FreeNAS)
                return True
            else:
                # invalid option 'V'
                return False
        else:
            return False

    def _wrap_send(self, send):
        return ["sh", "-c", "(" + implode(send) + " & PID=$!; echo \"zettarepl: zfs send PID is $PID\" 1>&2; "
                            "wait $PID)"]

    def _start_progress_observer(self):
        self.stop_progress_observer = threading.Event()

        try:
            pid = self.async_exec.head(self._get_zettarepl_pid, 10)
        except TimeoutError:
            raise TimeoutError("Timeout waiting for `zfs send` to start")

        threading.Thread(daemon=True, name=f"{threading.current_thread().name}.progress_observer",
                         target=self._progress_observer, args=(pid,)).start()

    def _stop_progress_observer(self):
        if self.stop_progress_observer:
            self.stop_progress_observer.set()

    def _get_zettarepl_pid(self, line):
        m = re.match("zettarepl: zfs send PID is ([0-9]+)", line.strip())
        if m:
            return int(m.group(1))

    def _progress_observer(self, pid):
        try:
            send_shell = self._get_send_shell()

            while True:
                if self.stop_progress_observer.wait(10):
                    return

                try:
                    s = send_shell.exec(["ps", "-o", "command", "-p", str(pid)])
                except ExecException as e:
                    if e.returncode == 1 and e.stdout.strip() == "COMMAND":
                        logger.debug("zfs send with PID %r is gone", pid)
                        return

                    raise

                m = re.search(r"zfs: sending (?P<snapshot>.+) \([0-9]+%: (?P<current>[0-9]+)/(?P<total>[0-9]+)\)", s)
                if m:
                    current = int(m.group("current"))
                    total = int(m.group("total"))
                    if total == 0:
                        total = current + 1
                    self.notify_progress_observer(current, total)
                else:
                    logger.debug("Unable to find ZFS send progress in %r", s)
        except Exception:
            logger.error("Unhandled exception in progress observer", exc_info=True)
