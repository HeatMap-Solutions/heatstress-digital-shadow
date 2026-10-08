"""Offline reports derived exclusively from saved measurements."""

import csv
import html
import json
import statistics
from pathlib import Path


def esc(value):
    return html.escape(str(value))


def page(title, body):
    return (
        """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>"""
        + esc(title)
        + """</title>
<style>body{font:16px system-ui,sans-serif;color:#20313f;background:#eef3f6;margin:0}
main{max-width:1080px;margin:32px auto;padding:40px;background:white;border-radius:16px}
h1{font-size:32px;margin:8px 0 24px}h2{margin-top:32px}small,.muted{color:#586c78}
table{border-collapse:collapse;width:100%;margin:20px 0}th,td{text-align:left;padding:12px;border-bottom:1px solid #dce4e9}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f6f8;padding:16px;font-size:13px}
.notice{border-left:5px solid #d18b17;background:#fff5df;padding:15px}.cards{display:flex;gap:16px;flex-wrap:wrap}
.card{padding:18px;background:#eef5f8;border-radius:8px;min-width:180px}.card strong{display:block;font-size:26px}
.bar{height:16px;background:#27858e;border-radius:3px;min-width:1px}.failed,.blocked,.invalid{color:#9b430c}
.completed{color:#146a41}a{color:#136277}@media print{body{background:white}main{margin:0;padding:0}details{display:block}}
</style><main><small>HEATSTRESS · CALCULATION BENCHMARK</small><h1>"""
        + esc(title)
        + "</h1>"
        + body
        + "</main></html>"
    )


def resources(root):
    path = root / "measurements/resources.csv"
    rows = []
    if path.exists():
        with path.open() as stream:
            rows = list(csv.DictReader(stream))
    by_service = {}
    for row in rows:
        item = by_service.setdefault(row["service"], {"cpu": [], "memory": []})
        item["cpu"].append(float(row["cpu_percent"]))
        item["memory"].append(int(row["memory_bytes"]))
    return {
        key: {
            "samples": len(v["cpu"]),
            "mean_cpu_percent": statistics.mean(v["cpu"]),
            "highest_sampled_memory_mib": max(v["memory"]) / 1024**2,
        }
        for key, v in by_service.items()
    }


def preflight_problems(root):
    path = Path(root) / "preflight.json"
    if not path.exists():
        return []
    problems = []
    for check in json.loads(path.read_text()).get("checks", []):
        if check.get("ok"):
            continue
        if "required_field" in check:
            problems.append(
                "Required wind field: "
                + check["required_field"]
                + ". Supplied fields: "
                + ", ".join(check.get("actual_fields", []))
                + ". No field was renamed."
            )
        else:
            problems.append(str(check.get("reason", check)))
    return problems


def run_report(root):
    root = Path(root)
    run = json.loads((root / "run.json").read_text())
    definition = json.loads((root / "scenario.json").read_text())
    duration = run.get("request", {}).get("elapsed_seconds")
    summary = resources(root)
    body = "<p>" + esc(definition["description"]) + '</p><div class="cards">'
    body += (
        '<div class="card">Status<strong class="'
        + esc(run["status"])
        + '">'
        + esc(run["status"])
        + "</strong></div>"
    )
    body += (
        '<div class="card">Request duration<strong>'
        + ("Not measured" if duration is None else f"{duration:.2f} s")
        + "</strong></div></div>"
    )
    if run["status"] != "completed":
        body += '<p class="notice">This run is not a successful calculation benchmark. Failure durations are excluded from successful timing summaries.</p>'
    for problem in run.get("problems", []) + preflight_problems(root):
        body += '<p class="notice">' + esc(problem) + "</p>"
    if run.get("resource_sampler_errors"):
        body += '<p class="notice">Some resource samples could not be parsed. See run.json; resource coverage is incomplete.</p>'
    body += (
        "<h2>Measurement boundary</h2><p>"
        + esc(definition["boundary"])
        + "</p><p><b>Excluded:</b> "
        + esc(definition["excludes"])
        + "</p>"
    )
    body += "<h2>Container resources</h2><table><tr><th>Service</th><th>Samples</th><th>Mean CPU</th><th>Highest sampled memory</th></tr>"
    for service, r in summary.items():
        body += f'<tr><td>{esc(service)}</td><td>{r["samples"]}</td><td>{r["mean_cpu_percent"]:.1f}%</td><td>{r["highest_sampled_memory_mib"]:.1f} MiB</td></tr>'
    body += '</table><p class="muted">Docker samples cover request observation, with small startup/teardown margins. CPU can exceed 100% on multiple cores. Sampling may miss short memory peaks; zero samples means unavailable, not zero usage.</p>'
    evidence = [
        ("run.json", "Run metadata"),
        ("scenario.json", "Exact scenario"),
        ("measurements/timings.csv", "Timings CSV"),
        ("measurements/resources.csv", "Resources CSV"),
        ("preflight.json", "Input checks"),
        ("validation.json", "Output checks"),
        ("artifacts.json", "Artifact inventory"),
    ]
    body += (
        "<h2>Evidence</h2><p>"
        + " · ".join(
            f'<a href="{name}">{label}</a>'
            for name, label in evidence
            if (root / name).exists()
        )
        + "</p>"
    )
    body += "<details><summary>How this was measured</summary><p>Python perf_counter measures from request submission until its full response or exception. Session setup, data preparation, container startup, output inspection and reporting are excluded. Docker supplies resource samples. GDAL reads newly generated rasters. Internal stage timing is unavailable: application logic is unchanged.</p><p>Random foliage remains unchanged. Output differences are not automatically regressions. A completed run means the request succeeded and required generated rasters passed readability and valid-cell checks, not scientific accuracy.</p></details>"
    for name in ("preflight.json", "validation.json"):
        if (root / name).exists():
            body += (
                "<details><summary>"
                + esc(name)
                + "</summary><pre>"
                + esc((root / name).read_text())
                + "</pre></details>"
            )
    body += (
        "<details><summary>Environment and full run record</summary><pre>"
        + esc(json.dumps(run, indent=2))
        + "</pre></details>"
    )
    (root / "report.html").write_text(page(definition["title"], body))


