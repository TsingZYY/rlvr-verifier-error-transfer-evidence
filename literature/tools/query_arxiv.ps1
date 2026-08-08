param(
    [string]$OutFile = "literature/arxiv_candidates.csv"
)

$ErrorActionPreference = "Stop"
$queries = @(
    'all:"reinforcement learning with verifiable rewards" OR all:RLVR',
    'all:verifier AND (all:"reward hacking" OR all:"reward gaming")',
    'all:"process reward model" AND (all:generalization OR all:robustness OR all:benchmark)',
    'all:"reward model" AND (all:overoptimization OR all:misspecification OR all:underspecification)',
    'all:"goal misgeneralization" OR all:"specification gaming"',
    'all:verifier AND (all:"cross-domain" OR all:"distribution shift" OR all:"false positive")'
)

$allEntries = @()
$queryIndex = 0
foreach ($query in $queries) {
    $queryIndex += 1
    $encoded = [System.Uri]::EscapeDataString($query)
    $uri = "https://export.arxiv.org/api/query?search_query=$encoded&start=0&max_results=100&sortBy=relevance&sortOrder=descending"
    $attempt = 0
    do {
        $attempt += 1
        try {
            [xml]$feed = (Invoke-WebRequest -UseBasicParsing -Uri $uri -TimeoutSec 60).Content
            $ok = $true
        } catch {
            $ok = $false
            if ($attempt -ge 4) { throw }
            Start-Sleep -Seconds (5 * $attempt)
        }
    } until ($ok)

    foreach ($entry in $feed.feed.entry) {
        $id = [string]$entry.id
        $arxivId = ($id -replace '^https?://arxiv.org/abs/', '') -replace 'v\d+$', ''
        $authors = (($entry.author | ForEach-Object { [string]$_.name }) -join "; ")
        $categories = (($entry.category | ForEach-Object { [string]$_.term }) -join "; ")
        $pdfLink = ($entry.link | Where-Object { $_.type -eq "application/pdf" } | Select-Object -First 1).href
        $allEntries += [pscustomobject]@{
            arxiv_id = $arxivId
            title = ([string]$entry.title -replace '\s+', ' ').Trim()
            authors = $authors
            published = [string]$entry.published
            updated = [string]$entry.updated
            categories = $categories
            abstract = ([string]$entry.summary -replace '\s+', ' ').Trim()
            abs_url = "https://arxiv.org/abs/$arxivId"
            pdf_url = if ($pdfLink) { [string]$pdfLink } else { "https://arxiv.org/pdf/$arxivId" }
            matched_query = $query
            query_rank = $allEntries.Count + 1
        }
    }
    if ($queryIndex -lt $queries.Count) { Start-Sleep -Seconds 4 }
}

$deduped = $allEntries |
    Group-Object arxiv_id |
    ForEach-Object {
        $first = $_.Group | Select-Object -First 1
        $first.matched_query = (($_.Group.matched_query | Sort-Object -Unique) -join " || ")
        $first
    } |
    Sort-Object published -Descending

$outPath = Join-Path (Get-Location) $OutFile
$outDir = Split-Path -Parent $outPath
New-Item -ItemType Directory -Path $outDir -Force | Out-Null
$deduped | Export-Csv -LiteralPath $outPath -NoTypeInformation -Encoding utf8
Write-Output "Saved $($deduped.Count) deduplicated arXiv candidates to $outPath"
