"""
test_memurai_client.py — Memurai CLI 客户端单元测试

全程 mock subprocess.run，**不真连 memurai**。

覆盖范围（≥30 测试）：
- Memurai.__init__       — 默认/带参/不存在路径/超时上限
- Memurai._run           — 正常/超时/FileNotFound/非零退出码/check_error=False/带密码 DB
- Memurai.ping           — PONG / 非 PONG
- Memurai.set            — 基本/EX/PX/失败
- Memurai.get            — 字符串 / nil
- Memurai.mset           — 基本 / 空 dict
- Memurai.delete         — 多键 / 无键
- Memurai.exists         — 1 / 0
- Memurai.scan           — pattern 传递 / 多行输出
- Memurai.pipe_setex_batch — 空 / +OK 计数 / replies 汇总 / -ERR 抛错
- Memurai.mget           — 基本 / 空 / (nil) 处理
- _parse_reply           — OK/nil/integer/integer非法/error/string/empty
- _encode_* 函数         — bulk_string / array / command（int+str+bytes）
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from subprocess import CompletedProcess
from typing import Iterable
from unittest.mock import patch

import pytest

# 把 scripts/redis 加入 sys.path，让 `import memurai_client` 可用。
# 不依赖外部 conftest.py，测试文件自洽。
_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "redis"))

from memurai_client import (  # noqa: E402
    Memurai,
    MemuraiError,
    _encode_array,
    _encode_bulk_string,
    _encode_command,
    _parse_reply,
)


# =============================================================================
# 辅助
# =============================================================================

def _completed(
    stdout: object = b"PONG\n",
    stderr: object = b"",
    returncode: int = 0,
) -> CompletedProcess:
    """构造一个像 subprocess.run 返回的 CompletedProcess。"""
    return CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture
def cli() -> Memurai:
    """提供一个 Memurai 实例：cli_path 用名称（绕过文件存在性检查）。"""
    return Memurai(cli_path=Path("memurai-cli.exe"))


# =============================================================================
# Memurai.__init__
# =============================================================================

class TestInit:
    def test_init_default(self):
        """无参初始化使用模块级 CLI_PATH（默认即 memurai-cli.exe，存在性靠名称豁免）。"""
        c = Memurai()
        assert c.host == "localhost"
        assert c.port == 6379
        assert c.password is None
        assert c.db == 0
        assert c.exit_on_error is True

    def test_init_with_existing_path(self, tmp_path):
        """传一个真实存在的路径，应跳过名称豁免判断。"""
        real = tmp_path / "fake-cli.exe"
        real.write_bytes(b"")
        c = Memurai(cli_path=real, host="h1", port=1234, password="pw", db=7)
        assert c.host == "h1"
        assert c.port == 1234
        assert c.password == "pw"
        assert c.db == 7

    def test_init_nonexistent_raises(self):
        """cli_path 不存在且不以 memurai-cli.exe 结尾 → MemuraiError。"""
        with pytest.raises(MemuraiError, match="memurai-cli 不存在"):
            Memurai(cli_path=Path("C:/definitely/not/here/foobar.txt"))

    def test_init_timeout_capped_to_300(self):
        """timeout 超过 5 分钟硬上限应被夹紧到 300.0。"""
        c = Memurai(cli_path=Path("memurai-cli.exe"), timeout=999.0)
        assert c.timeout == 300.0

    def test_init_exit_on_error_false(self):
        c = Memurai(cli_path=Path("memurai-cli.exe"), exit_on_error=False)
        assert c.exit_on_error is False


# =============================================================================
# Memurai._run
# =============================================================================

class TestRun:
    @patch("subprocess.run", return_value=_completed(stdout=b"PONG\n"))
    def test_run_returns_decoded_stdout(self, _mock, cli):
        out = cli._run(["PING"])
        assert out == "PONG\n"

    @patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["x"], timeout=300),
    )
    def test_run_timeout_raises_memurai_error(self, _mock, cli):
        with pytest.raises(MemuraiError, match="超时"):
            cli._run(["PING"])

    @patch("subprocess.run", side_effect=FileNotFoundError("no such file"))
    def test_run_filenotfound_raises_memurai_error(self, _mock, cli):
        with pytest.raises(MemuraiError, match="不可执行"):
            cli._run(["PING"])

    @patch("subprocess.run", return_value=_completed(stdout=b"", stderr=b"ERR", returncode=1))
    def test_run_nonzero_rc_raises(self, _mock, cli):
        with pytest.raises(MemuraiError, match="rc=1"):
            cli._run(["GET", "x"])

    @patch("subprocess.run", return_value=_completed(stdout=b"some output", returncode=1))
    def test_run_check_error_false_does_not_raise(self, _mock, cli):
        out = cli._run(["SCAN", "--pattern", "*"], check_error=False)
        assert out == "some output"

    @patch("subprocess.run", return_value=_completed(stdout=b"OK\n"))
    def test_run_with_password_db_includes_flags(self, mock_run, tmp_path):
        """命令应包含 -a <pw> 和 -n <db>。"""
        c = Memurai(
            cli_path=Path("memurai-cli.exe"),
            password="secret",
            db=3,
        )
        c._run(["PING"])
        args = mock_run.call_args.args[0]
        assert "-a" in args
        assert "secret" in args
        assert "-n" in args
        assert "3" in args

    @patch("subprocess.run", return_value=_completed(stdout=b""))
    def test_run_passes_input_bytes_when_provided(self, mock_run, cli):
        cli._run(["--pipe"], input_bytes=b"hello")
        kwargs = mock_run.call_args.kwargs
        assert kwargs["input"] == b"hello"
        # input_bytes 模式下 text=False
        assert kwargs["text"] is False


# =============================================================================
# Memurai.ping
# =============================================================================

class TestPing:
    @patch("subprocess.run", return_value=_completed(stdout=b"PONG\n"))
    def test_ping_success(self, _mock, cli):
        assert cli.ping() is True

    @patch("subprocess.run", return_value=_completed(stdout=b"ERROR\n"))
    def test_ping_failure(self, _mock, cli):
        assert cli.ping() is False


# =============================================================================
# Memurai.set
# =============================================================================

class TestSet:
    @patch("subprocess.run", return_value=_completed(stdout=b"OK\n"))
    def test_set_basic(self, _mock, cli):
        assert cli.set("k1", "v1") is True

    @patch("subprocess.run", return_value=_completed(stdout=b"OK\n"))
    def test_set_with_ex_appended(self, mock_run, cli):
        cli.set("k1", "v1", ex=60)
        args = mock_run.call_args.args[0]
        assert "EX" in args and "60" in args
        assert "PX" not in args

    @patch("subprocess.run", return_value=_completed(stdout=b"OK\n"))
    def test_set_with_px_appended(self, mock_run, cli):
        cli.set("k1", "v1", px=5000)
        args = mock_run.call_args.args[0]
        assert "PX" in args and "5000" in args
        assert "EX" not in args

    @patch("subprocess.run", return_value=_completed(stdout=b"(nil)\n"))
    def test_set_failure(self, _mock, cli):
        assert cli.set("k1", "v1") is False


# =============================================================================
# Memurai.get
# =============================================================================

class TestGet:
    @patch("subprocess.run", return_value=_completed(stdout=b"hello world\n"))
    def test_get_value(self, _mock, cli):
        assert cli.get("k1") == "hello world"

    @patch("subprocess.run", return_value=_completed(stdout=b"(nil)\n"))
    def test_get_nil(self, _mock, cli):
        assert cli.get("missing") is None


# =============================================================================
# Memurai.mset
# =============================================================================

class TestMset:
    @patch("subprocess.run", return_value=_completed(stdout=b"OK\n"))
    def test_mset_basic(self, mock_run, cli):
        assert cli.mset({"a": "1", "b": "2"}) is True
        # cmd 形如 [cli_path, -h, host, -p, port, -e, MSET, a, 1, b, 2]
        args = mock_run.call_args.args[0]
        assert "MSET" in args
        assert "a" in args and "1" in args
        assert "b" in args and "2" in args

    def test_mset_empty_returns_true_without_call(self, cli):
        """空 dict 不调子进程直接返回 True。"""
        with patch("subprocess.run") as mock_run:
            assert cli.mset({}) is True
            mock_run.assert_not_called()


# =============================================================================
# Memurai.delete / exists
# =============================================================================

class TestDeleteExists:
    @patch("subprocess.run", return_value=_completed(stdout=b"(integer) 2\n"))
    def test_delete_keys(self, _mock, cli):
        assert cli.delete("k1", "k2") == 2

    def test_delete_no_keys_returns_zero(self, cli):
        with patch("subprocess.run") as mock_run:
            assert cli.delete() == 0
            mock_run.assert_not_called()

    @patch("subprocess.run", return_value=_completed(stdout=b"(integer) 1\n"))
    def test_exists_true(self, _mock, cli):
        assert cli.exists("k1") is True

    @patch("subprocess.run", return_value=_completed(stdout=b"(integer) 0\n"))
    def test_exists_false(self, _mock, cli):
        assert cli.exists("missing") is False


# =============================================================================
# Memurai.scan
# =============================================================================

class TestScan:
    @patch("subprocess.run", return_value=_completed(stdout=b"k1\nk2\nk3\n"))
    def test_scan_returns_list(self, _mock, cli):
        assert cli.scan() == ["k1", "k2", "k3"]

    @patch("subprocess.run", return_value=_completed(stdout=b""))
    def test_scan_pattern_passed(self, mock_run, cli):
        cli.scan(pattern="audit:*", count=500)
        args = mock_run.call_args.args[0]
        assert "--pattern" in args and "audit:*" in args
        assert "--count" in args and "500" in args
        # check_error=False 时 -e 不应出现
        assert "-e" not in args

    @patch("subprocess.run", return_value=_completed(stdout=b""))
    def test_scan_empty(self, _mock, cli):
        assert cli.scan() == []


# =============================================================================
# Memurai.pipe_setex_batch（关键性能方法）
# =============================================================================

class TestPipeSetexBatch:
    def test_empty_items_returns_zero(self, cli):
        with patch("subprocess.run") as mock_run:
            assert cli.pipe_setex_batch([]) == 0
            mock_run.assert_not_called()

    @patch("subprocess.run", return_value=_completed(stdout=b"+OK\r\n+OK\r\n"))
    def test_ok_line_counting(self, _mock, cli):
        items = [("k1", 60, "v1"), ("k2", 120, "v2")]
        assert cli.pipe_setex_batch(items) == 2

    @patch(
        "subprocess.run",
        return_value=_completed(stdout=b"Last reply received from server.\nerrors: 0, replies: 3\n"),
    )
    def test_summary_line_counting(self, _mock, cli):
        """输出只有汇总没有 +OK 行时，从 'replies: N' 提取成功数。"""
        items = [("k1", 60, "v1"), ("k2", 60, "v2"), ("k3", 60, "v3")]
        assert cli.pipe_setex_batch(items) == 3

    @patch("subprocess.run", return_value=_completed(stdout=b"-ERR something failed\n"))
    def test_error_line_raises(self, _mock, cli):
        with pytest.raises(MemuraiError, match="pipe 写入失败"):
            cli.pipe_setex_batch([("k1", 60, "v1")])

    @patch("subprocess.run", return_value=_completed(stdout=b""))
    def test_sends_resp_stream_via_input_bytes(self, mock_run, cli):
        """应通过 input_bytes 传 RESP 编码字节流。"""
        cli.pipe_setex_batch([("k1", 60, "v1")])
        kwargs = mock_run.call_args.kwargs
        assert kwargs["input"] is not None
        assert isinstance(kwargs["input"], bytes)
        # RESP 流必须以 *3\r\n（SET 是 5 参数 → *5）开头
        assert kwargs["input"].startswith(b"*5\r\n")


# =============================================================================
# Memurai.mget
# =============================================================================

class TestMget:
    def test_mget_empty_keys(self, cli):
        with patch("subprocess.run") as mock_run:
            assert cli.mget([]) == []
            mock_run.assert_not_called()

    @patch("subprocess.run", return_value=_completed(stdout=b"v1\nv2\nv3\n"))
    def test_mget_basic(self, _mock, cli):
        assert cli.mget(["k1", "k2", "k3"]) == ["v1", "v2", "v3"]

    @patch("subprocess.run", return_value=_completed(stdout=b"v1\n(nil)\nv3\n"))
    def test_mget_with_nil(self, _mock, cli):
        assert cli.mget(["k1", "k2", "k3"]) == ["v1", None, "v3"]

    @patch("subprocess.run", return_value=_completed(stdout=b""))
    def test_mget_empty_output_returns_none_list(self, _mock, cli):
        assert cli.mget(["k1", "k2"]) == [None, None]


# =============================================================================
# _parse_reply
# =============================================================================

class TestParseReply:
    def test_empty(self):
        assert _parse_reply("") == ""
        assert _parse_reply("   \n  ") == ""

    def test_ok(self):
        assert _parse_reply("OK") == "OK"
        assert _parse_reply("  OK  \n") == "OK"

    def test_nil(self):
        assert _parse_reply("(nil)") is None

    def test_integer(self):
        assert _parse_reply("(integer) 42") == 42
        assert _parse_reply("(integer) 0") == 0
        assert _parse_reply("(integer) -7") == -7

    def test_integer_invalid_returns_raw(self):
        assert _parse_reply("(integer) notanum") == "(integer) notanum"

    def test_error_raises(self):
        # 注意：_parse_reply 要求 "(error) " 后跟一个空格
        with pytest.raises(MemuraiError):
            _parse_reply("(error) boom")

    def test_plain_string(self):
        assert _parse_reply("hello world") == "hello world"


# =============================================================================
# _encode_* 函数
# =============================================================================

class TestEncode:
    def test_encode_bulk_string(self):
        out = _encode_bulk_string("abc")
        assert out == b"$3\r\nabc\r\n"

    def test_encode_bulk_string_utf8(self):
        # 中文按 UTF-8 编码：3 字节字符
        out = _encode_bulk_string("中")
        assert out == b"$3\r\n\xe4\xb8\xad\r\n"

    def test_encode_array(self):
        assert _encode_array(3) == b"*3\r\n"
        assert _encode_array(0) == b"*0\r\n"

    def test_encode_command_str_args(self):
        out = _encode_command("SET", "k", "v")
        assert out == b"*3\r\n$3\r\nSET\r\n$1\r\nk\r\n$1\r\nv\r\n"

    def test_encode_command_int_args(self):
        out = _encode_command("EXPIRE", "k", 60)
        # int 60 应转为字符串 "60"
        assert b"$2\r\n60\r\n" in out

    def test_encode_command_bytes_arg(self):
        out = _encode_command("SET", b"rawbytes")
        assert b"$8\r\nrawbytes\r\n" in out


# =============================================================================
# 一些次要方法（顺带覆盖）
# =============================================================================

class TestMisc:
    @patch("subprocess.run", return_value=_completed(stdout=b"OK\n"))
    def test_setex(self, _mock, cli):
        assert cli.setex("k1", 100, "v1") is True

    @patch("subprocess.run", return_value=_completed(stdout=b"(integer) 1\n"))
    def test_expire_true(self, _mock, cli):
        assert cli.expire("k1", 60) is True

    @patch("subprocess.run", return_value=_completed(stdout=b"(integer) 0\n"))
    def test_expire_false(self, _mock, cli):
        assert cli.expire("missing", 60) is False

    @patch("subprocess.run", return_value=_completed(stdout=b"(integer) 5\n"))
    def test_dbsize(self, _mock, cli):
        assert cli.dbsize() == 5

    @patch("subprocess.run", return_value=_completed(stdout=b"# Server\r\nredis_version:7.0\r\n"))
    def test_info(self, _mock, cli):
        assert "redis_version" in cli.info()

    @patch("subprocess.run", return_value=_completed(stdout=b"OK\n"))
    def test_flushdb(self, _mock, cli):
        assert cli.flushdb() is True

    @patch("subprocess.run", return_value=_completed(stdout=b"OK\n"))
    def test_set_json(self, mock_run, cli):
        assert cli.set_json("k", {"a": 1}) is True
        args = mock_run.call_args.args[0]
        # 第 3 个参数应是 JSON 字符串
        assert '{"a": 1}' in args

    @patch("subprocess.run", return_value=_completed(stdout=b'{"a": 1}\n'))
    def test_get_json(self, _mock, cli):
        assert cli.get_json("k") == {"a": 1}

    @patch("subprocess.run", return_value=_completed(stdout=b"not-json\n"))
    def test_get_json_invalid_returns_none(self, _mock, cli):
        assert cli.get_json("k") is None

    @patch("subprocess.run", return_value=_completed(stdout=b""))
    def test_get_json_missing_returns_none(self, _mock, cli):
        assert cli.get_json("missing") is None

    @patch("subprocess.run", return_value=_completed(stdout=b"(integer) 1\n"))
    def test_expire_many(self, _mock, cli):
        assert cli.expire_many([("k1", 60), ("k2", 30)]) == 2

    @patch("subprocess.run", return_value=_completed(stdout=b"k1\nk2\n"))
    def test_scan_iter_yields(self, _mock, cli):
        assert list(cli.scan_iter(match="*")) == ["k1", "k2"]

    @patch("subprocess.run", return_value=_completed(stdout=b"k1\nk2\nk3\n"))
    def test_count(self, _mock, cli):
        assert cli.count("audit:*") == 3


# =============================================================================
# Memurai.pipe_exec / pipe_set_many（--pipe 通用批量）
# =============================================================================

class TestPipeExec:
    def test_empty_commands_returns_zero(self, cli):
        with patch("subprocess.run") as mock_run:
            assert cli.pipe_exec([]) == 0
            mock_run.assert_not_called()

    @patch("subprocess.run", return_value=_completed(stdout=b"+OK\r\n+OK\r\n"))
    def test_pipe_exec_counts_ok_lines(self, _mock, cli):
        cmds = [["SET", "k1", "v1"], ["SET", "k2", "v2"]]
        assert cli.pipe_exec(cmds) == 2

    @patch("subprocess.run", return_value=_completed(stdout=b":1\r\n:(integer) 1\r\n"))
    def test_pipe_exec_counts_integer_one_lines(self, _mock, cli):
        """整型回复（EXPIRE 等）的两种格式都应计数。"""
        cmds = [["EXPIRE", "k1", "60"], ["EXPIRE", "k2", "30"]]
        assert cli.pipe_exec(cmds) == 2

    @patch("subprocess.run", return_value=_completed(stdout=b"Last reply...\nerrors: 0, replies: 4\n"))
    def test_pipe_exec_summary_fallback(self, _mock, cli):
        """无 +OK 行时从 'replies: N' 提取数量。"""
        cmds = [["SET", "k1", "v1"]]
        assert cli.pipe_exec(cmds) == 4

    @patch("subprocess.run", return_value=_completed(stdout=b"-ERR boom\n"))
    def test_pipe_exec_error_raises(self, _mock, cli):
        with pytest.raises(MemuraiError, match="pipe 执行失败"):
            cli.pipe_exec([["SET", "k1", "v1"]])


class TestPipeSetMany:
    def test_empty_dict_returns_true(self, cli):
        with patch("subprocess.run") as mock_run:
            assert cli.pipe_set_many({}) is True
            mock_run.assert_not_called()

    @patch("subprocess.run", return_value=_completed(stdout=b"+OK\r\n"))
    def test_pipe_set_many_has_len_of_int_bug(self, _mock, cli):
        """**已知缺陷（memurai_client.py:374，非本测试引入）**：

        `pipe_set_many` 实现为 `len(self.pipe_exec(commands)) > 0`，
        但 `pipe_exec` 返回 `int` 而非 `list`，`len(int)` 抛 TypeError。

        本测试**断言**该缺陷行为以锁定现状；修复源码后此测试需同步翻转。
        """
        with pytest.raises(TypeError, match="has no len\\(\\)"):
            cli.pipe_set_many({"a": "1", "b": "2"})