def compare_records(records):
    if not records:
        raise ValueError("Select at least one saved run")
    if len({r["run_id"] for r in records}) != len(records):
        raise ValueError(
            "Duplicate run selected; each attempt may be counted only once"
        )
    identities = {r.get("machine_id") for r in records}
    if None in identities or len(identities) != 1:
        raise ValueError(
            "Cross-machine comparison is not allowed (or machine identity is missing)"
        )
    groups = {}
    warnings = []
    for r in records:
        groups.setdefault(r["scenario_id"], []).append(r)
    for scenario, rows in groups.items():
        for key in (
            "scenario_fingerprint",
            "input_fingerprint",
            "environment_fingerprint",
            "measurement_version",
        ):
            if len({r.get(key) for r in rows}) > 1:
                warnings.append(
                    f"{scenario}: {key} differs; timings are not a controlled before/after comparison."
                )
    return groups, warnings


def comparison_report(paths, destination):
    records = [json.loads((Path(p) / "run.json").read_text()) for p in paths]
    groups, warnings = compare_records(records)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    body = "<p>Same-machine evidence. Each scenario has its own timing group; generation and tree-update times are never added into an end-to-end total.</p>"
    for warning in warnings:
        body += '<p class="notice">' + esc(warning) + "</p>"
    body += "<h2>Successful request durations by scenario and label</h2><table><tr><th>Scenario / label</th><th>Completed / attempts</th><th>Median</th><th>Range</th><th>Median change vs first label</th></tr>"
    for scenario, rows in groups.items():
        labels = list(dict.fromkeys(r["label"] for r in rows))
        baseline = None
        incompatible = any(w.startswith(scenario + ":") for w in warnings)
        for label in labels:
            subset = [r for r in rows if r["label"] == label]
            values = [
                r["request"]["elapsed_seconds"]
                for r in subset
                if r["status"] == "completed"
            ]
            median = statistics.median(values) if values else None
            change = "—"
            if baseline and median is not None and not incompatible:
                change = f"{median-baseline:+.2f} s ({100*(median-baseline)/baseline:+.1f}%; negative = shorter)"
            if label == labels[0]:
                baseline = median
            body += f'<tr><td>{esc(scenario)} / {esc(label)}</td><td>{len(values)} / {len(subset)}</td><td>{f"{median:.2f} s" if values else "Unavailable"}</td><td>{f"{min(values):.2f}–{max(values):.2f} s" if values else "—"}</td><td>{change}</td></tr>'
    body += "</table><p>These are local request durations, not a reproduction of the historical whole-map runtime. Containers and working data restart for each attempt; host filesystem caches and background workload remain uncontrolled.</p>"
    blocked = [r for r in records if r["status"] != "completed"]
    if blocked:
        body += "<h2>Coverage gaps</h2>"
        path_by_id = {r["run_id"]: p for r, p in zip(records, paths)}
        for r in blocked:
            body += (
                '<p class="notice"><b>'
                + esc(r["scenario_id"])
                + ": "
                + esc(r["status"])
                + "</b><br>"
                + esc(
                    "; ".join(
                        r.get("problems", [])
                        + preflight_problems(path_by_id[r["run_id"]])
                    )
                )
                + "</p>"
            )
    body += "<h2>Observed resource use</h2><table><tr><th>Scenario / label</th><th>Service</th><th>Highest sampled memory</th><th>Mean CPU range across runs</th></tr>"
    resource_groups = {}
    for r, p in zip(records, paths):
        if r["status"] != "completed":
            continue
        for service, summary in resources(Path(p)).items():
            resource_groups.setdefault(
                (r["scenario_id"], r["label"], service), []
            ).append(summary)
    for (scenario, label, service), summaries in resource_groups.items():
        peak = max(s["highest_sampled_memory_mib"] for s in summaries)
        cpu = [s["mean_cpu_percent"] for s in summaries]
        body += f"<tr><td>{esc(scenario)} / {esc(label)}</td><td>{esc(service)}</td><td>{peak:.1f} MiB</td><td>{min(cpu):.1f}–{max(cpu):.1f}%</td></tr>"
    body += '</table><p class="muted">Maximum observed memory across successful attempts, separately per service; this can miss short peaks. CPU above 100% means multiple cores. Missing samples mean unavailable. Full timestamped samples are saved with each run.</p><h2>Every attempt</h2>'
    values = [
        r.get("request", {}).get("elapsed_seconds", 0)
        for r in records
        if r["status"] == "completed"
    ]
    maximum = max(values, default=1) or 1
    body += "<table><tr><th>Run</th><th>Status</th><th>Duration</th><th>Successful duration</th></tr>"
    for r, p in zip(records, paths):
        t = r.get("request", {}).get("elapsed_seconds")
        bar = ""
        if r["status"] == "completed":
            bar = f'<div class="bar" style="width:{100*t/maximum:.1f}%"></div>'
        import os

        link = Path(os.path.relpath(Path(p) / "report.html", destination)).as_posix()
        body += f'<tr><td><a href="{esc(link)}">{esc(r["run_id"])}</a></td><td class="{esc(r["status"])}">{esc(r["status"])}</td><td>{f"{t:.2f} s" if t is not None else "Not measured"}</td><td>{bar}</td></tr>'
    body += "</table><p>Failures remain visible and are excluded from successful medians. Repetitions retain unseeded foliage randomness. No scientific acceptance threshold has been applied. Open individual reports for resource usage, input identity, output validation and failure evidence.</p>"
    (destination / "report.html").write_text(page("Baseline comparison", body))
    (destination / "comparison.json").write_text(
        json.dumps(
            {"runs": [r["run_id"] for r in records], "warnings": warnings}, indent=2
        )
        + "\n"
    )
    with (destination / "comparison.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run_id", "scenario", "label", "status", "elapsed_seconds"])
        for r in records:
            w.writerow(
                [
                    r["run_id"],
                    r["scenario_id"],
                    r["label"],
                    r["status"],
                    r.get("request", {}).get("elapsed_seconds", ""),
                ]
            )
    return destination / "report.html"


