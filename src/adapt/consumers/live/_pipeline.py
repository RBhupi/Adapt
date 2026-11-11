# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Pipeline subprocess control — start, stop, observe, reconnect — and the
Log tab that displays its output. The dashboard shell delegates every
pipeline concern here; this controller never touches the map tabs.
"""

import contextlib
import logging
import os
import platform
import subprocess
import threading
import time
import tkinter as tk
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import IO

from adapt.configuration.schemas.errors import ConfigError
from adapt.consumers.live._context import AppContext
from adapt.consumers.live._timers import AfterHandles
from adapt.consumers.live._utils import (
    _pipeline_pid_from_file,
    _pipeline_running,
    init_store_cmd,
    is_repository,
    safe_close,
    write_dashboard_pipeline,
)
from adapt.utils.process import detached_process_kwargs, terminate_process_tree

logger = logging.getLogger(__name__)

LOG_MAX = 500
LOG_FILE = Path.home() / ".adapt" / "pipeline.log"


class PipelineController:
    """Start, stop, observe, and reconnect the adapt pipeline subprocess."""

    def __init__(
        self,
        app: tk.Tk,
        nb: ttk.Notebook,
        ctx: AppContext,
        *,
        badge: tk.Label,
        status_var: tk.StringVar,
        adopt_repo: Callable[[str], None],
    ):
        self.app = app
        self.ctx = ctx
        self._badge = badge
        self._status_var = status_var
        self._adopt_repo = adopt_repo  # select a repo in the toolbar + re-scan it
        self._proc: subprocess.Popen | None = None
        self._log_lines: list[str] = []
        self._log_file_handle: IO[str] | None = None
        self._active = True  # tail threads exit when this goes False
        self._timers = AfterHandles(self.app.after, self.app.after_cancel)
        self._build_log_tab(nb)

    # ── Log tab ───────────────────────────────────────────────────────────────

    def _build_log_tab(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb)
        nb.add(tab, text="Log")

        ctrl = ttk.Frame(tab, padding=4)
        ctrl.pack(side="top", fill="x")
        ttk.Button(ctrl, text="Refresh", command=self.flush_log).pack(side="left")
        ttk.Button(ctrl, text="Clear", command=self._clear_log).pack(side="left", padx=4)
        ttk.Separator(ctrl, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(ctrl, text="■ Stop Pipeline", command=self.stop).pack(side="left")

        self.log_text = scrolledtext.ScrolledText(
            tab,
            state="disabled",
            wrap="none",
            font=("Courier", 11),
            background="#1e1e1e",
            foreground="#d4d4d4",
        )
        self.log_text.pack(fill="both", expand=True)
        self.log_text.tag_config("error", foreground="#f44747")
        self.log_text.tag_config("warning", foreground="#dcdcaa")
        self.log_text.tag_config("info", foreground="#9cdcfe")

    def _append_log(self, line, tag=""):
        self.log_text.config(state="normal")
        self.log_text.insert("end", line + "\n", tag)
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def flush_log(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        for ln in self._log_lines[-200:]:
            tag = "error" if "ERROR" in ln else ("warning" if "WARNING" in ln else "")
            self.log_text.insert("end", ln + "\n", tag)
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _clear_log(self):
        self._log_lines.clear()
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")

    # ── Run Adapt wizard ──────────────────────────────────────────────────────

    def open_wizard(self) -> None:
        import webbrowser

        win = tk.Toplevel(self.app)
        win.title("Start New Pipeline")
        win.resizable(False, False)

        path_var = tk.StringVar(value=self.ctx.repo())
        radar_var = tk.StringVar(value=self.ctx.collection())
        mode_var = tk.StringVar(value="realtime")
        start_var = tk.StringVar(value="")
        end_var = tk.StringVar(value="")

        # ── Store folder + Browse ─────────────────────────────────────────────
        ttk.Label(win, text="Store folder:").grid(row=0, column=0, padx=8, pady=(12, 4), sticky="w")
        ttk.Entry(win, textvariable=path_var, width=42).grid(row=0, column=1, padx=4, pady=(12, 4))

        def _browse():
            chosen = filedialog.askdirectory(title="Select the store folder", parent=win)
            if chosen:
                path_var.set(chosen)

        ttk.Button(win, text="Browse…", command=_browse).grid(
            row=0, column=2, padx=(2, 8), pady=(12, 4)
        )

        # ── Radar ID ──────────────────────────────────────────────────────────
        ttk.Label(win, text="Radar ID:").grid(row=1, column=0, padx=8, pady=(8, 4), sticky="w")
        ttk.Entry(win, textvariable=radar_var, width=10).grid(
            row=1, column=1, padx=4, pady=(8, 4), sticky="w"
        )

        # ── Mode ──────────────────────────────────────────────────────────────
        ttk.Label(win, text="Mode:").grid(row=2, column=0, padx=8, pady=4, sticky="w")
        mode_f = ttk.Frame(win)
        mode_f.grid(row=2, column=1, pady=4, sticky="w")
        ttk.Radiobutton(
            mode_f,
            text="Realtime",
            variable=mode_var,
            value="realtime",
            command=lambda: _toggle_time(),
        ).pack(side="left")
        ttk.Radiobutton(
            mode_f,
            text="Historical",
            variable=mode_var,
            value="historical",
            command=lambda: _toggle_time(),
        ).pack(side="left", padx=8)

        # ── Historical time range (hidden unless historical) ──────────────────
        time_frame = ttk.Frame(win)
        time_frame.grid(row=3, column=0, columnspan=3, padx=8, pady=(0, 4), sticky="ew")
        ttk.Label(time_frame, text="Start (UTC):").grid(row=0, column=0, padx=(0, 4), sticky="w")
        ttk.Entry(time_frame, textvariable=start_var, width=20).grid(row=0, column=1, padx=(0, 12))
        ttk.Label(time_frame, text="End (UTC):").grid(row=0, column=2, padx=(0, 4), sticky="w")
        ttk.Entry(time_frame, textvariable=end_var, width=20).grid(row=0, column=3)
        ttk.Label(
            time_frame, text="ISO 8601, e.g. 2025-06-17T18:00:00Z", font=("", 8), foreground="gray"
        ).grid(row=1, column=0, columnspan=4, sticky="w")

        # ── What launching does + docs link ──────────────────────────────────
        ttk.Label(
            win,
            text="Launch writes dashboard.yaml in the store folder (the nexrad template "
            "with these values) and runs 'adapt run dashboard' there. For other "
            "settings, edit nexrad.yaml and run 'adapt run nexrad' in that folder.",
            wraplength=400,
            justify="left",
        ).grid(row=4, column=0, columnspan=3, padx=12, pady=(4, 2), sticky="w")
        docs = ttk.Label(
            win,
            text="See the Adapt CLI reference",
            foreground="#4a9fd4",
            cursor="hand2",
        )
        docs.grid(row=5, column=0, columnspan=3, padx=8, pady=4)
        docs.bind(
            "<Button-1>",
            lambda _: webbrowser.open("https://arm-doe.github.io/Adapt/cli_reference.html"),
        )

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_frame = ttk.Frame(win)
        btn_frame.grid(row=6, column=0, columnspan=3, pady=(4, 12))
        ttk.Button(btn_frame, text="Cancel", command=win.destroy).pack(side="left", padx=8)
        ttk.Button(
            btn_frame,
            text="Launch Pipeline",
            command=lambda: self._launch_pipeline_from_wizard(
                path_var.get().strip(),
                radar_var.get().strip(),
                mode_var.get(),
                start_var.get().strip(),
                end_var.get().strip(),
                win,
            ),
        ).pack(side="left", padx=8)

        def _toggle_time():
            if mode_var.get() == "historical":
                time_frame.grid()
            else:
                time_frame.grid_remove()

        _toggle_time()

    def _launch_pipeline_from_wizard(
        self,
        path: str,
        radar: str,
        mode: str,
        start: str,
        end: str,
        wizard_win,
    ) -> None:
        """Make the folder a store, write its dashboard.yaml, and run it there."""
        if not path or not radar:
            messagebox.showerror(
                "Missing input", "Enter a store folder and a radar ID.", parent=wizard_win
            )
            return
        if mode == "historical" and not (start and end):
            messagebox.showerror(
                "Missing input",
                "Historical mode needs a start and an end time.",
                parent=wizard_win,
            )
            return

        # ── Check for any already-running pipeline ────────────────────────────
        running_pid, running_proc = self._find_running_pipeline()
        if running_pid is not None:
            kill = messagebox.askyesno(
                "Pipeline already running",
                f"A pipeline is already running (PID {running_pid}).\n\nKill it and continue?",
                parent=wizard_win,
            )
            if not kill:
                return
            if running_proc is not None:
                with contextlib.suppress(Exception):
                    running_proc.terminate()
                    running_proc.wait(timeout=5)
            else:
                with contextlib.suppress(OSError):
                    os.kill(running_pid, 15)
            return  # user clicks Launch Pipeline again once old process is gone

        root = Path(path)
        if not root.is_dir():
            messagebox.showerror(
                "Not a directory", f"Expected a directory:\n{path}", parent=wizard_win
            )
            return

        # `adapt run` refuses a folder that is not a store: initialize it first
        # (a legacy pre-store root surfaces the explicit obsolete-layout error).
        if not self._ensure_store(root, wizard_win):
            return

        run = {"radar": radar, "mode": mode}
        if mode == "historical":
            run.update(start_time=start, end_time=end)
        try:
            cmd = write_dashboard_pipeline(root, **run)
        except ConfigError as exc:
            messagebox.showerror("Invalid pipeline settings", str(exc), parent=wizard_win)
            return

        # Auto-select the repo in the dashboard so panels load from this run
        self._adopt_repo(str(root))

        wizard_win.destroy()
        self._launch_pipeline(cmd, root)
        if self._proc is not None:
            messagebox.showinfo(
                "Pipeline started",
                f"Adapt pipeline running (PID {self._proc.pid}).\n"
                f"Output is streamed to the Log tab and saved to:\n{LOG_FILE}",
                parent=self.app,
            )

    # ── Launch / observe / stop ───────────────────────────────────────────────

    @staticmethod
    def _ensure_store(root: Path, parent) -> bool:
        """Run ``adapt init`` for a fresh directory; surface failures loudly."""
        if is_repository(root):
            return True
        result = subprocess.run(init_store_cmd(root), capture_output=True, text=True)
        if result.returncode != 0:
            messagebox.showerror(
                "Store initialization failed",
                result.stderr.strip() or result.stdout.strip() or "adapt init failed",
                parent=parent,
            )
            return False
        return True

    def _launch_pipeline(self, cmd: list, cwd: Path) -> None:
        """Launch ``cmd`` in ``cwd``, redirect all output to LOG_FILE, start watcher threads."""
        self._log_lines = []
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        # errors="replace": this handle receives a subprocess's raw output, which
        # we do not control — an undecodable byte must not abort the launch.
        log_handle = LOG_FILE.open("w", buffering=1, encoding="utf-8", errors="replace")
        self._log_file_handle = log_handle
        try:
            self._proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                **detached_process_kwargs(),
            )
        except Exception as exc:
            logger.exception("Failed to launch pipeline: %s", cmd)
            log_handle.close()
            self._log_file_handle = None
            # Show the command and the environment, not just the exception: this
            # is where an OS-level launch failure surfaces (on Windows a bad
            # interpreter or dependency reads as a bare "[WinError N]"), and the
            # command plus platform is what makes such a report actionable.
            messagebox.showerror(
                "Launch failed",
                f"{type(exc).__name__}: {exc}\n\n"
                f"Command: {' '.join(str(part) for part in cmd)} (in {cwd})\n"
                f"Platform: {platform.platform()} · Python {platform.python_version()}\n\n"
                f"Full traceback in {LOG_FILE}",
                parent=self.app,
            )
            return
        self._append_log(
            f"[{datetime.now():%H:%M:%S}] Pipeline started (PID {self._proc.pid})", "info"
        )
        self._append_log(f"  Log: {LOG_FILE}", "info")
        self._start_log_tail(LOG_FILE)
        self._start_proc_watcher(self._proc)
        self.update_badge()

    def _start_log_tail(self, log_path: Path) -> None:
        """Daemon thread: tail log_path and append new lines to the log display."""

        def _tail():
            try:
                with log_path.open("r", encoding="utf-8", errors="replace") as f:
                    f.seek(0, 2)  # start at end — don't replay old content
                    while self._active:
                        line = f.readline()
                        if line:
                            line = line.rstrip()
                            self._log_lines.append(line)
                            if len(self._log_lines) > LOG_MAX:
                                self._log_lines.pop(0)
                            tag = (
                                "error"
                                if "ERROR" in line
                                else "warning"
                                if "WARNING" in line
                                else ""
                            )
                            self.app.after(0, self._append_log, line, tag)
                        else:
                            if self._proc is None or self._proc.poll() is not None:
                                break
                            time.sleep(0.15)
            except Exception:
                logger.exception("Log tail thread failed")

        threading.Thread(target=_tail, daemon=True, name="LogTail").start()

    def _start_log_tail_from_end(self, log_path: Path, last_n: int = 200) -> None:
        """Tail log_path starting from the last *last_n* lines (for reconnect)."""

        def _tail_reconnect():
            try:
                with log_path.open("r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                    for ln in lines[-last_n:]:
                        ln = ln.rstrip()
                        self._log_lines.append(ln)
                        tag = "error" if "ERROR" in ln else "warning" if "WARNING" in ln else ""
                        self.app.after(0, self._append_log, ln, tag)
                    # Continue tailing from current position
                    while self._active and _pipeline_running():
                        line = f.readline()
                        if line:
                            line = line.rstrip()
                            self._log_lines.append(line)
                            if len(self._log_lines) > LOG_MAX:
                                self._log_lines.pop(0)
                            tag = (
                                "error"
                                if "ERROR" in line
                                else "warning"
                                if "WARNING" in line
                                else ""
                            )
                            self.app.after(0, self._append_log, line, tag)
                        else:
                            time.sleep(0.2)
            except Exception:
                logger.exception("Reconnect log tail thread failed")

        threading.Thread(target=_tail_reconnect, daemon=True, name="LogTailReconnect").start()

    def _start_proc_watcher(self, proc: subprocess.Popen) -> None:
        """Daemon thread: block on proc.wait(), then fire _on_proc_ended on the main thread."""

        def _watch():
            proc.wait()
            if self._log_file_handle is not None:
                safe_close(self._log_file_handle, "pipeline log file", logger)
                self._log_file_handle = None
            self.app.after(0, self._on_proc_ended)

        threading.Thread(target=_watch, daemon=True, name="ProcWatcher").start()

    def stop(self) -> None:
        """Terminate the pipeline process group; escalate to a hard kill after 5 s."""
        if self._proc is None or self._proc.poll() is not None:
            # No owned process — try PID-file-only external process
            pid = _pipeline_pid_from_file()
            if pid is not None:
                with contextlib.suppress(OSError):
                    os.kill(pid, 15)
            self._on_proc_ended()
            return
        self._status_var.set("Stopping pipeline…")
        proc = self._proc

        def _do_kill():
            terminate_process_tree(proc, force=False)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                terminate_process_tree(proc, force=True)

        threading.Thread(target=_do_kill, daemon=True).start()

    def _on_proc_ended(self) -> None:
        rc = self._proc.returncode if self._proc else None
        self._proc = None
        rc_str = f"exit {rc}" if rc is not None else "unknown"
        self._status_var.set(f"Stopped  |  {self.ctx.collection()}")
        self._append_log(f"[{datetime.now():%H:%M:%S}] Pipeline ended ({rc_str})", "info")
        self.update_badge()
        self.flush_log()

    def _find_running_pipeline(self) -> tuple[int | None, subprocess.Popen | None]:
        """Return (pid, proc) for the active pipeline, or (None, None) if idle."""
        if self._proc is not None and self._proc.poll() is None:
            return self._proc.pid, self._proc
        pid = _pipeline_pid_from_file()
        if pid is not None and _pipeline_running():
            return pid, None
        return None, None

    def is_running(self) -> bool:
        """True when this session's pipeline or a PID-file pipeline is alive."""
        return (self._proc is not None and self._proc.poll() is None) or _pipeline_running()

    def update_badge(self) -> None:
        if self.is_running():
            self._badge.config(text="● Pipeline running", fg="#4daf4a")
        else:
            self._badge.config(text="○ Idle", fg="#888888")

    # ── Reconnect to an external pipeline ─────────────────────────────────────

    def check_reconnect(self) -> None:
        """Offer to reconnect to an externally running pipeline on startup."""
        if not _pipeline_running() or self._proc is not None:
            return
        pid = _pipeline_pid_from_file()
        if pid is None:
            return
        ans = messagebox.askyesno(
            "Pipeline already running",
            f"Adapt pipeline (PID {pid}) is already running.\n\nReconnect to its log output?",
            parent=self.app,
        )
        if ans:
            self._reconnect_pipeline(pid)

    def _reconnect_pipeline(self, pid: int) -> None:
        """Attach log tail to a pipeline started outside this GUI session."""
        self._append_log(f"[{datetime.now():%H:%M:%S}] Reconnected to pipeline PID {pid}", "info")
        self.update_badge()
        if LOG_FILE.exists():
            self._start_log_tail_from_end(LOG_FILE, last_n=200)
        self._timers.recurring("poll", 2000, lambda: self._poll_external_pid(pid))

    def _poll_external_pid(self, pid: int) -> None:
        """Poll every 2 s for death of an external (PID-file-only) pipeline."""
        if not _pipeline_running():
            self._on_proc_ended()
            return
        self._timers.recurring("poll", 2000, lambda: self._poll_external_pid(pid))

    # ── Shutdown ──────────────────────────────────────────────────────────────

    def confirm_close(self) -> bool:
        """If the owned pipeline is still running, ask what to do.

        Returns False when the user chooses to stay in the dashboard.
        """
        if self._proc and self._proc.poll() is None:
            pid = self._proc.pid
            choice = messagebox.askyesnocancel(
                "Pipeline running",
                f"Adapt pipeline (PID {pid}) is still running.\n\n"
                "Yes → Kill it now\n"
                "No  → Keep it running in the background\n"
                "Cancel → Stay in dashboard",
                parent=self.app,
            )
            if choice is None:
                return False
            if choice:
                terminate_process_tree(self._proc, force=False)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    self._proc.wait(timeout=3)
        return True

    def shutdown(self) -> None:
        """Stop tail threads, cancel polls, release the log handle."""
        self._active = False
        self._timers.cancel_all()
        if self._log_file_handle is not None:
            safe_close(self._log_file_handle, "pipeline log file", logger)
            self._log_file_handle = None
