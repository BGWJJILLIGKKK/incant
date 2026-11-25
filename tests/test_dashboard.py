import json
import os
import shutil
import threading
import time
from http.server import ThreadingHTTPServer
from unittest.mock import Mock

import httpx
import pytest

from incant.config import load_config
from incant.review.daemon import Dashboard, make_dashboard_handler


@pytest.fixture
def install(tmp_path, monkeypatch):
    monkeypatch.delenv("INCANT_HOME", raising=False)
    monkeypatch.delenv("INCANT_LLM", raising=False)
    monkeypatch.delenv("INCANT_PATH_ROOTS", raising=False)
    monkeypatch.setenv("INCANT_DATA", str(tmp_path / "data"))
    base = tmp_path / "incant.toml"
    base.write_text('[pipeline]\nattribute = "tags"\nsynthesize = "dummy"\n')
    return base


def test_config_can_edit_base_and_local_and_reject_invalid_edits(install):
    dashboard = Dashboard(load_config(install), install)
    try:
        files = {file["id"]: file for file in dashboard.config_files()}
        assert files["base"]["text"] == install.read_text()
        assert files["local"]["text"] == ""
        dashboard.save_config({"id": "local", "text": '[pipeline]\noutput = "mp3"\n'})
        assert load_config(install).pipeline["output"] == "mp3"
        old = install.read_text()
        with pytest.raises(ValueError):
            dashboard.save_config({"id": "base", "text": "[pipeline\n"})
        assert install.read_text() == old
        dashboard.save_config({"id": "base", "text": '[pipeline]\nsynthesize = "dummy"\n'})
        assert "attribute" not in load_config(install).pipeline
        with pytest.raises(ValueError):
            dashboard.arguments({"source": str(install)})
    finally:
        dashboard.close()


def test_data_base_replacement_keeps_installation_resource_paths(install, monkeypatch):
    monkeypatch.chdir(install.parent)
    cfg = load_config()
    replacement = cfg.paths.data_dir / "incant.toml"
    edited = '[pipeline]\nsynthesize = "dummy"\noutput = "chapters"\n[paths]\nvoices_dir = "voices"\n'
    assert load_config(edits={replacement: edited}, publish_secrets=False).pipeline["output"] == "chapters"
    assert not replacement.exists()
    replacement.parent.mkdir()
    replacement.write_text(edited)
    new = load_config()
    assert new.paths.voices_dir == install.parent / "voices"
    assert new.pipeline["output"] == "chapters"
    monkeypatch.setenv("INCANT_HOME", str(install.parent))
    monkeypatch.chdir(replacement.parent)
    assert load_config().paths.voices_dir == install.parent / "voices"


def test_job_paths_use_code_data_and_launch_roots(install, monkeypatch):
    code = install.parent / "code"
    data = install.parent / "data"
    cli = install.parent / "cli-library"
    env = install.parent / "env-library"
    other = install.parent / "other"
    for root in (code, data, cli, env, other):
        root.mkdir()
        (root / f"{root.name}.txt").write_text("A short passage.")
    monkeypatch.setenv("INCANT_HOME", str(code))
    monkeypatch.setenv("INCANT_PATH_ROOTS", os.pathsep.join((str(env), str(data))))
    dashboard = Dashboard(load_config(install), install, allow_paths=(cli,))
    try:
        for root in (code, data, cli, env):
            path = root / f"{root.name}.txt"
            assert dashboard.arguments({"source": path.name})[-1] == str(path)
            args = dashboard.arguments({"source": str(path), "output": str(root / "result.m4b")})
            assert args[args.index("--out") + 1] == str(root / "result.m4b")
        target = other / "other.txt"
        with pytest.raises(ValueError, match="outside the daemon"):
            dashboard.arguments({"source": str(target)})
        alias = data / "alias.txt"
        alias.symlink_to(target)
        with pytest.raises(ValueError, match="outside the daemon"):
            dashboard.arguments({"source": str(alias)})
        with pytest.raises(ValueError, match="outside the daemon"):
            dashboard.save_config({"id": "local", "text": f"[paths]\noutput_dir = {json.dumps(str(other))}\n"})
        assert str(other) not in dashboard.settings()["path_roots"]
    finally:
        dashboard.close()