def raster_difference_section(results):
    body = "<h2>Output differences</h2><p>Final raster compared with the first successful run of the same scenario. Tree PET differences are in °C; shadow differences are in raster values. Matching grids are required. Random foliage remains unchanged; no scientific acceptance tolerance is applied.</p>"
    body += "<table><tr><th>Scenario / attempt</th><th>Mean absolute difference</th><th>Largest pixel difference</th><th>Changed NoData cells</th></tr>"
    for item in results:
        result = item.get("result", {})
        body += (
            "<tr><td>"
            + esc(item.get("scenario", ""))
            + "<br><small>"
            + esc(item["run"])
            + "</small></td>"
        )
        if result.get("compatible") and result.get("joint_valid_pixels", 0):
            body += f'<td>{result["mean_absolute_difference"]:.6g}</td><td>{result["maximum_absolute_difference"]:.6g}</td><td>{result["nodata_mask_differences"]:,}</td>'
        else:
            body += (
                '<td colspan="3">'
                + esc(
                    item.get(
                        "reason",
                        result.get("reason", "No jointly valid cells to compare"),
                    )
                )
                + "</td>"
            )
        body += "</tr>"
    body += (
        "</table><p>A small whole-map mean can hide larger local differences. These comparisons describe repeatability; they do not establish scientific accuracy.</p><details><summary>Full output-comparison evidence</summary><pre>"
        + esc(json.dumps(results, indent=2))
        + '</pre></details><p><a href="raster_differences.json">Output differences JSON</a></p>'
    )
    return body
