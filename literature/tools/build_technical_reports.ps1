param(
    [string]$Root = "literature"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Test-Pdf([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $item = Get-Item -LiteralPath $Path
    if ($item.Length -lt 10000) { return $false }
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $buffer = New-Object byte[] 5
        $read = $stream.Read($buffer, 0, 5)
        return ($read -eq 5 -and [System.Text.Encoding]::ASCII.GetString($buffer) -eq "%PDF-")
    } finally {
        $stream.Dispose()
    }
}

function Get-SafeName([string]$Text) {
    $safe = $Text -replace '[<>:"/\\|?*]', ''
    $safe = $safe -replace '[^\p{L}\p{Nd}\-_. ]', ''
    $safe = $safe -replace '\s+', '_'
    $safe = $safe.Trim("._ ")
    if ($safe.Length -gt 110) { $safe = $safe.Substring(0, 110).TrimEnd("_") }
    return $safe
}

function Invoke-PdfDownload([string]$Uri, [string]$Destination) {
    $partial = "$Destination.part"
    for ($attempt = 1; $attempt -le 4; $attempt += 1) {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $Uri -OutFile $partial -TimeoutSec 180 `
                -Headers @{"User-Agent" = "Codex technical-report archive/1.0 (research use)"}
            if (-not (Test-Pdf $partial)) {
                throw "Downloaded response is not a valid PDF"
            }
            Move-Item -LiteralPath $partial -Destination $Destination -Force
            return
        } catch {
            if (Test-Path -LiteralPath $partial) {
                Remove-Item -LiteralPath $partial -Force
            }
            if ($attempt -eq 4) { throw }
            Start-Sleep -Seconds (3 * $attempt)
        }
    }
}

$workspaceRoot = (Get-Location).Path
$rootPath = Join-Path $workspaceRoot $Root
$sourcePath = Join-Path $rootPath "technical_reports_sources.csv"
$reportDir = Join-Path $rootPath "00_industry_technical_reports"
$manifestPath = Join-Path $rootPath "technical_reports_manifest.csv"

if (-not (Test-Path -LiteralPath $sourcePath)) {
    throw "Missing source list: $sourcePath"
}
New-Item -ItemType Directory -Path $reportDir -Force | Out-Null

$sources = Import-Csv -LiteralPath $sourcePath
$results = @()
$index = 0
foreach ($source in $sources) {
    $index += 1
    if (-not [string]::IsNullOrWhiteSpace($source.reuse_local_pdf)) {
        $relativePath = $source.reuse_local_pdf
        $destination = Join-Path $workspaceRoot $relativePath
        $state = if (Test-Pdf $destination) { "reused-existing" } else { "failed" }
        $errorText = if ($state -eq "failed") { "Declared reused PDF is missing or invalid" } else { "" }
    } else {
        $company = Get-SafeName $source.company
        $title = Get-SafeName $source.title
        $filename = "$company`__$($source.year)__$title`__$($source.report_id).pdf"
        $destination = Join-Path $reportDir $filename
        $relativePath = $destination.Substring($workspaceRoot.TrimEnd('\').Length + 1)
        $state = "downloaded"
        $errorText = ""
        try {
            if (Test-Pdf $destination) {
                $state = "already-present"
            } else {
                Write-Output "[$index/$($sources.Count)] $($source.company): $($source.title)"
                Invoke-PdfDownload $source.pdf_url $destination
                Start-Sleep -Milliseconds 750
            }
        } catch {
            $state = "failed"
            $errorText = $_.Exception.Message
            Write-Output "FAILED $($source.report_id): $errorText"
        }
    }

    if (Test-Pdf $destination) {
        $item = Get-Item -LiteralPath $destination
        $size = $item.Length
        $hash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash
    } else {
        $size = 0
        $hash = ""
    }

    $results += [pscustomobject]@{
        report_id = $source.report_id
        company = $source.company
        title = $source.title
        year = $source.year
        report_type = $source.report_type
        priority = $source.priority
        evidence_role = $source.evidence_role
        landing_url = $source.landing_url
        pdf_url = $source.pdf_url
        relevance_note = $source.relevance_note
        local_pdf = $relativePath
        download_status = $state
        bytes = $size
        sha256 = $hash
        error = $errorText
    }
}

$results | Sort-Object @{Expression={[int]$_.priority}}, company, year, title |
    Export-Csv -LiteralPath $manifestPath -NoTypeInformation -Encoding utf8

$success = @($results | Where-Object { $_.download_status -ne "failed" }).Count
$failed = @($results | Where-Object { $_.download_status -eq "failed" }).Count
$bytes = ($results | Measure-Object bytes -Sum).Sum
Write-Output "Technical-report corpus complete: $success valid paths, $failed failures, $bytes bytes."
Write-Output "Manifest: $manifestPath"
