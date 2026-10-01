#!/usr/bin/env python3
"""Run the existing calculation APIs in disposable containers. Python 3.9+."""
import argparse
import contextlib
import csv
import datetime as dt
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from probe import save
from report import comparison_report, run_report, compare_records

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmark"
TOOL = Path(__file__).resolve().parent
VERSION = "1.1-external-unmodified"
ENV_KEYS = (
    "QT_QPA_PLATFORM",
    "QGIS_PREFIX_PATH",
    "PYTHONPATH",
    "LD_LIBRARY_PATH",
    "DATA_PATH",
    "GDAL_NUM_THREADS",
    "GDAL_CACHEMAX",
    "CPL_NUM_CPUS",
    "GDAL_DISABLE_READDIR_ON_OPEN",
)


def command(args, timeout=60, check=True):
    r = subprocess.run(
        [str(x) for x in args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )
    if check and r.returncode:
        raise RuntimeError(f"{args[0]} {args[1]} failed: {r.stderr.strip()[:1500]}")
    return r


def docker(*args, **kwargs):
    return command(["docker", *args], **kwargs)


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def machine_id():
    # Hash the OS machine identifier; never save the raw hardware UUID.
    try:
        if sys.platform == "darwin":
            raw = command(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"]).stdout
            token = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', raw).group(1)
        elif sys.platform == "win32":
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography"
            ) as key:
                token = winreg.QueryValueEx(key, "MachineGuid")[0]
        else:
            token = Path("/etc/machine-id").read_text().strip()
        return hashlib.sha256(token.encode()).hexdigest()
    except Exception:
        # Deterministic within this checkout; unavailable identity must be explicit.
        return fingerprint(
            {
                "host": platform.node(),
                "platform": platform.platform(),
                "node": uuid.getnode(),
            }
        )


def code_record():
    files = {}
    for folder in ("backend", "qgis"):
        for p in sorted((ROOT / folder).rglob("*")):
            if (
                p.is_file()
                and p.suffix in (".py", ".txt", ".sh")
                and "__pycache__" not in p.parts
            ):
                files[str(p.relative_to(ROOT))] = file_hash(p)
    return {
        "commit": command(["git", "rev-parse", "HEAD"]).stdout.strip(),
        "branch": command(["git", "branch", "--show-current"]).stdout.strip(),
        "dirty": bool(
            command(
                ["git", "status", "--porcelain", "--untracked-files=no"]
            ).stdout.strip()
        ),
        "application_source_fingerprint": fingerprint(files),
        "source_files": files,
        "tool_fingerprint": fingerprint(
            {p.name: file_hash(p) for p in sorted(TOOL.glob("*.py"))}
        ),
    }


def safe_relative(value):
    p = Path(value)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise ValueError(f"Unsafe relative path: {value}")
    return p


def inventory_inputs(source, paths):
    result = {}
    for relative in paths:
        p = source / safe_relative(relative)
        if p.is_symlink() or (
            p.exists() and not p.resolve().is_relative_to(source.resolve())
        ):
            raise ValueError(f"Input escapes source directory: {relative}")
        result[relative] = {"exists": p.is_file()}
        if p.is_file():
            result[relative].update(bytes=p.stat().st_size, sha256=file_hash(p))
    return result


def input_paths(source, required):
    paths = list(required)
    for relative in required:
        for suffix in (".aux.xml", ".ovr", ".msk"):
            if (source / (relative + suffix)).is_file():
                paths.append(relative + suffix)
    return sorted(set(paths))


def byte_count(value):
    match = re.fullmatch(r"\s*([\d.]+)\s*([KMGT]?i?B)\s*", value, re.I)
    if not match:
        raise ValueError(f"Unknown Docker memory unit: {value}")
    units = {
        "b": 1,
        "kb": 1000,
        "mb": 1000**2,
        "gb": 1000**3,
        "tb": 1000**4,
        "kib": 1024,
        "mib": 1024**2,
        "gib": 1024**3,
        "tib": 1024**4,
    }
    return round(float(match[1]) * units[match[2].lower()])


def stats_row(line):
    # Docker emits terminal clear/cursor escapes even through some non-TTY pipes.
    clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line).strip()
    if not clean:
        return None
    row = json.loads(clean)
    if row.get("CPUPerc") in ("--", "") or row.get("MemUsage", "").startswith("--"):
        return None
    return (
        row["Name"],
        float(row["CPUPerc"].strip("%")),
        byte_count(row["MemUsage"].split("/")[0]),
    )


