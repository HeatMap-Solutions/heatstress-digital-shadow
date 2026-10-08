# Run and understand the benchmarks

Start here when you want to collect measurements yourself.

The [complete usage and artifact guide](../tools/benchmark/README.md) explains
requirements, every scenario, generated files, comparisons, and troubleshooting.

## Run your first benchmark

1. Open a terminal in the repository root (`heatstress-digital-shadow`, the folder
   containing `docker-compose.yml`). Start Docker Desktop if you use it.
2. Check that Python and Docker are available and the existing app containers have
   been created:

   ```sh
   python3 --version
   docker info
   docker ps -a --filter name=qgis --filter name=backend
   ```

   You need Python 3.9+ and containers named exactly `qgis` and `backend`, plus the
   supplied `data/` folder. If these are missing, follow the setup requirements in
   the complete guide first. The benchmark reuses those images; it does not build
   the application for you.

3. Run one tree placement once:

   ```sh
   python3 tools/benchmark/bench.py run one-tree --label baseline
   ```

   Wait for `completed:` and the printed report path. A different status means
   you should open that report to inspect the reason.
4. Open that run's `report.html` in your browser. No web server is needed: find it
   in `benchmark/runs/` and open it with your browser. Start with the status and
   duration, then inspect the resource table and evidence links.
5. For measurements you can discuss with the client, collect three repetitions:

   ```sh
   python3 tools/benchmark/bench.py run one-tree --repeat 3 --label baseline
   python3 tools/benchmark/bench.py run ten-trees --repeat 3 --label baseline
   python3 tools/benchmark/bench.py run shadow-map --repeat 3 --label baseline
   ```

   Run these commands sequentially. Each attempt has its own folder; each command
   also produces a comparison report if more than one attempt was collected.
   Keep background workload comparable and compare only runs on your own machine.

By default, only logs and lightweight evidence are saved; generated maps are
validated before disposable data is removed. Add `--save-maps` to a `run` command
to retain every generated file. Use it for both sets of runs when you need later
pixel-by-pixel comparisons. It can retain about 1.2 GB per old full-area tree run.
`--keep-work` is a separate debugging option that also keeps bulky working copies.

## Find your results

Run folders already include **both date and time**, in UTC:

```text
benchmark/runs/2026-09-07_124418_one-tree_6a9320e8/
               YYYY-MM-DD_HHMMSS_scenario_unique-id
```

This example started on **7 September 2026 at 12:44:18 UTC**. The final ID avoids
collisions if two attempts start within the same second. The label (for example,
`baseline` or `after-change`) is saved in `run.json` and comparison tables.

For artifact explanations, see [What is saved](../tools/benchmark/README.md#what-is-saved).
For interpreting timestamps, see [Folder naming and timestamps](../tools/benchmark/README.md#folder-naming-and-timestamps).

PET generation currently records a blocked attempt because of the supplied wind
field name; independent wind generation records missing DSM/DTM inputs. These are
coverage gaps, not successful calculation timings. The guide lists the commands
and exact measurement boundaries for both.

## Compare later changes

Run the same scenario after a change, using `--label after-change`. Then select
the saved run-folder names, baseline first:

```sh
python3 tools/benchmark/bench.py compare BASELINE_RUN_ID AFTER_CHANGE_RUN_ID --name meeting
```

Replace the two placeholders with actual folder names. You can list all three
baseline and all three later runs in the same command. Open the printed comparison
report; the tool groups attempts by scenario and label.

## Check the benchmark tool

```sh
python3 tools/benchmark/bench.py test
```

This tests the measurement tool, including synthetic GIS outputs. It does not
replace running the real scenarios above or establish scientific map accuracy.
