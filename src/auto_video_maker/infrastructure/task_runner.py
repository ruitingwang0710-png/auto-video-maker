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
        fn: Callable[[], Any],
        completed: Signal,
        failed: Signal,
    ) -> None:
        super().__init__()
        self._task_id = task_id
        self._fn = fn
        self._completed = completed
        self._failed = failed

    def run(self) -> None:  # noqa: D102 (QRunnable 接口)
        try:
            result = self._fn()
        except BaseException as exc:  # noqa: BLE001 上报所有失败
            self._failed.emit(self._task_id, exc)
        else:
            self._completed.emit(self._task_id, result)


class TaskRunner(QObject):
    """后台执行可调用对象，回调回到本对象所属线程。"""

    _completed = Signal(int, object)
    _failed = Signal(int, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._next_id = 1
        # task_id -> (on_success, on_error)
        self._active: dict[int, tuple[Callable[[Any], None], Callable[[Exception], None]]] = {}
        self._completed.connect(self._on_completed)
        self._failed.connect(self._on_failed)

    # ------------------------------------------------------------ API

    def run(
        self,
        fn: Callable[[], Any],
        on_success: Callable[[Any], None],
        on_error: Callable[[Exception], None],
    ) -> int:
        """提交后台任务，返回唯一 task_id。"""
        task_id = self._next_id
        self._next_id += 1
        self._active[task_id] = (on_success, on_error)
        self._pool.start(_FnRunnable(task_id, fn, self._completed, self._failed))
        logger.info("后台任务已提交：task_id=%d", task_id)
        return task_id

    def cancel(self, task_id: int) -> None:
        """取消任务：使 task_id 失效，之后到达的结果被丢弃。

        不承诺中断底层执行；底层请求最迟在其自身 timeout 后结束。
        """
        if self._active.pop(task_id, None) is not None:
            logger.info("后台任务已取消：task_id=%d", task_id)

    def is_active(self, task_id: int) -> bool:
        """任务是否仍在等待结果（未取消且未完成）。"""
        return task_id in self._active

    # ------------------------------------------------------------ 内部

    def _on_completed(self, task_id: int, result: Any) -> None:
        handlers = self._active.pop(task_id, None)
        if handlers is None:
            logger.info("丢弃已失效任务的结果：task_id=%d", task_id)
            return
        handlers[0](result)

    def _on_failed(self, task_id: int, exc: Exception) -> None:
        handlers = self._active.pop(task_id, None)
        if handlers is None:
            logger.info("丢弃已失效任务的错误：task_id=%d", task_id)
            return
        handlers[1](exc)
