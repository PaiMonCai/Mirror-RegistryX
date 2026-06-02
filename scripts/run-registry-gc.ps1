param(
    [string]$EnvFile = ".env",
    [string]$ComposeFile = "docker-compose.yml",
    [string]$PanelUrl = "http://localhost:8080",
    [string]$AdminUsername = "",
    [string]$AdminPassword = "",
    [string]$RegistryService = "registry",
    [string]$SyncService = "sync",
    [string]$RegistryConfigPath = "/etc/docker/registry/config.yml",
    [switch]$Force,
    [switch]$SkipSyncService
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$script:GcLog = [System.Collections.Generic.List[string]]::new()

function Add-LogLine {
    param([string]$Line)
    $script:GcLog.Add($Line)
    Write-Host $Line
}

function Get-LogTail {
    param([int]$MaxChars = 18000)
    $text = ($script:GcLog -join "`n")
    if ($text.Length -le $MaxChars) {
        return $text
    }
    return $text.Substring($text.Length - $MaxChars)
}

function Read-DotEnv {
    param([string]$Path)
    $values = @{}
    if (-not (Test-Path -LiteralPath $Path)) {
        return $values
    }

    foreach ($rawLine in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $line = $rawLine.Trim()
        if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith("#")) {
            continue
        }
        $index = $line.IndexOf("=")
        if ($index -lt 1) {
            continue
        }
        $key = $line.Substring(0, $index).Trim()
        $value = $line.Substring($index + 1).Trim()
        if ($value.Length -ge 2) {
            if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        $values[$key] = $value
    }
    return $values
}

function Get-ConfigValue {
    param(
        [hashtable]$Values,
        [string]$Name,
        [string]$Default = ""
    )
    if ($Values.ContainsKey($Name)) {
        return [string]$Values[$Name]
    }
    return $Default
}

function Join-Url {
    param(
        [string]$BaseUrl,
        [string]$Path
    )
    return $BaseUrl.TrimEnd("/") + "/" + $Path.TrimStart("/")
}

function Invoke-PanelJson {
    param(
        [string]$Method,
        [string]$Path,
        [object]$Body = $null,
        [Microsoft.PowerShell.Commands.WebRequestSession]$WebSession = $null
    )
    $uri = Join-Url -BaseUrl $PanelUrl -Path ("api" + $Path)
    $params = @{
        Uri = $uri
        Method = $Method
        TimeoutSec = 30
        ErrorAction = "Stop"
    }
    if ($null -ne $WebSession) {
        $params.WebSession = $WebSession
    }
    if ($null -ne $Body) {
        $params.ContentType = "application/json"
        $params.Body = ($Body | ConvertTo-Json -Depth 20 -Compress)
    }
    try {
        return Invoke-RestMethod @params
    } catch {
        throw "Panel API $Method $Path failed: $($_.Exception.Message)"
    }
}

function Invoke-CheckedCommand {
    param(
        [string]$FilePath,
        [string[]]$Arguments
    )
    Add-LogLine (">> {0} {1}" -f $FilePath, ($Arguments -join " "))
    $output = & $FilePath @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    foreach ($line in $output) {
        Add-LogLine ([string]$line)
    }
    if ($exitCode -ne 0) {
        throw ("Command failed with exit code {0}: {1} {2}" -f $exitCode, $FilePath, ($Arguments -join " "))
    }
}

function Update-GcStatus {
    param(
        [string]$Status,
        [string]$RequestId,
        [string]$Message
    )
    Invoke-PanelJson `
        -Method "POST" `
        -Path "/storage/gc/status" `
        -WebSession $script:PanelSession `
        -Body @{
            status = $Status
            request_id = $RequestId
            message = $Message
            log_tail = Get-LogTail
        } | Out-Null
}

function Start-RegistryServices {
    $services = @($RegistryService)
    if (-not $SkipSyncService) {
        $services += $SyncService
    }
    Invoke-CheckedCommand -FilePath "docker" -Arguments (@("compose", "-f", $ComposeFile, "up", "-d") + $services)
}

$envValues = Read-DotEnv -Path $EnvFile
if ($PanelUrl -eq "http://localhost:8080") {
    $PanelUrl = Get-ConfigValue -Values $envValues -Name "PANEL_URL" -Default $PanelUrl
}
if ($ComposeFile -eq "docker-compose.yml") {
    $ComposeFile = Get-ConfigValue -Values $envValues -Name "COMPOSE_FILE" -Default $ComposeFile
}
if ([string]::IsNullOrWhiteSpace($AdminUsername)) {
    $AdminUsername = Get-ConfigValue -Values $envValues -Name "ADMIN_USERNAME" -Default "admin"
}
if ([string]::IsNullOrWhiteSpace($AdminPassword)) {
    $AdminPassword = Get-ConfigValue -Values $envValues -Name "ADMIN_PASSWORD"
}
if ([string]::IsNullOrWhiteSpace($AdminPassword)) {
    throw "ADMIN_PASSWORD is required. Set it in .env or pass -AdminPassword."
}

$script:PanelSession = New-Object Microsoft.PowerShell.Commands.WebRequestSession
Invoke-PanelJson -Method "POST" -Path "/auth/login" -WebSession $script:PanelSession -Body @{
    username = $AdminUsername
    password = $AdminPassword
} | Out-Null

$gc = Invoke-PanelJson -Method "GET" -Path "/storage/gc/status" -WebSession $script:PanelSession
if ($gc.status -eq "running") {
    Add-LogLine "GC request $($gc.request_id) is already running. Exiting."
    exit 0
}
if ($gc.status -ne "requested") {
    if (-not $Force) {
        Add-LogLine "No pending GC request. Use -Force to create and run one from the host."
        exit 0
    }
    $created = Invoke-PanelJson -Method "POST" -Path "/storage/gc/request" -WebSession $script:PanelSession -Body @{}
    $gc = $created.request
}

$requestId = [string]$gc.request_id
if ([string]::IsNullOrWhiteSpace($requestId)) {
    throw "GC request id is empty."
}

try {
    Update-GcStatus -Status "running" -RequestId $requestId -Message "Registry garbage-collect is running on the host."

    if (-not $SkipSyncService) {
        Invoke-CheckedCommand -FilePath "docker" -Arguments @("compose", "-f", $ComposeFile, "stop", $SyncService)
    }
    Invoke-CheckedCommand -FilePath "docker" -Arguments @("compose", "-f", $ComposeFile, "stop", $RegistryService)
    Invoke-CheckedCommand -FilePath "docker" -Arguments @("compose", "-f", $ComposeFile, "run", "--rm", $RegistryService, "registry", "garbage-collect", $RegistryConfigPath)
    Start-RegistryServices

    $services = @($RegistryService)
    if (-not $SkipSyncService) {
        $services += $SyncService
    }
    Invoke-CheckedCommand -FilePath "docker" -Arguments (@("compose", "-f", $ComposeFile, "ps") + $services)
    Update-GcStatus -Status "completed" -RequestId $requestId -Message "Registry garbage-collect completed."
    Add-LogLine "GC request $requestId completed."
} catch {
    $message = $_.Exception.Message
    Add-LogLine "GC request $requestId failed: $message"
    try {
        Add-LogLine "Attempting to restart registry services after failure."
        Start-RegistryServices
    } catch {
        Add-LogLine "Service restart after failure also failed: $($_.Exception.Message)"
    }
    Update-GcStatus -Status "failed" -RequestId $requestId -Message $message
    throw
}
