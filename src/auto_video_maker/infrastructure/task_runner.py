"""后台任务执行器：基于 task_id（generation token）的取消语义。

- 每次任务分配唯一 task_id；取消使该 ID 失效
- 旧任务晚到的成功/失败回调因 ID 失效被丢弃
- 新任务持有新 ID，不能重新激活旧任务结果
- 回调经 Qt 队列信号回到创建 TaskRunner 的线程（主线程）
- 只依赖 QtCore，不依赖图形栈
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

logger = logging.getLogger(__name__)


class _FnRunnable(QRunnable):
    """在线程池中执行函数并经信号上报结果。"""

    def __init__(
        self,
        task_id: int,
        fn: Callable[..., Any],
        completed: Signal,
        failed: Signal,
        progress_reporter: Callable[[int], None] | None = None,
    ) -> None:
        super().__init__()
        self._task_id = task_id
        self._fn = fn
        self._completed = completed
        self._failed = failed
        self._progress_reporter = progress_reporter

    def run(self) -> None:  # noqa: D102 (QRunnable 接口)
        try:
            if self._progress_reporter is not None:
                result = self._fn(self._progress_reporter)
            else:
                result = self._fn()
        except BaseException as exc:  # noqa: BLE001 上报所有失败
            self._failed.emit(self._task_id, exc)
        else:
            self._completed.emit(self._task_id, result)


class TaskRunner(QObject):
    """后台执行可调用对象，回调回到本对象所属线程。"""

    _completed = Signal(int, object)
    _failed = Signal(int, object)
    _progressed = Signal(int, int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._next_id = 1
        # task_id -> (on_success, on_error, on_progress|None)
        self._active: dict[int, tuple[Callable[[Any], None], Callable[[Exception], None], Callable[[int], None] | None]] = {}
        # task_id -> 已派发的最大进度（保证单调不减 + 节流重复值）
        self._last_progress: dict[int, int] = {}
        self._completed.connect(self._on_completed)
        self._failed.connect(self._on_failed)
        self._progressed.connect(self._on_progressed)

    # ------------------------------------------------------------ API

    def run(
        self,
        fn: Callable[..., Any],
        on_success: Callable[[Any], None],
        on_error: Callable[[Exception], None],
        on_progress: Callable[[int], None] | None = None,
    ) -> int:
        """提交后台任务，返回唯一 task_id。

        on_progress（可选）：提供时 fn 以 fn(report_progress) 形式调用，
        report_progress(percent: 0–100) 可在工作线程内安全调用；
        进度经队列信号回到主线程，单调不减、重复值节流，
        任务成功/失败/取消后不再派发。
        未提供 on_progress 时 fn 以零参调用，与既有行为完全一致。
        """
        task_id = self._next_id
        self._next_id += 1
        self._active[task_id] = (on_success, on_error, on_progress)
        self._last_progress[task_id] = -1
        reporter = None
        if on_progress is not None:
            def reporter(percent: int, _task_id: int = task_id) -> None:
                self._progressed.emit(_task_id, int(percent))
        self._pool.start(
            _FnRunnable(task_id, fn, self._completed, self._failed, reporter)
        )
        logger.info("后台任务已提交：task_id=%d", task_id)
        return task_id

    def cancel(self, task_id: int) -> None:
        """取消任务：使 task_id 失效，之后到达的结果被丢弃。

        不承诺中断底层执行；底层请求最迟在其自身 timeout 后结束。
        """
        if self._active.pop(task_id, None) is not None:
            self._last_progress.pop(task_id, None)
            logger.info("后台任务已取消：task_id=%d", task_id)

    def is_active(self, task_id: int) -> bool:
        """任务是否仍在等待结果（未取消且未完成）。"""
        return task_id in self._active

    # ------------------------------------------------------------ 内部

    def _on_completed(self, task_id: int, result: Any) -> None:
        handlers = self._active.pop(task_id, None)
        self._last_progress.pop(task_id, None)
        if handlers is None:
            logger.info("丢弃已失效任务的结果：task_id=%d", task_id)
            return
        handlers[0](result)

    def _on_failed(self, task_id: int, exc: Exception) -> None:
        handlers = self._active.pop(task_id, None)
        self._last_progress.pop(task_id, None)
        if handlers is None:
            logger.info("丢弃已失效任务的错误：task_id=%d", task_id)
            return
        handlers[1](exc)

    def _on_progressed(self, task_id: int, percent: int) -> None:
        handlers = self._active.get(task_id)
        if handlers is None or handlers[2] is None:
            return  # 已取消/已完成：不再派发进度
        clamped = max(0, min(100, percent))
        if clamped <= self._last_progress.get(task_id, -1):
            return  # 单调不减 + 重复节流
        self._last_progress[task_id] = clamped
        handlers[2](clamped)
