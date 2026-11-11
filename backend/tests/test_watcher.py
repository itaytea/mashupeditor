from app import watcher


def _make(monkeypatch, tmp_path, jobs, playlist_id="p1"):
    (tmp_path / "a.mp3").write_bytes(b"x")
    monkeypatch.setattr(watcher.settings, "load",
                        lambda: {"find_dir": str(tmp_path), "watch": True})
    monkeypatch.setattr(watcher.analysis, "find_audio_files",
                        lambda f: list(f.glob("*.mp3")))
    monkeypatch.setattr(watcher.db, "list_jobs", lambda kind, n: [])
    monkeypatch.setattr(watcher.db, "get_job", lambda i: jobs.get(i))
    state = {"pl": playlist_id}
    monkeypatch.setattr(watcher.db, "get_playlist",
                        lambda: {"id": state["pl"]} if state["pl"] else None)
    started = []
    holder = []

    def start(folder, files):
        started.append(files)
        if jobs.get(f"job{len(started)}", {}).get("status") == "done":
            holder[0].mark_checked([str(p) for p in files])  # as a real job would
        return f"job{len(started)}"

    w = watcher.FolderWatcher(start)
    holder.append(w)
    return w, started, state


def test_backoff_after_failure_and_reset_on_success(monkeypatch, tmp_path):
    jobs = {"job1": {"status": "failed"}, "job2": {"status": "done"}}
    w, started, _ = _make(monkeypatch, tmp_path, jobs)
    now = [1000.0]
    monkeypatch.setattr(watcher.time, "monotonic", lambda: now[0])
    assert w.tick() == "job1"
    assert w.tick() is None and len(started) == 1  # backing off 60s
    now[0] += watcher.BACKOFF_START + 1
    assert w.tick() == "job2"
    assert w.tick() is None  # job2 done: nothing new is retried since no failure
    assert w._failures == 0 and w._retry_at == 0.0


def test_backoff_doubles_and_caps(monkeypatch, tmp_path):
    w, _, _ = _make(monkeypatch, tmp_path, {})
    now = [0.0]
    monkeypatch.setattr(watcher.time, "monotonic", lambda: now[0])
    w._failures = 20
    w._last_job = "j"
    monkeypatch.setattr(watcher.db, "get_job", lambda i: {"status": "failed"})
    w._note_job_result()
    assert w._retry_at == watcher.BACKOFF_MAX


def test_playlist_change_resets_state(monkeypatch, tmp_path):
    jobs = {"job1": {"status": "failed"}}
    w, started, state = _make(monkeypatch, tmp_path, jobs)
    w.tick()
    w.tick()  # records failure, backs off
    assert w._failures == 1
    w._checked["x"] = 1.0
    state["pl"] = "p2"
    assert w.tick() == "job2"
    assert w._checked == {} and w._failures == 0
