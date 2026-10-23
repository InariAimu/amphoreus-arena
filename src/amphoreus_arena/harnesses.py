import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .process import spawn, stop
from .telemetry import codex_usage, dsh_usage


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def image_inputs(directory: Path, ply: int, images: list[Path] | None) -> list[dict]:
    """Record exact image bytes separately from native transport logs."""
    entries = []
    for path in images or []:
        path = path.resolve()
        data = path.read_bytes()
        entries.append({"path": str(path), "mimeType": mimetypes.guess_type(path.name)[0],
                        "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    if entries:
        write_json(directory / f"{ply:04d}.images.json", entries)
    return entries


def log_frame(value):
    if isinstance(value, dict):
        if value.get("name") == "ARENA_GO_TOKEN":
            return {**value, "value": "[redacted]"}
        if value.get("type") == "image" and isinstance(value.get("data"), str):
            data = base64.b64decode(value["data"])
            return {**value, "data": {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}}
        return {key: log_frame(item) for key, item in value.items()}
    if isinstance(value, list):
        return [log_frame(item) for item in value]
    return value


def dsh_command(config: dict) -> tuple[list[str], dict[str, str]]:
    env = os.environ.copy()
    if config.get("home"):
        env["DSH_HOME"] = str(Path(config["home"]).resolve())
    if config.get("command"):
        command = list(config["command"])
    else:
        desktop = Path(config.get("desktop_exe", "D:/Software/DeepSeekHarness/DeepSeek Harness.exe"))
        if not desktop.is_file():
            raise FileNotFoundError("Set dsh.desktop_exe or dsh.command; desktop executable not found")
        cli = desktop.parent / "resources/app.asar/dsh/node_modules/@deepseek-ai/dsh-desktop-host/lib/cli.js"
        env["ELECTRON_RUN_AS_NODE"] = "1"
        command = [str(desktop), "--expose-internals", str(cli), "--profile", "acp"]
    for patch in config.get("patches", []):
        path = Path(patch).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        command += ["--patch", str(path)]
    return command, env


class DshHarness:
    def __init__(self, config: dict, directory: Path):
        self.config, self.directory = config, directory
        self.workspace = directory / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.metadata = {"kind": "dsh", "transport": "acp", "session_id": None}
        self.process = None
        self.reader_task = None
        self.pending: dict[int, asyncio.Future] = {}
        self.counter = 0
        self.messages: list[str] = []
        self.all_messages: list[str] = []
        self.fatal: Exception | None = None
        self.stderr_file = None
        self.game_tools = None

    def configure_game_tools(self, connection):
        self.game_tools = connection

    def log(self, direction: str, frame: dict):
        with (self.directory / "protocol.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(),
                                "direction": direction, "frame": log_frame(frame)}, ensure_ascii=False) + "\n")

    async def send(self, frame: dict):
        self.log("out", frame)
        self.process.stdin.write((json.dumps(frame, ensure_ascii=False) + "\n").encode())
        await self.process.stdin.drain()

    async def rpc(self, method: str, params: dict):
        if self.fatal:
            raise self.fatal
        self.counter += 1
        request_id = self.counter
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            return await future
        finally:
            self.pending.pop(request_id, None)

    async def read_loop(self):
        try:
            while line := await self.process.stdout.readline():
                # Desktop model-provider diagnostics can reach stdout alongside ACP messages.
                # Retain those bytes separately; they are not model output or protocol messages.
                try:
                    frame = json.loads(line)
                except json.JSONDecodeError:
                    with (self.directory / "launcher.stdout.log").open("ab") as log:
                        log.write(line)
                    continue
                if not isinstance(frame, dict) or frame.get("jsonrpc") != "2.0":
                    raise RuntimeError("Unexpected JSON on DSH ACP stdout")
                self.log("in", frame)
                if "method" in frame:
                    await self.on_request(frame)
                elif frame.get("id") in self.pending:
                    future = self.pending[frame["id"]]
                    if not future.done():
                        if "error" in frame:
                            future.set_exception(RuntimeError(f"DSH ACP: {frame['error']}"))
                        else:
                            future.set_result(frame.get("result", {}))
            raise RuntimeError("DSH ACP exited or closed stdout; see dsh/stderr.log")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.fatal = exc
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(exc)

    async def on_request(self, frame: dict):
        if frame["method"] == "session/update":
            update = frame.get("params", {}).get("update", {})
            if update.get("sessionUpdate") == "config_option_update":
                self.update_config_options(update.get("configOptions", []))
            if update.get("sessionUpdate") == "usage_update":
                self.metadata["usage"] = dsh_usage(update)
            if self.game_tools and update.get("sessionUpdate") == "tool_call":
                # ACP streams commentary before tool calls in the same prompt.
                # The final answer is the text following the last tool call;
                # retain the complete transcript separately, without JSON scraping.
                self.messages = []
            if update.get("sessionUpdate") == "agent_message_chunk":
                content = update.get("content", {})
                if content.get("type") == "text":
                    self.messages.append(content["text"])
                    self.all_messages.append(content["text"])
        elif "id" in frame:
            if frame["method"] == "session/request_permission":
                # The smoke game needs no external tools; never hang on a permission UI.
                result = {"outcome": {"outcome": "cancelled"}}
                await self.send({"jsonrpc": "2.0", "id": frame["id"], "result": result})
            else:
                await self.send({"jsonrpc": "2.0", "id": frame["id"],
                                 "error": {"code": -32601, "message": "Unsupported client method"}})

    def update_config_options(self, options: list):
        self.metadata["config_options"] = options
        for option in options:
            value = option.get("currentValue")
            if option["id"] == "model" and value:
                try:
                    provider, model = json.loads(value)
                except (ValueError, TypeError):
                    provider, model = None, value
                self.metadata.update(provider=provider, model=model, model_source="acp_config")
            elif option["id"] == "reasoning_effort":
                self.metadata["reasoning_effort"] = value

    async def start(self):
        command, env = dsh_command(self.config)
        self.metadata["command"] = command
        # Desktop's launcher's version is authoritative; ACP agentInfo is a protocol package version.
        version_command = (list(self.config["command"]) if self.config.get("command") else command[:3])
        version = await spawn(version_command + ["--version"], env=env,
                              stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, _ = await asyncio.wait_for(version.communicate(), 15)
            self.metadata["version"] = out.decode(errors="replace").strip()
        finally:
            await stop(version)
        self.stderr_file = (self.directory / "stderr.log").open("wb")
        self.process = await spawn(command, env=env, cwd=str(self.workspace),
                                   stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                   stderr=self.stderr_file, limit=4 * 1024 * 1024)
        self.reader_task = asyncio.create_task(self.read_loop())
        init = await self.rpc("initialize", {"protocolVersion": 1, "clientCapabilities": {},
                                            "clientInfo": {"name": "amphoreus-arena", "version": "0.1.0"}})
        self.metadata["agent_info"] = init.get("agentInfo")
        self.metadata["agent_capabilities"] = init.get("agentCapabilities", {})
        servers = []
        if self.game_tools:
            servers = [{**self.game_tools, "env": [{"name": key, "value": value}
                                                   for key, value in self.game_tools["env"].items()]}]
        session = await self.rpc("session/new", {"cwd": str(self.workspace.resolve()), "mcpServers": servers})
        self.metadata["session_id"] = session["sessionId"]
        self.update_config_options(session.get("configOptions", []))
        for option_id, value in (("model", self.config.get("model")),
                                 ("reasoning_effort", self.config.get("reasoning_effort"))):
            if value:
                result = await self.rpc("session/set_config_option", {
                    "sessionId": session["sessionId"], "configId": option_id, "value": value})
                self.update_config_options(result.get("configOptions", []))

    async def act(self, prompt: str, schema: dict, ply: int, images: list[Path] | None = None) -> str:
        self.messages = []
        self.all_messages = []
        self.metadata.pop("usage", None)
        (self.directory / f"{ply:04d}.prompt.txt").write_text(prompt, encoding="utf-8")
        content = [{"type": "text", "text": prompt}]
        if images and not self.metadata.get("agent_capabilities", {}).get("promptCapabilities", {}).get("image"):
            raise RuntimeError("DSH ACP did not advertise image input; configure a vision route before initialize")
        for entry in image_inputs(self.directory, ply, images):
            content.append({"type": "image", "mimeType": entry["mimeType"],
                            "data": base64.b64encode(Path(entry["path"]).read_bytes()).decode("ascii")})
        result = await self.rpc("session/prompt", {"sessionId": self.metadata["session_id"],
                                                   "prompt": content})
        raw = "".join(self.messages)
        if self.game_tools:
            (self.directory / f"{ply:04d}.transcript.txt").write_text("".join(self.all_messages), encoding="utf-8")
        (self.directory / f"{ply:04d}.response.txt").write_text(raw, encoding="utf-8")
        if result.get("stopReason") != "end_turn":
            raise RuntimeError(f"DSH prompt did not complete: {result}")
        return raw

    async def close(self):
        if self.process and self.process.returncode is None and not self.fatal:
            try:
                sid = self.metadata.get("session_id")
                if sid:
                    await self.send({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": sid}})
                    await asyncio.wait_for(self.rpc("session/close", {"sessionId": sid}), 10)
            except Exception:
                pass
        await stop(self.process)
        if self.reader_task:
            self.reader_task.cancel()
            await asyncio.gather(self.reader_task, return_exceptions=True)
        if self.stderr_file:
            self.stderr_file.close()


class CodexHarness:
    def __init__(self, config: dict, directory: Path):
        self.config, self.directory = config, directory
        self.workspace = directory / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.executable = config.get("executable") or shutil.which("codex")
        if not self.executable:
            raise FileNotFoundError("codex executable not found; set black.executable")
        self.process = None
        self.session_log = None
        self.session_log_offset = 0
        self.game_tools = None
        self.metadata = {"kind": "codex", "transport": "exec/resume", "session_id": None,
                         "requested_model": config.get("model", "inherited"),
                         "model": config.get("model"), "model_source": "configured" if config.get("model") else "pending",
                         "reasoning_effort": config.get("reasoning_effort", "low")}

    def configure_game_tools(self, connection):
        self.game_tools = connection

    def refresh_session_metadata(self):
        # exec's public JSONL lacks model info. Best-effort read of this exact local session
        # records the effective model without opening credentials or unrelated sessions.
        if not self.metadata.get("session_id"):
            return
        root = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions"
        try:
            if self.session_log is None:
                self.session_log = next(root.rglob(f"*{self.metadata['session_id']}.jsonl"), None)
            if self.session_log:
                with self.session_log.open("r", encoding="utf-8") as log:
                    log.seek(self.session_log_offset)
                    for line in log:
                        event = json.loads(line)
                        if event.get("type") == "turn_context":
                            context = event["payload"]
                            if context.get("model"):
                                self.metadata.update(model=context["model"], model_source="session_turn_context")
                            if context.get("effort"):
                                self.metadata["reasoning_effort"] = context["effort"]
                    self.session_log_offset = log.tell()
        except (OSError, ValueError):
            pass  # Metadata enrichment must not break a valid move on other CLI versions.

    async def start(self):
        process = await spawn([self.executable, "--version"], stdout=asyncio.subprocess.PIPE,
                              stderr=asyncio.subprocess.PIPE)
        try:
            out, _ = await asyncio.wait_for(process.communicate(), 15)
            self.metadata["version"] = out.decode(errors="replace").strip()
            self.metadata["executable"] = self.executable
        finally:
            await stop(process)

    def command(self, schema_file: Path, answer_file: Path, images: list[Path] | None = None) -> list[str]:
        command = [self.executable, "-c", 'approval_policy="never"',
                   "-c", 'sandbox_mode="read-only"']
        if self.game_tools:
            connection = self.game_tools
            values = {"command": connection["command"], "args": connection["args"],
                      "env_vars": list(connection["env"]), "required": True,
                      "default_tools_approval_mode": "approve", "startup_timeout_sec": 30}
            for key, value in values.items():
                command += ["-c", f"mcp_servers.arena_go.{key}={json.dumps(value)}"]
        # This CLI replaces (rather than appends) repeated -c lists across
        # subcommand levels. Keep all overrides together before exec/resume.
        effort = self.config.get("reasoning_effort", "low")
        command += ["-c", f'model_reasoning_effort="{effort}"', "exec"]
        if self.metadata["session_id"]:
            command += ["resume", self.metadata["session_id"]]
        command += ["--skip-git-repo-check", "--json", "--output-schema", str(schema_file),
                    "--output-last-message", str(answer_file)]
        if self.config.get("model"):
            command += ["--model", self.config["model"]]
        for path in images or []:
            command += ["--image", str(path.resolve())]
        command += ["-"]
        return command

    async def act(self, prompt: str, schema: dict, ply: int, images: list[Path] | None = None) -> str:
        schema_file = self.directory / f"{ply:04d}.schema.json"
        self.metadata.pop("usage", None)
        answer_file = self.directory / f"{ply:04d}.response.txt"
        write_json(schema_file, schema)
        (self.directory / f"{ply:04d}.prompt.txt").write_text(prompt, encoding="utf-8")
        image_inputs(self.directory, ply, images)
        command = self.command(schema_file, answer_file, images)
        write_json(self.directory / f"{ply:04d}.command.json", command)
        with (self.directory / f"{ply:04d}.events.jsonl").open("wb") as stdout, \
                (self.directory / f"{ply:04d}.stderr.log").open("wb") as stderr:
            env = os.environ.copy()
            if self.game_tools:
                env.update(self.game_tools["env"])
            self.process = await spawn(command, cwd=str(self.workspace), stdin=asyncio.subprocess.PIPE,
                                       stdout=stdout, stderr=stderr, env=env)
            try:
                await self.process.communicate(prompt.encode("utf-8"))
            finally:
                await stop(self.process)
        events = (self.directory / f"{ply:04d}.events.jsonl").read_text(encoding="utf-8")
        for line in events.splitlines():
            event = json.loads(line)
            if event.get("type") == "thread.started":
                self.metadata["session_id"] = event["thread_id"]
            if event.get("type") == "turn.completed":
                self.metadata["last_usage"] = event.get("usage")
                if event.get("usage"):
                    self.metadata["usage"] = codex_usage(event["usage"])
        if self.process.returncode != 0:
            raise RuntimeError(f"Codex exited {self.process.returncode}; see {ply:04d}.stderr.log / events.jsonl")
        if not answer_file.exists():
            raise RuntimeError("Codex did not write a final answer")
        if not self.metadata["session_id"]:
            raise RuntimeError("Codex did not report its session id")
        self.refresh_session_metadata()
        return answer_file.read_text(encoding="utf-8")

    async def close(self):
        await stop(self.process)


class RandomHarness:
    """Deterministic local plumbing test; explicitly labelled, never an AI fallback."""

    def __init__(self, config: dict, directory: Path):
        import random
        self.random = random.Random(config.get("seed", 42))
        self.metadata = {"kind": "random", "seed": config.get("seed", 42)}

    async def start(self):
        pass

    async def act(self, prompt: str, schema: dict, ply: int, images: list[Path] | None = None) -> str:
        observation = json.loads(prompt.split("\n", 1)[1])
        return json.dumps({"move": self.random.choice(observation["legal_moves"]),
                           "explanation": "随机合法走子，用于验证程序管线。"})

    async def close(self):
        pass


def make_harness(config: dict, directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    factories = {"dsh": DshHarness, "codex": CodexHarness, "random": RandomHarness}
    return factories[config["kind"]](config, directory)