def test_external_configured_work_and_output_do_not_block_allowed_input(install):
    work = install.parent / "audiobooks" / "work"
    output = install.parent / "audiobooks" / "out"
    extra = install.parent / "Stuff"
    extra.mkdir()
    source = extra / "story.txt"
    source.write_text("A short passage.")
    book = work / "saved-book"
    book.mkdir(parents=True)
    (book / "meta.json").write_text("{}")
    install.write_text(f"[paths]\nwork_dir = {json.dumps(str(work))}\noutput_dir = {json.dumps(str(output))}\n")
    dashboard = Dashboard(load_config(install), install, allow_paths=(extra,))
    try:
        args = dashboard.arguments({"source": str(source), "output": "story.m4b"})
        assert args[-1] == str(source)
        assert args[args.index("--out") + 1] == str(output / "story.m4b")
        assert dashboard.arguments({"source": "saved-book"})[-1] == str(book)
        assert dashboard.arguments({"source": str(book)})[-1] == str(book)
        chapter = book / "0001.txt"
        chapter.write_text("A different passage.")
        assert dashboard.arguments({"source": str(chapter)})[-1] == str(chapter)
        assert dashboard.arguments({"source": "saved-book/0001.txt"})[-1] == str(chapter)
        unrelated = work.parent / "unrelated.txt"
        unrelated.write_text("Outside the configured work directory.")
        with pytest.raises(ValueError, match="outside the daemon"):
            dashboard.arguments({"source": str(unrelated)})
        dashboard.save_config({"id": "local", "text": '[pipeline]\noutput = "m4b"\n'})
    finally:
        dashboard.close()


def test_restart_marks_interrupted_jobs_and_runs_queued_jobs(install):
    cfg = load_config(install)
    text = cfg.paths.data_dir / "book.txt"
    text.parent.mkdir()
    text.write_text("The sun was rising.")
    root = text.parent / ".incant" / "jobs"
    root.mkdir(parents=True)
    for prefix, state in (("a", "running"), ("b", "queued"), ("c", "cancelled")):
        job_id = prefix * 32
        job = {"id": job_id, "state": state, "source": str(text), "mode": "plan", "created": prefix,
               "args": ["-c", str(install), "--dry-run", "--", str(text)]}
        (root / f"{job_id}.json").write_text(json.dumps(job))
    dashboard = Dashboard(cfg, install)
    try:
        assert dashboard.job("a" * 32)["state"] == "interrupted"
        for _ in range(100):
            queued = dashboard.job("b" * 32)
            if queued["state"] not in {"running", "queued"}:
                break
            time.sleep(.05)
        assert queued["state"] == "succeeded"
        assert "plan:" in queued["log"]
        assert dashboard.job("c" * 32)["state"] == "cancelled"
    finally:
        dashboard.close()


