# Exercises the frozen worker the way the GUI does: solve, sweep (spawns worker jobs), optimize, report.
# usage: pwsh packaging/smoke_bundle.ps1 <folder with cirplas-solve.exe> <work folder>
param([string]$Bundle = "dist/CirPlas", [string]$Work = "smoke")
$ErrorActionPreference = "Stop"
$exe = Join-Path (Resolve-Path $Bundle) "cirplas-solve.exe"
New-Item -ItemType Directory -Force $Work | Out-Null
$work = Resolve-Path $Work
$proj = Join-Path $work "p.cirplas.json"
Copy-Item "cirplas/data/examples/demo_300mm_60MHz.cirplas.json" $proj

function Step([string]$name, [string[]]$arguments, [string]$expect) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "$name failed with exit code $LASTEXITCODE" }
    if (-not (Test-Path $expect)) { throw "$name did not write $expect" }
    Write-Host "ok: $name"
}

Step "solve" @($proj, "-o", "$work/run.h5", "--mode", "ac", "--progress", "none") "$work/run.h5"
Step "sweep" @("sweep", $proj, "--param", "circuit.ports.W.sources.0.v", "--values", "300,400", "--mode", "ac", "-o", "$work/sweep", "--jobs", "2", "--progress", "none") "$work/sweep/results.json"
Step "optimize" @("optimize", $proj, "--param", "circuit.ports.W.sources.0.v:300:500", "--objective", "uniformity_se", "--grid", "2", "--max-eval", "3", "-o", "$work/best.cirplas.json", "--progress", "none") "$work/best.cirplas.json"
Step "report" @("report", "$work/run.h5", "-o", "$work/report.html") "$work/report.html"
