<#
Portable PostgreSQL 16 + pgvector for the PPM dev machine.
No installer, no Windows service: binaries and data live under -EnvRoot and are
started/stopped with pg_ctl. Mirrors docker-compose.yml (official pgvector
image) which is the standard deployment path for an office machine.

Usage:
  scripts/dev_pg.ps1 init     extract, initdb, start, create ppm + ppm_test, enable vector
  scripts/dev_pg.ps1 start
  scripts/dev_pg.ps1 stop
  scripts/dev_pg.ps1 status
  scripts/dev_pg.ps1 psql
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('init', 'start', 'stop', 'status', 'psql')]
    [string]$Action = 'status',
    [string]$EnvRoot = 'D:\ppm-env',
    [int]$Port = 5432
)

$ErrorActionPreference = 'Stop'

$PgVersion       = '16.15'
$PgvectorVersion = '0.8.6'
$PgZipName       = "postgresql-$PgVersion-windows-x64-binaries.zip"
$PgvectorZipName = "vector.v$PgvectorVersion-pg16.zip"
# EDB publishes versioned binaries behind sbp.enterprisedb.com getfile links;
# the fileid below is the PostgreSQL 16.15 Windows x86-64 archive.
$PgUrl           = 'https://sbp.enterprisedb.com/getfile.jsp?fileid=1260623'
$PgvectorUrl     = "https://github.com/andreiramani/pgvector_pgsql_windows/releases/download/0.8.6_16/vector.v$PgvectorVersion-pg16.zip"

$Downloads = Join-Path $EnvRoot 'downloads'
$PgHome    = Join-Path $EnvRoot 'pgsql'
$PgData    = Join-Path $EnvRoot 'pgdata'
$PgLog     = Join-Path $EnvRoot 'postgres.log'
$PgBin     = Join-Path $PgHome 'bin'

$SuperUser     = 'ppm'
$SuperPassword = 'ppm_dev_password'
$Databases     = @('ppm', 'ppm_test')

function Get-PgExe([string]$Name) { Join-Path $PgBin "$Name.exe" }

function Assert-Download([string]$Path, [string]$Url) {
    if (Test-Path $Path) { return }
    Write-Host "downloading $Url"
    curl.exe -L --fail --output $Path $Url
    if ($LASTEXITCODE -ne 0) { throw "download failed: $Url" }
}

function Install-Binaries {
    if (Test-Path (Get-PgExe 'pg_ctl')) {
        Write-Host "postgres binaries already present at $PgHome"
    } else {
        $zip = Join-Path $Downloads $PgZipName
        Assert-Download $zip $PgUrl
        Write-Host "extracting $PgZipName"
        New-Item -ItemType Directory -Force -Path $EnvRoot | Out-Null
        tar.exe -xf $zip -C $EnvRoot
        if (-not (Test-Path (Get-PgExe 'pg_ctl'))) { throw "extraction did not produce $PgBin\pg_ctl.exe" }
    }

    # pgvector: lib/vector.dll -> pgsql\lib, share\extension\* -> pgsql\share\extension
    if (Test-Path (Join-Path $PgHome 'lib\vector.dll')) {
        Write-Host 'pgvector already installed'
    } else {
        $zip = Join-Path $Downloads $PgvectorZipName
        Assert-Download $zip $PgvectorUrl
        $stage = Join-Path $Downloads 'pgvector-stage'
        Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Force -Path $stage | Out-Null
        tar.exe -xf $zip -C $stage
        Copy-Item (Join-Path $stage 'lib\*') (Join-Path $PgHome 'lib') -Force
        Copy-Item (Join-Path $stage 'share\extension\*') (Join-Path $PgHome 'share\extension') -Force
        if (-not (Test-Path (Join-Path $PgHome 'share\extension\vector.control'))) { throw 'pgvector copy failed' }
        Write-Host "pgvector $PgvectorVersion installed"
    }
}

function Initialize-Cluster {
    if (Test-Path (Join-Path $PgData 'PG_VERSION')) {
        Write-Host "cluster already initialised at $PgData"
        return
    }
    $pwfile = Join-Path $env:TEMP "ppm-pg-pwfile-$PID.txt"
    Set-Content -Path $pwfile -Value $SuperPassword -NoNewline
    try {
        & (Get-PgExe 'initdb') -D $PgData -U $SuperUser -A scram-sha-256 --pwfile=$pwfile -E UTF8 --locale=C
        if ($LASTEXITCODE -ne 0) { throw 'initdb failed' }
    } finally {
        Remove-Item -Force $pwfile -ErrorAction SilentlyContinue
    }
    Add-Content -Path (Join-Path $PgData 'postgresql.conf') -Value @"

# --- PPM dev settings ---
port = $Port
listen_addresses = '127.0.0.1'
"@
}

function Start-Cluster {
    & (Get-PgExe 'pg_ctl') -D $PgData status *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Host 'postgres already running'
        return
    }
    & (Get-PgExe 'pg_ctl') -D $PgData -l $PgLog -w start
    if ($LASTEXITCODE -ne 0) { throw "pg_ctl start failed - see $PgLog" }
}

function Stop-Cluster {
    & (Get-PgExe 'pg_ctl') -D $PgData status *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'postgres not running'
        return
    }
    & (Get-PgExe 'pg_ctl') -D $PgData stop -m fast -w
}

function Ensure-Databases {
    $env:PGPASSWORD = $SuperPassword
    try {
        foreach ($db in $Databases) {
            $exists = & (Get-PgExe 'psql') -h 127.0.0.1 -p $Port -U $SuperUser -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$db'"
            if ($exists -ne '1') {
                & (Get-PgExe 'createdb') -h 127.0.0.1 -p $Port -U $SuperUser $db
            }
            & (Get-PgExe 'psql') -h 127.0.0.1 -p $Port -U $SuperUser -d $db -q -c 'CREATE EXTENSION IF NOT EXISTS vector'
            if ($LASTEXITCODE -ne 0) { throw "CREATE EXTENSION vector failed on $db" }
            Write-Host "database ready: $db"
        }
    } finally {
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    }
}

switch ($Action) {
    'init' {
        New-Item -ItemType Directory -Force -Path $Downloads | Out-Null
        Install-Binaries
        Initialize-Cluster
        Start-Cluster
        Ensure-Databases
        Write-Host ''
        Write-Host "postgres running on 127.0.0.1:$Port"
        Write-Host "dsn: postgresql://$SuperUser`:$SuperPassword@127.0.0.1:$Port/ppm"
    }
    'start' { Start-Cluster }
    'stop' { Stop-Cluster }
    'status' {
        & (Get-PgExe 'pg_ctl') -D $PgData status
        $ready = & (Get-PgExe 'pg_isready') -h 127.0.0.1 -p $Port
        Write-Host $ready
    }
    'psql' {
        $env:PGPASSWORD = $SuperPassword
        try { & (Get-PgExe 'psql') -h 127.0.0.1 -p $Port -U $SuperUser -d ppm } finally { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
    }
}