@pytest.mark.parametrize("during", ["headers", "body"])
def test_disconnected_review_response_does_not_send_a_second_response(during):
    dashboard = Mock()
    dashboard.cfg.review = load_config().review
    dashboard.book.return_value.book.return_value = {"title": "Sample"}
    handler = object.__new__(make_dashboard_handler(dashboard))
    handler.path = "/review/sample/api/book"
    handler.connection = Mock()
    handler.client_address = ("127.0.0.1", 9000)
    handler.headers = {"Host": "localhost"}
    handler.send_response = Mock()
    handler.send_header = Mock()
    handler.end_headers = Mock()
    handler.wfile = Mock()
    writer = handler.end_headers if during == "headers" else handler.wfile.write
    writer.side_effect = BrokenPipeError("client disconnected")
    handler.do_GET()
    handler.send_response.assert_called_once_with(200)
    assert handler.close_connection


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_dashboard_runs_pipeline_and_serves_review_and_export(install):
    external = install.parent / "external"
    external.mkdir()
    dashboard = Dashboard(load_config(install), install, allow_paths=(external,))
    with ThreadingHTTPServer(("127.0.0.1", 0), make_dashboard_handler(dashboard)) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False) as client:
                assert client.get("/").status_code == 200
                assert client.get("/api/config", headers={"Host": "external.test"}).status_code == 403
                assert client.post("/api/jobs", json={}, headers={"Origin": "http://external.test"}).status_code == 403
                data = b"The pond was quiet that morning."
                uploaded = client.post("/api/upload?name=sample.txt", content=data)
                assert uploaded.status_code == 201
                source = uploaded.json()["source"]
                queued = client.post("/api/jobs", json={"source": source, "output": str(external / "book.m4b")})
                assert queued.status_code == 202
                key = queued.json()["id"]
                for _ in range(200):
                    job = client.get(f"/api/jobs/{key}").json()
                    if job["state"] not in {"queued", "running"}:
                        break
                    time.sleep(.05)
                assert job["state"] == "succeeded", job
                assert "[output] m4b" in job["log"]
                [book] = client.get("/api/books").json()
                assert book["slug"] == "sample" and book["rendered"] == 1
                assert client.get(f"/review/{book['slug']}/api/book").json()["meta"]["title"] == "Sample"
                assert client.get(f"/review/{book['slug']}/api/chapter/0001").json()["audio"]
                assert client.get(f"/review/{book['slug']}/audio/chapter/0001.wav").content.startswith(b"RIFF")
                assert client.get(book["exports"][0]["url"]).content[4:8] == b"ftyp"
        finally:
            server.shutdown()
            thread.join()
            dashboard.close()
    resumed = Dashboard(load_config(install), install)
    try:
        assert resumed.job(key)["state"] == "succeeded"
    finally:
        resumed.close()


def test_clear_finished_history_keeps_queue_and_survives_restart(install):
    cfg = load_config(install)
    source = cfg.paths.data_dir / "book.txt"
    source.parent.mkdir()
    source.write_text("A short book.")
    dashboard = Dashboard(cfg, install)
    try:
        with dashboard.operation:
            cancelled = dashboard.submit({"source": str(source), "mode": "plan"})["id"]
            dashboard.cancel(cancelled)
            log = dashboard.root / f"{cancelled}.log"
            log.write_text("A finished job log.")
            queued = dashboard.submit({"source": str(source), "mode": "plan"})["id"]
            assert dashboard.clear_finished() == 1
            assert not log.exists()
            assert [job["id"] for job in dashboard.jobs_list()] == [queued]
        for _ in range(100):
            if dashboard.job(queued)["state"] == "succeeded":
                break
            time.sleep(0.05)
        assert dashboard.job(queued)["state"] == "succeeded"
    finally:
        dashboard.close()

    restarted = Dashboard(cfg, install)
    try:
        assert [job["id"] for job in restarted.jobs_list()] == [queued]
        assert source.is_file()
    finally:
        restarted.close()


def test_public_domain_requires_caddy_peer_and_https_origin():
    from incant.config import ReviewConfig
    from incant.review.daemon import request_allowed

    review = ReviewConfig(public_domains=["Books.Example.com"], trusted_proxy_ips=["192.168.1.20"])
    caddy = "192.168.1.20"
    host = "books.example.com"
    assert request_allowed(review, caddy, host, None, False)
    assert request_allowed(review, caddy, host, "https://books.example.com", True)
    assert not request_allowed(review, caddy, host, None, True)
    assert not request_allowed(review, caddy, host, "http://books.example.com", True)
    assert not request_allowed(review, caddy, "other.example.com", None, False)
    assert not request_allowed(review, "192.168.1.21", host, None, False)
    assert not request_allowed(review, caddy, "localhost", None, False)
    assert request_allowed(review, "127.0.0.1", "localhost:8765", "http://localhost:8765", True)


def test_dashboard_config_cannot_change_proxy_trust(install):
    dashboard = Dashboard(load_config(install), install)
    try:
        with pytest.raises(ValueError, match="proxy settings"):
            dashboard.save_config({"id": "local", "text": '[review]\ntrusted_proxy_ips = ["192.168.1.1"]\n'})
    finally:
        dashboard.close()
