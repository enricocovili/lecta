#!/usr/bin/env python3
"""Lecta LaTeX compile service.

Runs in a container with no network, as an unprivileged user, and serves a tiny
JSON-over-HTTP/1.1 API on a unix socket in a shared volume. Standard library only.

Endpoints (POST, JSON body):
  /compile   build a project work dir (figures first, then latexmk)
  /figure    compile one standalone figure (diagram self-correction loop)
  /synctex   map a PDF position to a source file/line (best effort)
  GET /health

Queue: per project key at most one compile runs, plus at most one pending; a
newer request replaces the pending one (the replaced caller gets "superseded").
A global limit of CONCURRENCY compiles applies; interactive requests are
started before background ones, and background ones run with a higher nice.

Sandbox (per run): shell escape off, openin_any=p / openout_any=p (no absolute
paths, no "..", no dot files), latexmk -norc with a fixed rc file (a project
can't inject a latexmkrc), fresh HOME/TMPDIR/TEXMFVAR under /tmp, rlimits on
CPU time, address space, file size and processes, wall-clock timeout that kills
the whole process group.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import resource
import shutil
import signal
import tempfile
import time
from pathlib import Path

SOCKET = os.environ.get("LECTA_LATEX_SOCKET", "/run/lecta/latex.sock")
WORK_ROOT = Path(os.environ.get("LECTA_LATEX_WORK", "/latex-work")).resolve()
CONCURRENCY = int(os.environ.get("LECTA_LATEX_CONCURRENCY", "2"))
MEM_LIMIT_MB = int(os.environ.get("LECTA_LATEX_MEM_MB", "2048"))
MAX_LOG = 2 * 1024 * 1024
RC_FILE = Path(__file__).with_name("latexmkrc")

ENGINES = {"pdflatex": "-pdf", "xelatex": "-pdfxe", "lualatex": "-pdflua"}
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,99}$")
_REL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+/-]{0,250}$")


class BadRequest(Exception):
    pass


def resolve_workdir(rel: str) -> Path:
    if not isinstance(rel, str) or not _REL_RE.match(rel) or ".." in rel.split("/"):
        raise BadRequest("bad workdir")
    p = (WORK_ROOT / rel).resolve()
    if not str(p).startswith(str(WORK_ROOT) + os.sep):
        raise BadRequest("workdir outside root")
    p.mkdir(parents=True, exist_ok=True)
    return p


# --------------------------------------------------------------------------- process sandbox


def _limits(cpu_s: int, nice: int):
    def apply() -> None:
        os.setsid()
        with contextlib.suppress(OSError):
            os.nice(nice)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 5))
        mem = MEM_LIMIT_MB * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        fsize = 512 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    return apply


def _env(tmp: Path) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp / "home"),
        "TMPDIR": str(tmp / "tmp"),
        "TEXMFVAR": str(tmp / "texmf-var"),
        "TEXMFCONFIG": str(tmp / "texmf-config"),
        "TEXMFHOME": str(tmp / "texmf-home"),
        # kpathsea reads these from the environment, overriding texmf.cnf.
        "openin_any": "p",
        "openout_any": "p",
        "shell_escape": "f",
        # One log line per message (the diagnostics parser relies on it).
        "max_print_line": "10000",
        "error_line": "254",
        "half_error_line": "238",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "SOURCE_DATE_EPOCH": "0",
        "FORCE_SOURCE_DATE": "1",
    }
    for d in ("home", "tmp", "texmf-var", "texmf-config", "texmf-home"):
        (tmp / d).mkdir(parents=True, exist_ok=True)
    return env


async def run_sandboxed(argv: list[str], cwd: Path, timeout: int, background: bool) -> tuple[int, str, bool]:
    """Run argv in cwd; returns (returncode, combined output, timed_out)."""
    tmp = Path(tempfile.mkdtemp(prefix="job-", dir="/tmp"))
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=_env(tmp),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            preexec_fn=_limits(timeout + 10, 15 if background else 5),
        )
        timed_out = False
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            timed_out = True
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            out, _ = await proc.communicate()
        except asyncio.CancelledError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            raise
        return proc.returncode if proc.returncode is not None else -1, out.decode("utf-8", "replace")[-MAX_LOG:], timed_out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def latexmk_argv(engine: str, root: str, jobname: str, outdir: str | None = None) -> list[str]:
    argv = [
        "latexmk",
        "-norc",
        "-r",
        str(RC_FILE),
        ENGINES.get(engine, "-pdf"),
        "-interaction=nonstopmode",
        "-file-line-error",
        "-synctex=1",
        "-no-shell-escape",
        f"-jobname={jobname}",
    ]
    if outdir:
        argv.append(f"-outdir={outdir}")
    argv.append(root)
    return argv


def read_log(path: Path) -> str:
    try:
        if path.is_symlink():
            return ""
        data = path.read_bytes()[-MAX_LOG:]
        return data.decode("utf-8", "replace")
    except OSError:
        return ""


# --------------------------------------------------------------------------- figures


def _sha(*parts: bytes) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(hashlib.sha256(p).digest())
    return h.hexdigest()


_IMG_REF_RE = re.compile(r"images/[A-Za-z0-9._+/-]+")


def figure_hash(work: Path, name: str, engine: str) -> str:
    src = (work / "figures" / f"{name}.tex").read_bytes()
    preamble = (work / "preamble.tex").read_bytes() if (work / "preamble.tex").exists() else b""
    parts = [src, preamble, engine.encode()]
    for ref in sorted(set(_IMG_REF_RE.findall(src.decode("utf-8", "replace")))):
        p = work / ref
        if p.is_file() and not p.is_symlink():
            parts.append(ref.encode() + p.read_bytes())
    return _sha(*parts)


FIG_WRAPPER = (
    "\\documentclass[border=3pt,varwidth]{standalone}\n"
    "\\def\\lectastandalone{1}\n"
    "\\input{preamble}\n"
    "\\begin{document}\n"
    "\\input{figures/%s}\n"
    "\\end{document}\n"
)


async def compile_figure(work: Path, cache: Path, name: str, engine: str, timeout: int, background: bool) -> dict:
    if not _NAME_RE.match(name):
        return {"name": name, "status": "error", "log": "bad figure name"}
    h = figure_hash(work, name, engine)
    cached = cache / f"{h}.pdf"
    target_dir = work / "figures-cache"
    target_dir.mkdir(exist_ok=True)
    target = target_dir / f"{name}.pdf"
    if cached.is_file():
        _place(cached, target)
        return {"name": name, "status": "cached", "hash": h}
    build = work / "_figbuild"
    build.mkdir(exist_ok=True)
    wrapper = f"_fig-{name}.tex"
    (work / wrapper).write_text(FIG_WRAPPER % name)
    argv = latexmk_argv(engine, wrapper, f"fig-{name}", outdir="_figbuild")
    t0 = time.monotonic()
    code, out, timed_out = await run_sandboxed(argv, work, timeout, background)
    log = read_log(build / f"fig-{name}.log") or out
    pdf = build / f"fig-{name}.pdf"
    (work / wrapper).unlink(missing_ok=True)
    if code == 0 and pdf.is_file() and not pdf.is_symlink():
        cache.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pdf, cached)
        _place(cached, target)
        return {"name": name, "status": "ok", "hash": h, "seconds": round(time.monotonic() - t0, 2), "log": log[-20000:]}
    target.unlink(missing_ok=True)
    return {
        "name": name,
        "status": "timeout" if timed_out else "error",
        "hash": h,
        "log": log[-60000:],
        "seconds": round(time.monotonic() - t0, 2),
    }


def _place(src: Path, dst: Path) -> None:
    if dst.is_symlink():
        dst.unlink()
    tmp = dst.with_name(dst.name + ".tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


# --------------------------------------------------------------------------- compile


async def do_compile(req: dict) -> dict:
    work = resolve_workdir(req["workdir"])
    cache = resolve_workdir(req.get("figure_cache") or f"{req['workdir']}-figcache")
    engine = req.get("engine", "pdflatex")
    if engine not in ENGINES:
        raise BadRequest("bad engine")
    timeout = int(req.get("timeout", 120))
    background = req.get("priority") == "background"
    mode = req.get("mode", "draft")
    t0 = time.monotonic()

    figures = []
    fig_dir = work / "figures"
    if req.get("figures", True) and fig_dir.is_dir():
        for f in sorted(fig_dir.glob("*.tex")):
            if f.is_symlink():
                continue
            figures.append(await compile_figure(work, cache, f.stem, engine, max(30, timeout // 2), background))

    main = req.get("main", "main.tex")
    if not _NAME_RE.match(main) or not (work / main).is_file():
        return {"status": "error", "log": f"{main} not found", "figures": figures}
    pre = []
    if mode == "publish":
        pre.append("\\def\\lectapublish{1}")
    only = req.get("includeonly") or []
    if only:
        for p in only:
            if not _REL_RE.match(p) or ".." in p:
                raise BadRequest("bad includeonly entry")
        pre.append("\\def\\lectaincludeonly{" + ",".join(p.removesuffix(".tex") for p in only) + "}")
    root = "_root.tex"
    (work / root).write_text("".join(pre) + "\\input{" + main.removesuffix(".tex") + "}\n")
    jobname = main.removesuffix(".tex")
    code, out, timed_out = await run_sandboxed(latexmk_argv(engine, root, jobname), work, timeout, background)
    log = read_log(work / f"{jobname}.log") or out
    pdf = work / f"{jobname}.pdf"
    ok = code == 0 and pdf.is_file() and not pdf.is_symlink()
    status = "ok" if ok else ("timeout" if timed_out else "error")
    return {
        "status": status,
        "pdf": str(pdf.relative_to(WORK_ROOT)) if pdf.is_file() else None,
        "pdf_mtime": pdf.stat().st_mtime if pdf.is_file() else None,
        "log": log,
        "latexmk_output": out[-20000:],
        "figures": [{k: v for k, v in f.items() if k != "log" or f["status"] in ("error", "timeout")} for f in figures],
        "seconds": round(time.monotonic() - t0, 2),
        "returncode": code,
    }


async def do_figure(req: dict) -> dict:
    work = resolve_workdir(req["workdir"])
    cache = resolve_workdir(req.get("figure_cache") or f"{req['workdir']}-figcache")
    engine = req.get("engine", "pdflatex")
    if engine not in ENGINES:
        raise BadRequest("bad engine")
    res = await compile_figure(
        work, cache, req["name"], engine, int(req.get("timeout", 60)), req.get("priority") == "background"
    )
    target = work / "figures-cache" / f"{req['name']}.pdf"
    if res["status"] in ("ok", "cached"):
        res["pdf"] = str(target.relative_to(WORK_ROOT))
    return res


async def do_synctex(req: dict) -> dict:
    work = resolve_workdir(req["workdir"])
    page = int(req["page"])
    x, y = float(req["x"]), float(req["y"])
    pdf = req.get("pdf", "main.pdf")
    if not _NAME_RE.match(pdf):
        raise BadRequest("bad pdf")
    code, out, _ = await run_sandboxed(
        ["synctex", "edit", "-o", f"{page}:{x}:{y}:{pdf}"], work, 15, False
    )
    res: dict = {"status": "ok" if code == 0 else "error"}
    for line in out.splitlines():
        if line.startswith("Input:"):
            res["file"] = line[6:].strip()
        elif line.startswith("Line:"):
            res["line"] = int(line[5:].strip() or 0)
    return res


# --------------------------------------------------------------------------- queue


class Slot:
    def __init__(self) -> None:
        self.running = False
        self.pending: tuple[dict, asyncio.Future] | None = None


class Scheduler:
    def __init__(self) -> None:
        self.slots: dict[str, Slot] = {}
        self.active = 0
        self.waiters: list[tuple[int, float, asyncio.Future]] = []

    async def _acquire(self, priority: int) -> None:
        if self.active < CONCURRENCY and not self.waiters:
            self.active += 1
            return
        fut = asyncio.get_running_loop().create_future()
        self.waiters.append((priority, time.monotonic(), fut))
        self.waiters.sort(key=lambda w: (w[0], w[1]))
        try:
            await fut
        except asyncio.CancelledError:
            self.waiters = [w for w in self.waiters if w[2] is not fut]
            if fut.done() and not fut.cancelled():
                self._release()
            raise

    def _release(self) -> None:
        while self.waiters:
            _, _, fut = self.waiters.pop(0)
            if not fut.done():
                fut.set_result(None)
                return
        self.active -= 1

    async def submit(self, key: str, req: dict, fn) -> dict:
        slot = self.slots.setdefault(key, Slot())
        if slot.running:
            if slot.pending is not None:
                old_req, old_fut = slot.pending
                if not old_fut.done():
                    old_fut.set_result({"status": "superseded"})
            fut = asyncio.get_running_loop().create_future()
            slot.pending = (req, fut)
            return await fut
        return await self._run(key, slot, req, fn)

    async def _run(self, key: str, slot: Slot, req: dict, fn) -> dict:
        slot.running = True
        try:
            await self._acquire(0 if req.get("priority") != "background" else 1)
            try:
                result = await fn(req)
            finally:
                self._release()
        finally:
            if slot.pending is not None:
                # Hand the slot straight to the newest pending request (stays "running").
                nreq, nfut = slot.pending
                slot.pending = None
                asyncio.get_running_loop().create_task(self._chain(key, slot, nreq, nfut, fn))
            else:
                slot.running = False
                self.slots.pop(key, None)
        return result

    async def _chain(self, key, slot, req, fut, fn) -> None:
        try:
            res = await self._run(key, slot, req, fn)
        except Exception as e:  # noqa: BLE001
            res = {"status": "error", "log": f"internal error: {e}"}
        if not fut.done():
            fut.set_result(res)


SCHED = Scheduler()


# --------------------------------------------------------------------------- HTTP over unix socket


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    status, body = 200, {}
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=30)
        lines = head.decode("latin-1").split("\r\n")
        method, path, _ = lines[0].split(" ", 2)
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        length = int(headers.get("content-length", "0") or 0)
        if length > 1024 * 1024:
            raise BadRequest("body too large")
        raw = await reader.readexactly(length) if length else b""
        req = json.loads(raw or b"{}")
        if method == "GET" and path == "/health":
            body = {"ok": True, "active": SCHED.active, "keys": len(SCHED.slots)}
        elif method == "POST" and path == "/compile":
            key = str(req.get("key") or req["workdir"])
            body = await SCHED.submit(key, req, do_compile)
        elif method == "POST" and path == "/figure":
            key = "fig:" + str(req.get("key") or req["workdir"]) + ":" + str(req.get("name"))
            body = await SCHED.submit(key, req, do_figure)
        elif method == "POST" and path == "/synctex":
            body = await do_synctex(req)
        else:
            status, body = 404, {"error": "not found"}
    except (BadRequest, KeyError, ValueError) as e:
        status, body = 400, {"error": str(e)}
    except Exception as e:  # noqa: BLE001
        status, body = 500, {"error": f"{type(e).__name__}: {e}"}
    data = json.dumps(body).encode()
    reason = {200: "OK", 400: "Bad Request", 404: "Not Found", 500: "Internal Server Error"}.get(status, "OK")
    writer.write(
        f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\n"
        "Connection: close\r\n\r\n".encode()
        + data
    )
    with contextlib.suppress(Exception):
        await writer.drain()
        writer.close()


async def main() -> None:
    sock = Path(SOCKET)
    sock.parent.mkdir(parents=True, exist_ok=True)
    sock.unlink(missing_ok=True)
    server = await asyncio.start_unix_server(handle, path=str(sock), limit=2**20)
    os.chmod(sock, 0o660)
    print(f"compile service listening on {sock} (concurrency {CONCURRENCY})", flush=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    async with server:
        await stop.wait()
    sock.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
