<#
.SYNOPSIS
    Open VS Code for a PR's source branch using git worktrees.
.DESCRIPTION
    Reads a JSON file (passed as $args[0]) containing PR data, finds or creates
    a git worktree for the source branch, and opens VS Code there.
#>

param()

# --- Read and parse input -----------------------------------------------
$jsonPath = $args[0]
if (!$jsonPath -or !(Test-Path $jsonPath)) {
    Write-Host "Error: JSON file not found: $jsonPath" -ForegroundColor Red
    exit 1
}

$pr = Get-Content $jsonPath -Raw | ConvertFrom-Json
$sourceBranch = $pr.sourceBranch -replace '^refs/heads/', ''
$repoName = $pr.repoName

if (!$sourceBranch -or !$repoName) {
    Write-Host "Error: JSON must contain sourceBranch and repoName" -ForegroundColor Red
    exit 1
}

Write-Host "Looking for branch '$sourceBranch' in repo '$repoName'..." -ForegroundColor Cyan

# --- Helpers ------------------------------------------------------------
$reposRoot = 'C:\repos'
if (!(Test-Path $reposRoot)) {
    Write-Host "Error: Repos root not found: $reposRoot" -ForegroundColor Red
    exit 1
}

function Parse-Worktrees([string]$repoPath) {
    $wtRaw = git -C $repoPath worktree list --porcelain 2>$null
    if (!$wtRaw) { return @() }

    $entries = @()
    $current = @{}
    foreach ($line in $wtRaw) {
        if ($line -match '^worktree (.+)') {
            if ($current.Count) { $entries += [PSCustomObject]$current }
            $current = @{ Path = $Matches[1] -replace '/', '\' }
        } elseif ($line -match '^branch refs/heads/(.+)') {
            $current.Branch = $Matches[1]
        } elseif ($line -match '^detached') {
            $current.Branch = '(detached)'
        }
    }
    if ($current.Count) { $entries += [PSCustomObject]$current }
    return $entries
}

# --- Scan repos for existing worktree with matching branch ---------------
$matchedWorktree = $null
$matchedRepo = $null

foreach ($dir in Get-ChildItem $reposRoot -Directory) {
    $gitPath = Join-Path $dir.FullName '.git'
    # Skip worktree checkouts (.git is a file, not a directory)
    if (!(Test-Path $gitPath -PathType Container)) { continue }

    $entries = Parse-Worktrees $dir.FullName
    foreach ($e in $entries) {
        if ($e.Branch -eq $sourceBranch) {
            $matchedWorktree = $e.Path
            $matchedRepo = $dir.FullName
            break
        }
    }
    if ($matchedWorktree) { break }
}

# --- If worktree found, open it -----------------------------------------
if ($matchedWorktree) {
    Write-Host "Opened existing worktree: $matchedWorktree" -ForegroundColor Green
    code $matchedWorktree
    exit 0
}

# --- No existing worktree — find the repo and create one -----------------
Write-Host "No worktree found for '$sourceBranch'. Searching for repo..." -ForegroundColor Yellow

# Try to find the repo by name first
$targetRepo = $null
$repoDir = Join-Path $reposRoot $repoName
$repoGit = Join-Path $repoDir '.git'
if ((Test-Path $repoDir) -and (Test-Path $repoGit -PathType Container)) {
    $targetRepo = $repoDir
    Write-Host "Matched repo by name: $repoName" -ForegroundColor Cyan
}

# If not found by name, scan all repos for one that has the branch remotely
if (!$targetRepo) {
    foreach ($dir in Get-ChildItem $reposRoot -Directory) {
        $gitPath = Join-Path $dir.FullName '.git'
        if (!(Test-Path $gitPath -PathType Container)) { continue }

        $remoteRef = git -C $dir.FullName ls-remote --heads origin $sourceBranch 2>$null
        if ($remoteRef) {
            $targetRepo = $dir.FullName
            Write-Host "Found branch on remote in repo: $($dir.Name)" -ForegroundColor Cyan
            break
        }
    }
}

if (!$targetRepo) {
    Write-Host "Error: No repo found matching '$repoName' or containing branch '$sourceBranch'" -ForegroundColor Red
    exit 1
}

# Fetch latest from origin
Write-Host "Fetching origin (this may take a moment)..." -ForegroundColor Cyan
git -C $targetRepo fetch origin 2>&1 | Out-Null

# Build worktree path: <repo>\.worktrees\<branch-with-dashes>
$safeBranch = $sourceBranch -replace '/', '-'
$worktreesDir = Join-Path $targetRepo '.worktrees'
$newPath = Join-Path $worktreesDir $safeBranch

if (!(Test-Path $worktreesDir)) {
    New-Item -ItemType Directory -Path $worktreesDir -Force | Out-Null
}

# Try existing local branch first, fall back to creating from remote
$wtOutput = git -C $targetRepo worktree add $newPath $sourceBranch 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "Created worktree from existing local branch: $newPath" -ForegroundColor Green
    code $newPath
    exit 0
}

$wtOutput = git -C $targetRepo worktree add $newPath "origin/$sourceBranch" -b $sourceBranch 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "Created new branch + worktree from remote: $newPath" -ForegroundColor Green
    code $newPath
    exit 0
}

Write-Host "Error creating worktree: $wtOutput" -ForegroundColor Red
exit 1