class ResourceSampler:
    def __init__(self, names, root):
        self.names = names
        self.root = root
        self.process = None
        self.thread = None
        self.errors = []

    def start(self):
        self.stderr = (self.root / "logs/resource-sampler.log").open("w")
        self.process = subprocess.Popen(
            ["docker", "stats", "--format", "{{json .}}", *self.names],
            stdout=subprocess.PIPE,
            stderr=self.stderr,
            text=True,
        )
        self.start_time = time.perf_counter()
        self.thread = threading.Thread(target=self.read, daemon=True)
        self.thread.start()

    def read(self):
        with (self.root / "measurements/resources.csv").open("w", newline="") as f, (
            self.root / "logs/docker-stats.raw"
        ).open("w") as raw:
            w = csv.writer(f)
            w.writerow(
                ["utc", "observation_seconds", "service", "cpu_percent", "memory_bytes"]
            )
            for line in self.process.stdout:
                raw.write(line)
                try:
                    parsed = stats_row(line)
                    if parsed is None:
                        continue
                    name, cpu, memory = parsed
                    service = self.names.get(name, name)
                    w.writerow(
                        [
                            dt.datetime.now(dt.timezone.utc).isoformat(),
                            time.perf_counter() - self.start_time,
                            service,
                            cpu,
                            memory,
                        ]
                    )
                    f.flush()
                except (ValueError, KeyError) as exc:
                    self.errors.append(str(exc))

    def stop(self):
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            self.thread.join(timeout=5)
            self.process.stdout.close()
            self.stderr.close()


@contextlib.contextmanager
def lock():
    BENCH.mkdir(exist_ok=True)
    p = BENCH / ".lock"
    try:
        fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError(
            "Another benchmark owns benchmark/.lock. See README recovery instructions."
        )
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    try:
        yield
    finally:
        p.unlink(missing_ok=True)


def selected_environment(raw):
    env = dict(
        item.split("=", 1) for item in raw["Config"].get("Env", []) if "=" in item
    )
    limits = raw["HostConfig"]
    unsupported = {
        k: limits.get(k)
        for k in ("CpuQuota", "CpuPeriod", "CpuShares", "MemoryReservation")
        if limits.get(k)
    }
    if unsupported:
        raise RuntimeError(
            f"Explicit resource settings need support before replication: {unsupported}"
        )
    return {
        "image": raw["Image"],
        "environment": {k: env[k] for k in ENV_KEYS if k in env},
        "limits": {
            k: limits.get(k)
            for k in ("Memory", "MemorySwap", "NanoCpus", "CpusetCpus", "ShmSize")
        },
    }


def runtime():
    raw = json.loads(docker("inspect", "qgis", "backend").stdout)
    engine = json.loads(docker("info", "--format", "{{json .}}").stdout)
    return {
        "qgis": selected_environment(raw[0]),
        "backend": selected_environment(raw[1]),
        "docker": {
            k: engine.get(k)
            for k in ("OSType", "Architecture", "NCPU", "MemTotal", "ServerVersion")
        },
        "host": {
            "system": platform.system(),
            "release": platform.release(),
            "architecture": platform.machine(),
            "logical_cpus": os.cpu_count(),
        },
    }


