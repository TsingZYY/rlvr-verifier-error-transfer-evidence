param(
    [string]$Root = "literature"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$selection = [ordered]@{
    "01_core_verifier_error_structure" = @(
        "2607.11022","2606.01066","2605.20744","2605.12474","2604.16242",
        "2604.15149","2605.02909","2604.07666","2603.16140","2603.07084",
        "2603.06621","2602.18037","2602.01103","2601.20103","2601.11061",
        "2601.04954","2512.16912","2511.22888","2511.21654","2510.27044",
        "2510.00915","2509.15557","2509.03403","2507.08794","2505.22203",
        "2605.06523","2603.28063","2605.11134"
    )
    "02_verifiers_prms_and_benchmarks" = @(
        "2507.09884","2505.15801","2508.03686","2110.14168","2211.14275",
        "2305.20050","2312.08935","2408.15240","2412.06559","2501.03124",
        "2501.07301","2502.11250","2505.23474","2506.00027","2510.20304",
        "2511.13027","2512.03244","2601.12748","2601.12294","2602.09305",
        "2603.12963","2605.10141","2403.13787","2410.16184","2505.15034"
    )
    "03_reward_hacking_and_misspecification_foundations" = @(
        "1606.06565","1705.08417","1908.04734","2011.08827","2105.14111",
        "2201.03544","2209.13085","2210.01790","2210.10760","2312.09244",
        "2407.14503","2406.02900","2402.09345","2406.10162","2410.06491",
        "2407.13399"
    )
    "04_rlvr_generalization_context" = @(
        "2402.03300","2411.15124","2501.12948","2503.14476","2503.20783",
        "2504.13837","2512.20760"
    )
}

$direct = @(
    [pscustomobject]@{
        paper_id = "ACL-2026.eacl-short.31"
        category = "02_verifiers_prms_and_benchmarks"
        title = "Out of Distribution, Out of Luck: Process Rewards Misguide Reasoning Models"
        authors = "Alexey Dontsov; Anton Korznikov; Andrey V. Galichin; Elena Tutubalina"
        year = "2026"
        venue = "EACL 2026 Short Papers"
        status = "peer-reviewed"
        landing_url = "https://aclanthology.org/2026.eacl-short.31/"
        pdf_url = "https://aclanthology.org/2026.eacl-short.31.pdf"
    },
    [pscustomobject]@{
        paper_id = "ACL-2024.acl-long.254"
        category = "02_verifiers_prms_and_benchmarks"
        title = "A Chain-of-Thought Is as Strong as Its Weakest Link: A Benchmark for Verifiers of Reasoning Chains"
        authors = "Alon Jacovi et al."
        year = "2024"
        venue = "ACL 2024"
        status = "peer-reviewed"
        landing_url = "https://aclanthology.org/2024.acl-long.254/"
        pdf_url = "https://aclanthology.org/2024.acl-long.254.pdf"
    },
    [pscustomobject]@{
        paper_id = "ACL-2023.acl-long.262"
        category = "03_reward_hacking_and_misspecification_foundations"
        title = "Reward Gaming in Conditional Text Generation"
        authors = "Richard Yuanzhe Pang; Vishakh Padmakumar; Thibault Sellam; Ankur Parikh; He He"
        year = "2023"
        venue = "ACL 2023"
        status = "peer-reviewed"
        landing_url = "https://aclanthology.org/2023.acl-long.262/"
        pdf_url = "https://aclanthology.org/2023.acl-long.262.pdf"
    },
    [pscustomobject]@{
        paper_id = "PMLR-v235-chen24bn"
        category = "03_reward_hacking_and_misspecification_foundations"
        title = "ODIN: Disentangled Reward Mitigates Hacking in RLHF"
        authors = "Lichang Chen et al."
        year = "2024"
        venue = "ICML 2024"
        status = "peer-reviewed"
        landing_url = "https://proceedings.mlr.press/v235/chen24bn.html"
        pdf_url = "https://raw.githubusercontent.com/mlresearch/v235/main/assets/chen24bn/chen24bn.pdf"
    },
    [pscustomobject]@{
        paper_id = "PMLR-v267-zhu25f"
        category = "03_reward_hacking_and_misspecification_foundations"
        title = "When Can Proxies Improve the Sample Complexity of Preference Learning?"
        authors = "Yuchen Zhu et al."
        year = "2025"
        venue = "ICML 2025"
        status = "peer-reviewed"
        landing_url = "https://proceedings.mlr.press/v267/zhu25f.html"
        pdf_url = "https://raw.githubusercontent.com/mlresearch/v267/main/assets/zhu25f/zhu25f.pdf"
    },
    [pscustomobject]@{
        paper_id = "PMLR-v336-sriraman26a"
        category = "03_reward_hacking_and_misspecification_foundations"
        title = "Revisiting the (Sub)Optimality of Best-of-N for Inference-Time Alignment"
        authors = "Ved Sriraman; Adam Block"
        year = "2026"
        venue = "COLT 2026"
        status = "peer-reviewed"
        landing_url = "https://proceedings.mlr.press/v336/sriraman26a.html"
        pdf_url = "https://raw.githubusercontent.com/mlresearch/v336/main/assets/sriraman26a/sriraman26a.pdf"
    }
)

function Get-SafeName([string]$Text) {
    $safe = $Text -replace '[<>:"/\\|?*]', ''
    $safe = $safe -replace '[^\p{L}\p{Nd}\-_. ]', ''
    $safe = $safe -replace '\s+', '_'
    $safe = $safe.Trim("._ ")
    if ($safe.Length -gt 96) { $safe = $safe.Substring(0, 96).TrimEnd("_") }
    return $safe
}

function Test-Pdf([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $item = Get-Item -LiteralPath $Path
    if ($item.Length -lt 10000) { return $false }
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $buffer = New-Object byte[] 5
        $read = $stream.Read($buffer, 0, 5)
        if ($read -ne 5) { return $false }
        return ([System.Text.Encoding]::ASCII.GetString($buffer) -eq "%PDF-")
    } finally {
        $stream.Dispose()
    }
}

function Invoke-Download([string]$Uri, [string]$Destination) {
    $tmp = "$Destination.part"
    for ($attempt = 1; $attempt -le 5; $attempt += 1) {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $Uri -OutFile $tmp -TimeoutSec 120 `
                -Headers @{"User-Agent" = "Codex literature archival/1.0 (research use)"}
            if (-not (Test-Pdf $tmp)) { throw "Downloaded content is not a valid PDF" }
            Move-Item -LiteralPath $tmp -Destination $Destination -Force
            return
        } catch {
            if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force }
            if ($attempt -eq 5) { throw }
            Start-Sleep -Seconds (4 * $attempt)
        }
    }
}

$rootPath = Join-Path (Get-Location) $Root
New-Item -ItemType Directory -Path $rootPath -Force | Out-Null
foreach ($category in $selection.Keys) {
    New-Item -ItemType Directory -Path (Join-Path $rootPath $category) -Force | Out-Null
}

$candidatePath = Join-Path $rootPath "arxiv_candidates.csv"
$candidateRows = @()
if (Test-Path -LiteralPath $candidatePath) {
    $candidateRows = Import-Csv -LiteralPath $candidatePath
}
$candidateById = @{}
foreach ($row in $candidateRows) { $candidateById[$row.arxiv_id] = $row }

$allSelectedIds = @($selection.Values | ForEach-Object { $_ } | Sort-Object -Unique)
$missingIds = @($allSelectedIds | Where-Object { -not $candidateById.ContainsKey($_) })
for ($offset = 0; $offset -lt $missingIds.Count; $offset += 25) {
    $last = [Math]::Min($offset + 24, $missingIds.Count - 1)
    $chunk = @($missingIds[$offset..$last])
    $uri = "https://export.arxiv.org/api/query?id_list=$($chunk -join ',')&max_results=$($chunk.Count)"
    [xml]$feed = (Invoke-WebRequest -UseBasicParsing -Uri $uri -TimeoutSec 90 `
        -Headers @{"User-Agent" = "Codex literature archival/1.0 (research use)"}).Content
    foreach ($entry in $feed.feed.entry) {
        $arxivId = (([string]$entry.id) -replace '^https?://arxiv.org/abs/', '') -replace 'v\d+$', ''
        $candidateById[$arxivId] = [pscustomobject]@{
            arxiv_id = $arxivId
            title = (([string]$entry.title) -replace '\s+', ' ').Trim()
            authors = (($entry.author | ForEach-Object { [string]$_.name }) -join "; ")
            published = [string]$entry.published
            updated = [string]$entry.updated
            categories = (($entry.category | ForEach-Object { [string]$_.term }) -join "; ")
            abstract = (([string]$entry.summary) -replace '\s+', ' ').Trim()
            abs_url = "https://arxiv.org/abs/$arxivId"
            pdf_url = "https://arxiv.org/pdf/$arxivId"
        }
    }
    Start-Sleep -Seconds 4
}

$records = @()
foreach ($category in $selection.Keys) {
    foreach ($id in $selection[$category]) {
        if (-not $candidateById.ContainsKey($id)) {
            $records += [pscustomobject]@{
                paper_id = $id; category = $category; title = "[metadata unavailable]"
                authors = ""; year = ""; venue = "arXiv"; status = "metadata-failed"
                landing_url = "https://arxiv.org/abs/$id"; pdf_url = "https://arxiv.org/pdf/$id"
                abstract = ""; published = ""; updated = ""; subjects = ""
            }
            continue
        }
        $row = $candidateById[$id]
        $records += [pscustomobject]@{
            paper_id = $id
            category = $category
            title = $row.title
            authors = $row.authors
            year = ([string]$row.published).Substring(0, 4)
            venue = "arXiv"
            status = "preprint-or-author-version"
            landing_url = $row.abs_url
            pdf_url = "https://arxiv.org/pdf/$id"
            abstract = $row.abstract
            published = $row.published
            updated = $row.updated
            subjects = $row.categories
        }
    }
}
foreach ($row in $direct) {
    $records += [pscustomobject]@{
        paper_id = $row.paper_id; category = $row.category; title = $row.title
        authors = $row.authors; year = $row.year; venue = $row.venue; status = $row.status
        landing_url = $row.landing_url; pdf_url = $row.pdf_url
        abstract = ""; published = ""; updated = ""; subjects = ""
    }
}

$downloadResults = @()
$index = 0
foreach ($record in $records) {
    $index += 1
    $firstAuthor = (($record.authors -split ';')[0] -split '\s+')[-1]
    if ([string]::IsNullOrWhiteSpace($firstAuthor)) { $firstAuthor = "Unknown" }
    $shortTitle = Get-SafeName $record.title
    $safeId = Get-SafeName $record.paper_id
    $fileName = "$($record.year)__$(Get-SafeName $firstAuthor)__$shortTitle`__$safeId.pdf"
    $categoryPath = Join-Path $rootPath $record.category
    $destination = Join-Path $categoryPath $fileName
    $cwdPrefix = (Get-Location).Path.TrimEnd('\') + '\'
    if ($destination.StartsWith($cwdPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        $relativePath = $destination.Substring($cwdPrefix.Length)
    } else {
        $relativePath = $destination
    }
    $state = "downloaded"
    $errorText = ""
    try {
        if (Test-Pdf $destination) {
            $state = "already-present"
        } else {
            Write-Output "[$index/$($records.Count)] Downloading $($record.paper_id): $($record.title)"
            Invoke-Download $record.pdf_url $destination
            Start-Sleep -Milliseconds 1200
        }
        $item = Get-Item -LiteralPath $destination
        $hash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash
        $size = $item.Length
    } catch {
        $state = "failed"
        $errorText = $_.Exception.Message
        $hash = ""
        $size = 0
        Write-Output "FAILED $($record.paper_id): $errorText"
    }
    $downloadResults += [pscustomobject]@{
        paper_id = $record.paper_id
        category = $record.category
        title = $record.title
        authors = $record.authors
        year = $record.year
        venue = $record.venue
        publication_status = $record.status
        landing_url = $record.landing_url
        pdf_url = $record.pdf_url
        abstract = $record.abstract
        subjects = $record.subjects
        local_pdf = $relativePath
        download_status = $state
        bytes = $size
        sha256 = $hash
        error = $errorText
    }
}

$manifestPath = Join-Path $rootPath "corpus_manifest.csv"
$downloadResults | Sort-Object category, year, title | Export-Csv -LiteralPath $manifestPath -NoTypeInformation -Encoding utf8
$success = @($downloadResults | Where-Object { $_.download_status -ne "failed" }).Count
$failed = @($downloadResults | Where-Object { $_.download_status -eq "failed" }).Count
$bytes = ($downloadResults | Measure-Object bytes -Sum).Sum
Write-Output "Corpus complete: $success PDFs, $failed failures, $bytes bytes. Manifest: $manifestPath"
