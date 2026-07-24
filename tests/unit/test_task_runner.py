"""TaskRunner 测试：完成、失败、取消与晚到结果丢弃（只依赖 QtCore）。"""

import threading
import time

import pytest

QtCore = pytest.importorskip(
    "PySide6.QtCore", reason="当前环境无法加载 PySide6", exc_type=ImportError
)

from PySide6.QtCore import QCoreApplication  # noqa: E402

from auto_video_maker.infrastructure.task_runner import TaskRunner  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QCoreApplication:
    return QCoreApplication.instance() or QCoreApplication([])


@pytest.fixture
def runner(qapp: QCoreApplication) -> TaskRunner:
    return TaskRunner()


def wait_until(qapp: QCoreApplication, predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_success_path(qapp: QCoreApplication, runner: TaskRunner) -> None:
    results: list = []
    task_id = runner.run(lambda: 40 + 2, results.append, lambda e: results.append(e))
    assert wait_until(qapp, lambda: results)
    assert results == [42]
    assert not runner.is_active(task_id)


def test_failure_path(qapp: QCoreApplication, runner: TaskRunner) -> None:
    errors: list = []

    def failing() -> None:
        raise ValueError("失败原因")

    runner.run(failing, lambda r: errors.append(("ok", r)), errors.append)
    assert wait_until(qapp, lambda: errors)
    assert isinstance(errors[0], ValueError)


def test_cancel_drops_late_result(qapp: QCoreApplication, runner: TaskRunner) -> None:
    """测试要求 15：取消后旧结果晚到 → 丢弃。"""
    release = threading.Event()
    callbacks: list = []

    def slow() -> str:
        release.wait(timeout=5)
        return "晚到的结果"

    task_id = runner.run(slow, callbacks.append, callbacks.append)
    runner.cancel(task_id)  # 立即取消
    assert not runner.is_active(task_id)
    release.set()  # 后台任务随后完成
    # 等待足够时间让晚到结果（若未被丢弃）派发
    wait_until(qapp, lambda: callbacks, timeout=1.0)
    assert callbacks == []  # 已被丢弃


def test_new_task_not_affected_by_cancelled_old_task(
    qapp: QCoreApplication, runner: TaskRunner
) -> None:
    """测试要求 15：新任务持有新 ID，旧结果不被重新激活。"""
    release_old = threading.Event()
    old_calls: list = []
    new_calls: list = []

    old_id = runner.run(
        lambda: (release_old.wait(timeout=5), "旧结果")[1],
        old_calls.append,
        old_calls.append,
    )
    runner.cancel(old_id)

    new_id = runner.run(lambda: "新结果", new_calls.append, new_calls.append)
    assert new_id != old_id
    release_old.set()

    assert wait_until(qapp, lambda: new_calls)
    wait_until(qapp, lambda: old_calls, timeout=0.5)
    assert new_calls == ["新结果"]
    assert old_calls == []


def test_cancel_unknown_id_is_safe(runner: TaskRunner) -> None:
    runner.cancel(9999)


def test_task_ids_unique(qapp: QCoreApplication, runner: TaskRunner) -> None:
    done: list = []
    ids = [runner.run(lambda: None, done.append, done.append) for _ in range(5)]
    assert len(set(ids)) == 5
    assert wait_until(qapp, lambda: len(done) == 5)
