import threading
import queue
import logging
import uuid
from typing import Callable, Any, Dict

logger = logging.getLogger(__name__)

class BackgroundWorker:
    """Encapsulates background task execution."""
    def __init__(self):
        self._results_queue = queue.Queue()
        self._tasks: Dict[str, threading.Thread] = {}

    def submit(self, task_id: str, func: Callable, *args, **kwargs):
        """Submit a task to run in a background thread."""
        if task_id in self._tasks and self._tasks[task_id].is_alive():
            logger.warning(f"Task {task_id} is already running.")
            return

        def _worker_wrapper():
            try:
                result = func(*args, **kwargs)
                self._results_queue.put({'task_id': task_id, 'status': 'done', 'result': result})
            except Exception as e:
                logger.error(f"Task {task_id} failed: {e}")
                self._results_queue.put({'task_id': task_id, 'status': 'error', 'error': str(e)})

        thread = threading.Thread(target=_worker_wrapper, daemon=True)
        self._tasks[task_id] = thread
        thread.start()
        logger.info(f"Started background task {task_id}")

    def get_result(self, block: bool = False, timeout: float = None) -> Any:
        """Poll for a completed task result."""
        try:
            return self._results_queue.get(block=block, timeout=timeout)
        except queue.Empty:
            return None