def create_container(name, service, env, network, root, work):
    temp = work.parent / "tmp" / service
    temp.mkdir(parents=True, exist_ok=True)
    args = [
        "run",
        "-d",
        "--name",
        name,
        "--network",
        network,
        "--network-alias",
        service,
        "--label",
        "heatstress.benchmark=true",
        "--label",
        f"heatstress.benchmark.run={root.name}",
        "--mount",
        f"type=bind,src={ROOT/service},dst=/app,readonly",
        "--mount",
        f"type=bind,src={work},dst=/data",
        "--mount",
        f"type=bind,src={temp},dst=/tmp",
        "--mount",
        f"type=bind,src={TOOL},dst=/bench,readonly",
        "--mount",
        f"type=bind,src={root},dst=/results",
        "-w",
        "/app",
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
    ]
    for key, value in env["environment"].items():
        args += ["-e", f"{key}={value}"]
    if service == "backend":
        args += ["-e", "QGIS_URL=http://qgis:8000"]
    limits = env["limits"]
    for key, flag in [
        ("Memory", "--memory"),
        ("MemorySwap", "--memory-swap"),
        ("CpusetCpus", "--cpuset-cpus"),
        ("ShmSize", "--shm-size"),
    ]:
        if limits.get(key):
            args += [flag, str(limits[key])]
    if limits.get("NanoCpus"):
        args += ["--cpus", str(limits["NanoCpus"] / 1e9)]
    # Bypass only the backend's DB setup entrypoint to avoid rewriting its mounted
    # source database. Session/update requests do not use that database.
    args += [
        "--entrypoint",
        "uvicorn",
        env["image"],
        "app:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    ]
    docker(*args, timeout=120)


def wait_ready(name, path):
    script = (
        'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000'
        + path
        + '",timeout=2).read()'
    )
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if (
            docker(
                "exec", name, "python3", "-c", script, check=False, timeout=8
            ).returncode
            == 0
        ):
            return
        time.sleep(1)
    raise RuntimeError(f"{name} did not become ready within 90 seconds")


def initialize_run(definition, label):
    identifier = (
        dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d_%H%M%S")
        + "_"
        + definition["id"]
        + "_"
        + uuid.uuid4().hex[:8]
    )
    root = BENCH / "runs" / identifier
    for sub in ("measurements", "logs", "outputs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    save(root / "scenario.json", definition)
    (root / "measurements/resources.csv").write_text(
        "utc,observation_seconds,service,cpu_percent,memory_bytes\n"
    )
    save(
        root / "validation.json", {"ok": False, "checks": [], "reason": "Not executed"}
    )
    save(root / "artifacts.json", [])
    run = {
        "schema_version": 1,
        "measurement_version": VERSION,
        "run_id": identifier,
        "scenario_id": definition["id"],
        "label": label,
        "status": "preparing",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "machine_id": machine_id(),
        "scenario_fingerprint": fingerprint(definition),
        "problems": [],
        "cache_policy": "Fresh containers and reset working files; host/Docker filesystem caches are uncontrolled. Not a cold-cache claim.",
        "randomness": "Application default; no seed injected",
        "internal_stage_timings": "Unavailable in external observation mode",
    }
    save(root / "run.json", run)
    return root, run


def collect(work, root, before, output_prefix="outputs", save_maps=False):
    artifacts = []
    if not work.exists():
        return artifacts
    for p in sorted(work.rglob("*")):
        if not p.is_file() or p.is_symlink():
            continue
        relative = p.relative_to(work).as_posix()
        h = file_hash(p)
        old = before.get(relative)
        # Capture rewritten artifacts even when the bytes happen to match inputs.
        if old and old["sha256"] == h and old["mtime_ns"] == p.stat().st_mtime_ns:
            continue
        dst = root / output_prefix / relative
        size = p.stat().st_size
        # Default retention is bounded text evidence, never GIS map data.
        retained = save_maps or (
            p.suffix.lower() in {".json", ".csv", ".txt", ".log", ".xml"}
            and size <= 1024 * 1024
        )
        if retained:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, dst)
        artifacts.append(
            {"path": output_prefix + "/" + relative, "bytes": size, "sha256": h, "retained": retained}
        )
    save(root / "artifacts.json", artifacts)
    return artifacts


def execute(definition, label, args):
    root, run = initialize_run(definition, label)
    run["artifact_retention"] = "all" if args.save_maps else "lightweight"
    print(f"Run: {root}", flush=True)
    prefix = "hsbench-" + uuid.uuid4().hex[:10]
    network = prefix + "-net"
    names = {prefix + "-qgis": "qgis", prefix + "-backend": "backend"}
    qgis, backend = names
    created = []
    network_created = False
    sampler = None
    before = {}
    original = {}
    work = root / ".work/data"
    start = time.perf_counter()
    request_started = False
    try:
        source = args.data.resolve()
        if source == BENCH or BENCH in source.parents:
            raise ValueError("Source data must be outside benchmark/")
        run["code"] = code_record()
        run["environment"] = runtime()
        run["environment_fingerprint"] = fingerprint(run["environment"])
        original = inventory_inputs(source, input_paths(source, definition["inputs"]))
        run["inputs"] = original
        run["input_fingerprint"] = fingerprint(original)
        run["source_directory"] = str(source)
        missing = [p for p, v in original.items() if not v["exists"]]
        if missing:
            run["status"] = "blocked"
            run["problems"].append("Missing input files: " + ", ".join(missing))
            return root, run
        needed = sum(v.get("bytes", 0) for v in original.values())
        if shutil.disk_usage(root).free < max(6 * 1024**3, needed * 5):
            raise RuntimeError(
                "Insufficient disk space for working copies and generated artifacts"
            )
        work.mkdir(parents=True)
        for relative in original:
            src = source / relative
            dst = work / relative
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            before[relative] = {
                "sha256": file_hash(dst),
                "mtime_ns": dst.stat().st_mtime_ns,
            }
            if before[relative]["sha256"] != original[relative]["sha256"]:
                raise RuntimeError(f"Input changed while copying: {relative}")
        for folder in (
            "uhi",
            "pet",
            "shadow-maps",
            "server/sessions",
            "raster",
            "json",
            "wind",
        ):
            (work / folder).mkdir(parents=True, exist_ok=True)
        network_created = True
        docker("network", "create", "--label", "heatstress.benchmark=true", network)
        for name, service in names.items():
            created.append(name)
            create_container(
                name, service, run["environment"][service], network, root, work
            )
        # Inspect exact disposable copies using observation code, with no application imports.
        docker(
            "exec",
            "-e",
            "GDAL_PAM_ENABLED=NO",
            qgis,
            "python3",
            "/bench/probe.py",
            "inspect",
            "/data",
            "/results/scenario.json",
            "/results/preflight.json",
            timeout=90,
        )
        preflight = json.loads((root / "preflight.json").read_text())
        run["versions"] = preflight["versions"]
        if not preflight["ok"]:
            run["status"] = "blocked"
            run["problems"].append(
                "Dataset/field preflight failed; see preflight.json. No calculation request submitted."
            )
            return root, run
        wait_ready(qgis, "/ping")
        wait_ready(backend, "/openapi.json")
        run["preparation_seconds"] = time.perf_counter() - start
        run["status"] = "running"
        save(root / "run.json", run)
        sampler = ResourceSampler(names, root)
        sampler.start()
        request_started = True
        target = (
            "http://127.0.0.1:8000"
            if definition["service"] == "backend"
            else "http://qgis:8000"
        )
        # The timed HTTP request runs inside the backend container. No exposed host ports.
        result = docker(
            "exec",
            backend,
            "python3",
            "/bench/probe.py",
            "request",
            target + definition["endpoint"],
            "/results/scenario.json",
            "/results/response.json",
            str(args.timeout),
            timeout=args.timeout + 45,
            check=False,
        )
        (root / "logs/request-driver.log").write_text(result.stdout + result.stderr)
        if (root / "response.json").exists():
            run["request"] = json.loads((root / "response.json").read_text())
            run["status"] = run["request"]["status"]
        else:
            run["status"] = "indeterminate"
            run["problems"].append("Request driver ended without a response record")
    except KeyboardInterrupt:
        run["status"] = "interrupted"
        run["problems"].append(
            "Interrupted; benchmark containers stopped before collecting artifacts"
        )
    except Exception as exc:
        run["status"] = "indeterminate" if request_started else "error"
        run["problems"].append(f"{type(exc).__name__}: {exc}")
    finally:
        if sampler:
            try:
                sampler.stop()
                run["resource_sampler_errors"] = sampler.errors
            except Exception as exc:
                run["problems"].append(f"Resource sampler shutdown failed: {exc}")
        # Stop containers before reading shared outputs; timeout does not mean the
        # server stopped calculating. Never retry automatically while it may run.
        for name in created:
            try:
                docker("stop", "-t", "2", name, timeout=15)
                logs = docker("logs", name, check=False, timeout=20)
                (root / "logs" / f"{names[name]}.log").write_text(
                    logs.stdout + logs.stderr
                )
                state = json.loads(docker("inspect", name).stdout)[0]["State"]
                run.setdefault("container_exit", {})[names[name]] = {
                    "oom_killed": state.get("OOMKilled"),
                    "exit_code": state.get("ExitCode"),
                }
            except Exception as exc:
                run["problems"].append(
                    f"Container shutdown/log collection failed: {exc}"
                )
        try:
            artifacts = collect(work, root, before, save_maps=args.save_maps)
            artifacts += collect(
                work.parent / "tmp", root, {}, "outputs/container-temporary", save_maps=args.save_maps
            )
            save(root / "artifacts.json", artifacts)
            run["generated_artifact_bytes"] = sum(p["bytes"] for p in artifacts)
            run["artifact_bytes"] = sum(p["bytes"] for p in artifacts if p["retained"])
            if request_started and qgis in created:
                validation_name = prefix + "-validate"
                try:
                    docker(
                        "run",
                        "--rm",
                        "--name",
                        validation_name,
                        "--network",
                        "none",
                        "--entrypoint",
                        "python3",
                        "-e",
                        "GDAL_PAM_ENABLED=NO",
                        "-e",
                        "PYTHONDONTWRITEBYTECODE=1",
                        "--mount",
                        f"type=bind,src={TOOL},dst=/bench,readonly",
                        "--mount",
                        f"type=bind,src={root},dst=/results",
                        "--mount",
                        f"type=bind,src={work},dst=/generated,readonly",
                        run["environment"]["qgis"]["image"],
                        "/bench/probe.py",
                        "validate",
                        "/generated",
                        "/results/scenario.json",
                        "/results/validation.json",
                        "/results/artifacts.json",
                        timeout=180,
                    )
                finally:
                    docker("rm", "-f", validation_name, check=False)
                if (
                    run["status"] == "completed"
                    and not json.loads((root / "validation.json").read_text())["ok"]
                ):
                    run["status"] = "invalid"
                    run["problems"].append(
                        "HTTP request succeeded but final artifact validation failed"
                    )
            if original:
                unchanged = (
                    inventory_inputs(
                        args.data.resolve(),
                        input_paths(args.data.resolve(), definition["inputs"]),
                    )
                    == original
                )
                run["source_data_unchanged"] = unchanged
                if not unchanged:
                    run["status"] = "invalid"
                    run["problems"].append(
                        "Source inputs changed during run; do not use for comparisons"
                    )
            if (
                run.get("code")
                and code_record()["application_source_fingerprint"]
                != run["code"]["application_source_fingerprint"]
            ):
                run["status"] = "invalid"
                run["problems"].append("Application code changed during run")
        except Exception as exc:
            run["problems"].append(f"Artifact verification failed: {exc}")
            if run["status"] == "completed":
                run["status"] = "invalid"
        for name in created:
            try:
                docker("rm", "-f", name, check=False)
            except Exception as exc:
                run["problems"].append(f"Cleanup needed for {name}: {exc}")
        if network_created:
            try:
                docker("network", "rm", network, check=False)
            except Exception as exc:
                run["problems"].append(f"Cleanup needed for {network}: {exc}")
        if (
            work.exists()
            and not args.keep_work
            and not any("Artifact verification failed" in p for p in run["problems"])
        ):
            shutil.rmtree(root / ".work")
        run["finished_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        run["total_observation_seconds"] = time.perf_counter() - start
        save(root / "run.json", run)
        with (root / "measurements/timings.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["metric", "seconds", "status"])
            w.writerow(
                [
                    "request",
                    run.get("request", {}).get("elapsed_seconds", ""),
                    run["status"],
                ]
            )
            w.writerow(["preparation", run.get("preparation_seconds", ""), "excluded"])
            w.writerow(
                ["entire_tool_run", run["total_observation_seconds"], "excluded"]
            )
        run_report(root)
        print(f'{run["status"]}: {root/"report.html"}', flush=True)
    return root, run


def definitions():
    return {
        p.stem: json.loads(p.read_text())
        for p in sorted((BENCH / "scenarios").glob("*.json"))
    }


def compare_rasters(paths, destination):
    records = [json.loads((Path(p) / "run.json").read_text()) for p in paths]
    _, warnings = compare_records(records)
    baseline = {}
    results = []
    for path, run in zip(paths, records):
        scenario = run["scenario_id"]
        if run["status"] != "completed":
            continue
        if any(w.startswith(scenario + ":") for w in warnings):
            results.append(
                {
                    "run": run["run_id"],
                    "status": "not_compared",
                    "reason": "Scenario/input/environment definitions differ",
                }
            )
            continue
        checks = json.loads((Path(path) / "validation.json").read_text()).get(
            "checks", []
        )
        raster = next(
            (c["path"] for c in checks if c.get("ok") and "raster" in c), None
        )
        if not raster:
            continue
        if not (Path(path) / "outputs" / raster).is_file():
            results.append({
                "run": run["run_id"], "scenario": scenario,
                "status": "unavailable",
                "reason": "Final map was not retained; rerun with --save-maps for numerical comparisons",
            })
            continue
        if scenario not in baseline:
            baseline[scenario] = (path, run, raster)
            continue
        first, first_run, first_raster = baseline[scenario]
        name = "hsbench-diff-" + uuid.uuid4().hex[:10]
        filename = run["run_id"] + "_difference.json"
        item = {
            "baseline": first_run["run_id"],
            "run": run["run_id"],
            "scenario": scenario,
        }
        try:
            docker(
                "run",
                "--rm",
                "--name",
                name,
                "--network",
                "none",
                "--entrypoint",
                "python3",
                "-e",
                "GDAL_PAM_ENABLED=NO",
                "-e",
                "PYTHONDONTWRITEBYTECODE=1",
                "--mount",
                f"type=bind,src={Path(first).resolve()},dst=/first,readonly",
                "--mount",
                f"type=bind,src={Path(path).resolve()},dst=/second,readonly",
                "--mount",
                f"type=bind,src={TOOL},dst=/bench,readonly",
                "--mount",
                f"type=bind,src={destination},dst=/results",
                run["environment"]["qgis"]["image"],
                "/bench/probe.py",
                "difference",
                "/first/outputs/" + first_raster,
                "/second/outputs/" + raster,
                "/results/" + filename,
                timeout=180,
            )
            item.update(
                status="compared",
                result=json.loads((destination / filename).read_text()),
            )
        except Exception as exc:
            item.update(status="unavailable", reason=str(exc))
        finally:
            docker("rm", "-f", name, check=False)
        results.append(item)
    save(destination / "raster_differences.json", results)
    from report import raster_difference_section

    html = (destination / "report.html").read_text()
    section = raster_difference_section(results)
    (destination / "report.html").write_text(
        html.replace("</main>", section + "</main>")
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("list", help="List existing scenario boundaries and prerequisites")
    sub.add_parser(
        "test", help="Run host HTTP tests and GIS tests with synthetic rasters only"
    )
    r = sub.add_parser("run")
    r.add_argument("scenario")
    r.add_argument("--repeat", type=int, default=1)
    r.add_argument("--label", default="baseline")
    r.add_argument("--data", type=Path, default=ROOT / "data")
    r.add_argument("--timeout", type=float, default=1200)
    r.add_argument("--save-maps", action="store_true",
                   help="Retain all generated files, including maps and intermediate GIS data")
    r.add_argument("--keep-work", action="store_true",
                   help="Keep disposable working data for debugging (may be several GB)")
    c = sub.add_parser("compare")
    c.add_argument("runs", nargs="+")
    c.add_argument("--name", default="comparison")
    a = p.parse_args()
    os.chdir(ROOT)
    if a.action == "test":
        host = command(
            [
                sys.executable,
                "-B",
                "-m",
                "unittest",
                "discover",
                "-s",
                TOOL / "tests",
                "-p",
                "test_benchmark.py",
                "-v",
            ],
            check=False,
        )
        print(host.stdout + host.stderr)
        if host.returncode:
            return host.returncode
        image = json.loads(docker("inspect", "qgis").stdout)[0]["Image"]
        result = docker(
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "python3",
            "-e",
            "GDAL_PAM_ENABLED=NO",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            "--mount",
            f"type=bind,src={TOOL},dst=/bench,readonly",
            image,
            "-m",
            "unittest",
            "discover",
            "-s",
            "/bench/tests",
            "-p",
            "test_rasters.py",
            "-v",
            check=False,
        )
        print(result.stdout + result.stderr)
        return result.returncode
    if a.action == "list":
        for key, d in definitions().items():
            print(f'{key}: {d["description"]}\n  {d["boundary"]}')
        return 0
    if a.action == "compare":
        paths = []
        for value in a.runs:
            path = Path(value)
            paths.append(path.resolve() if path.is_dir() else BENCH / "runs" / value)
        dest = (
            BENCH
            / "comparisons"
            / (
                dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d_%H%M%S")
                + "_"
                + re.sub(r"[^\w-]", "_", a.name)
                + "_"
                + uuid.uuid4().hex[:6]
            )
        )
        report_path = comparison_report(paths, dest)
        compare_rasters(paths, dest)
        print(report_path)
        return 0
    if a.repeat < 1 or a.repeat > 20 or a.timeout <= 0:
        p.error("repeat must be 1..20 and timeout must be positive")
    defs = definitions()
    if a.scenario not in defs:
        p.error("Unknown scenario; use list")
    with lock():
        paths = []
        status = 0
        for _ in range(a.repeat):
            path, run = execute(defs[a.scenario], a.label, a)
            paths.append(path)
            if run["status"] != "completed":
                status = 2
                break
        if len(paths) > 1:
            dest = BENCH / "comparisons" / (paths[0].name + "_repetitions")
            report_path = comparison_report(paths, dest)
            compare_rasters(paths, dest)
            print(report_path)
    return status


if __name__ == "__main__":

    def interrupted(signum, frame):
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, interrupted)
    try:
        sys.exit(main())
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        print(f"Benchmark: {exc}", file=sys.stderr)
        sys.exit(2)
