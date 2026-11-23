param(
    [Parameter(Mandatory)]
    [string]$Package,
    [Parameter(Mandatory)]
    [string]$AhnIndex,
    [string]$Image = 'project-qgis'
)
$ErrorActionPreference = 'Stop'
$repository = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$sourcePackage = (Resolve-Path -LiteralPath $Package).Path
$sourceIndex = (Resolve-Path -LiteralPath $AhnIndex).Path
docker info --format '{{.ServerVersion}}'
if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop before resuming the batch.' }
do {
    docker run --rm --entrypoint python3 `
        --mount "type=bind,src=$repository,dst=/workspace" `
        --mount "type=bind,src=$sourcePackage,dst=/inputs,readonly" `
        --mount "type=bind,src=$sourceIndex,dst=/ahn-index.gpkg,readonly" `
        -e ZEELAND_AHN_INDEX=/ahn-index.gpkg `
        -e QT_QPA_PLATFORM=offscreen -e GDAL_CACHEMAX=512 -e GDAL_NUM_THREADS=2 `
        -e ZEELAND_SCRATCH=/tmp/zeeland-wind-scratch -w /workspace `
        $Image scripts/run_zeeland_wind.py --workers 2 --max-new-tiles 8 --skip-assembly --skip-failed
    if ($LASTEXITCODE -ne 0) { throw 'Batch did not finish; completed tiles are retained for resume.' }
    $state = Get-Content -LiteralPath (Join-Path $repository 'output/zeeland-wind/batch-state.json') -Raw | ConvertFrom-Json
} while ($state.counts.pending -gt 0)
docker run --rm --network none --entrypoint python3 `
    --mount "type=bind,src=$repository,dst=/workspace" `
    -e QT_QPA_PLATFORM=offscreen -w /workspace $Image scripts/finalize_zeeland_wind.py
if ($LASTEXITCODE -ne 0) { throw 'Final export checks failed; inspect output/zeeland-wind.' }
