import json
import os

from testmind import core


def task_root(consumer_root, task_id):
    return os.path.join(consumer_root or core.ROOT, ".testmind", "tasks", task_id)


def ensure_task_dir(consumer_root, task_id):
    p = task_root(consumer_root, task_id)
    os.makedirs(p, exist_ok=True)
    os.makedirs(os.path.join(p, "evidence"), exist_ok=True)
    return p


def save_snapshot(consumer_root, task_id, bundle):
    d = ensure_task_dir(consumer_root, task_id)
    path = os.path.join(d, "task.snapshot.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, ensure_ascii=False, indent=2)
    return path


def load_state(consumer_root, task_id):
    path = os.path.join(task_root(consumer_root, task_id), "state.json")
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_state(consumer_root, task_id, state):
    d = ensure_task_dir(consumer_root, task_id)
    path = os.path.join(d, "state.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    return path
