#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Background task queue for processing meetings with parallel workers
"""
import os
import asyncio
from typing import Dict, Set
from datetime import datetime

# Проверяем режим работы
SERVER_MODE = os.getenv('SERVER_MODE', 'false').lower() == 'true'

if not SERVER_MODE:
    raise RuntimeError("queue should not be imported in desktop mode")

from .worker import worker


class ProcessingQueue:
    """Очередь для фоновой обработки встреч с поддержкой параллельной обработки"""

    def __init__(self, max_workers: int = 2):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.queued: Set[int] = set()
        self.processing: Set[int] = set()
        self.tasks: Dict[int, asyncio.Task] = {}
        self._worker_tasks: list[asyncio.Task] = []
        self._busy_worker_tasks: Set[asyncio.Task] = set()
        self._retire_when_idle = 0
        self.max_workers = max_workers
        self._running = False

    def set_max_workers(self, max_workers: int):
        """Изменение количества параллельных воркеров"""
        if max_workers < 1:
            max_workers = 1
        if max_workers > 4:
            max_workers = 4

        old_workers = self.max_workers
        self.max_workers = max_workers

        # Если увеличили количество воркеров, запускаем дополнительных
        if self._running and max_workers > old_workers:
            for _ in range(max_workers - old_workers):
                self._spawn_worker()
        elif self._running and max_workers < old_workers:
            active = [t for t in self._worker_tasks if not t.done()]
            excess = max(0, len(active) - max_workers)
            idle = [t for t in active if t not in self._busy_worker_tasks]
            for task in idle[:excess]:
                task.cancel()
            # Busy workers retire after their current meeting; never cancel a
            # live processor subprocess merely because the admin lowered a cap.
            self._retire_when_idle += max(0, excess - len(idle[:excess]))

    def _spawn_worker(self):
        task = asyncio.create_task(self._process_queue())
        self._worker_tasks.append(task)
        task.add_done_callback(
            lambda done: self._worker_tasks.remove(done)
            if done in self._worker_tasks else None)

    async def start(self):
        """Запуск обработчиков очереди"""
        if not self._running:
            self._running = True
            # Запускаем N параллельных воркеров
            for _ in range(self.max_workers):
                self._spawn_worker()
            print(f"Started {self.max_workers} parallel workers")

    async def stop(self):
        """Остановка обработчиков очереди"""
        self._running = False
        for task in list(self._worker_tasks):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._worker_tasks.clear()
        self._busy_worker_tasks.clear()
        self._retire_when_idle = 0
        print("All workers stopped")

    async def add_meeting(self, meeting_id: int, regenerate: bool = False):
        """Добавление встречи в очередь. ``regenerate`` пропускает транскрибацию
        (саммари/анализ заново из существующего транскрипта — новые версии)."""
        meeting_id = int(meeting_id)
        if meeting_id not in self.processing and meeting_id not in self.queued:
            self.queued.add(meeting_id)
            await self.queue.put((meeting_id, regenerate))
            print(f"Meeting {meeting_id} added to queue (regenerate={regenerate}). "
                  f"Queue size: {self.queue.qsize()}, Processing: {len(self.processing)}")

    async def cancel_meeting(self, meeting_id: int) -> str:
        """Stop a meeting that is queued or running. Returns what happened.

        A queued id cannot be pulled back out of an asyncio.Queue, so it is
        marked instead and skipped when a worker picks it up.
        """
        meeting_id = int(meeting_id)
        if meeting_id in self.processing:
            worker.cancel(meeting_id)
            return "processing"
        if meeting_id in self.queued:
            worker.cancelled.add(meeting_id)
            return "queued"
        return "idle"

    def is_processing(self, meeting_id: int) -> bool:
        """Проверка, обрабатывается ли встреча"""
        return meeting_id in self.processing

    def get_status(self) -> dict:
        """Получение статуса очереди"""
        return {
            "queue_size": self.queue.qsize(),
            "processing_count": len(self.processing),
            "max_workers": self.max_workers,
            "active_workers": len([t for t in self._worker_tasks if not t.done()]),
            "processing_meetings": list(self.processing)
        }

    async def _process_queue(self):
        """Обработчик очереди (работает в фоне)"""
        while self._running:
            try:
                # Получаем следующую встречу из очереди
                meeting_id, regenerate = await self.queue.get()
                self.queued.discard(meeting_id)

                # Помечаем как обрабатываемую
                self.processing.add(meeting_id)
                current_task = asyncio.current_task()
                if current_task is not None:
                    self._busy_worker_tasks.add(current_task)
                print(f"Worker started processing meeting {meeting_id}. Active: {len(self.processing)}")

                # Запускаем обработку
                try:
                    await worker.process_meeting(meeting_id, regenerate=regenerate)
                except Exception as e:
                    print(f"Error processing meeting {meeting_id}: {e}")
                finally:
                    # Убираем из обрабатываемых
                    self.processing.discard(meeting_id)
                    if current_task is not None:
                        self._busy_worker_tasks.discard(current_task)
                    self.queue.task_done()
                    print(f"Worker finished meeting {meeting_id}. Active: {len(self.processing)}")
                    if self._retire_when_idle > 0:
                        self._retire_when_idle -= 1
                        break

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Queue worker error: {e}")
                await asyncio.sleep(1)


# Определяем оптимальное количество воркеров
_GPU_PROBE_CODE = (
    "import json\n"
    "try:\n"
    "    import torch\n"
    "    if torch.cuda.is_available():\n"
    "        mem = torch.cuda.get_device_properties(0).total_memory / (1024**3)\n"
    "        print(json.dumps({'cuda': True, 'mem_gb': mem}))\n"
    "    else:\n"
    "        print(json.dumps({'cuda': False}))\n"
    "except Exception as e:\n"
    "    print(json.dumps({'cuda': False, 'error': str(e)[:200]}))\n"
)


def _probe_gpu() -> dict:
    """Probe CUDA + VRAM via the backend runtime (which has torch); the server venv
    is torch-free, so an in-process ``import torch`` would always fail → CPU. Never
    raises: any failure returns ``{}`` (caller treats as CPU)."""
    import subprocess
    import json as _json
    from ..runtime import backend_python
    py = backend_python()
    if not py.exists():
        return {}
    try:
        proc = subprocess.run([str(py), "-c", _GPU_PROBE_CODE],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=90)
        out = (proc.stdout or "").strip().splitlines()
        return _json.loads(out[-1]) if out else {}
    except Exception as e:  # noqa: BLE001
        print(f"GPU probe failed: {e}")
        return {}


def get_optimal_workers() -> int:
    """Автоопределение оптимального количества параллельных воркеров.

    GPU is probed via the embedded python (server venv has no torch). VRAM tiers:
    ≥8 GB → 4, ≥6 GB → 3, else 2. No CUDA → CPU: ≥8 cores → 2, else 1."""
    try:
        info = _probe_gpu()
        if info.get("cuda"):
            gpu_memory = float(info.get("mem_gb") or 0)
            if gpu_memory >= 8:
                return 4
            elif gpu_memory >= 6:
                return 3
            else:
                return 2
        import multiprocessing
        return 2 if multiprocessing.cpu_count() >= 8 else 1
    except Exception as e:  # noqa: BLE001
        print(f"Error detecting optimal workers: {e}")
        return 2


# Глобальный экземпляр очереди с автоопределением воркеров
optimal_workers = get_optimal_workers()
print(f"Optimal workers detected: {optimal_workers}")
processing_queue = ProcessingQueue(max_workers=optimal_workers)

