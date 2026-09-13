"""Tests that execute real host processes through Runner (no adb needed)."""

import sys

import pytest

from mrt.core.errors import CommandFailed, ToolNotFound
from mrt.core.oplog import NullLog, OperationLog
from mrt.core.runner import Runner, find_tool


def test_run_captures_output_and_check():
    r = Runner(log=NullLog())
    res = r.run([sys.executable, "-c", "import sys; print('hi'); sys.stderr.write('err'); sys.exit(3)"])
    assert res.returncode == 3 and res.text.strip() == "hi" and res.err == "err" and not res.ok
    with pytest.raises(CommandFailed):
        r.run([sys.executable, "-c", "raise SystemExit(1)"], check=True)


def test_run_stdout_file_streams_binary(tmp_path):
    r = Runner(log=NullLog())
    out = tmp_path / "blob.bin"
    seen = []
    code = "import sys; sys.stdout.buffer.write(bytes(range(256)) * 20000); sys.stderr.write('x' * 100000)"
    res = r.run([sys.executable, "-c", code], stdout_file=str(out), progress=seen.append)
    assert res.ok and out.stat().st_size == 256 * 20000 == res.bytes_written
    assert seen and seen[-1] == 256 * 20000
    assert len(res.err) == 100000  # stderr drained without deadlock


def test_run_timeout_to_file(tmp_path):
    r = Runner(log=NullLog())
    res = r.run([sys.executable, "-c", "import time; time.sleep(5)"], stdout_file=str(tmp_path / "x"), timeout=0.5)
    assert res.returncode == 124


def test_dry_run_executes_nothing(tmp_path):
    r = Runner(log=NullLog(), dry_run=True)
    marker = tmp_path / "m"
    res = r.run([sys.executable, "-c", f"open({str(marker)!r}, 'w').write('x')"])
    assert res.dry_run and res.ok and not marker.exists()


def test_find_tool_missing(monkeypatch):
    monkeypatch.setenv("PATH", "")
    monkeypatch.delenv("MRT_ADB", raising=False)
    with pytest.raises(ToolNotFound):
        find_tool("adb")
    monkeypatch.setenv("MRT_ADB", sys.executable)
    assert find_tool("adb") == sys.executable


def test_operation_log_writes_jsonl(tmp_path):
    log = OperationLog(log_dir=str(tmp_path))
    log.command(["adb", "devices"], 0, 0.01)
    log.info("hello", detail="x")
    log.close()
    text = log.jsonl_path.read_text()
    assert '"kind": "command"' in text and '"argv": ["adb", "devices"]' in text and '"detail": "x"' in text
