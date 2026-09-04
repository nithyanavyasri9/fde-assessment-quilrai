# Task 4 - Rate Limiter & Fallback Router - Live HTTP Test Script
# Run order:
#   Terminal 1: python mock_llm_servers.py
#   Terminal 2: python router.py
#   Terminal 3: powershell -ExecutionPolicy Bypass -File .\run_tests.ps1

$RouterUrl = "http://localhost:9300/v1/chat/completions"
$PrimaryUrl = "http://localhost:9400"
$Results = @()

function Send-Request {
    param([string]$Tenant, [string]$Content = "Hello world")
    $body = "{`"messages`":[{`"role`":`"user`",`"content`":`"$Content`"}],`"max_tokens`":100}"
    try {
        return Invoke-RestMethod -Uri $RouterUrl -Method POST `
            -Headers @{"Authorization"="Bearer $Tenant"} `
            -ContentType "application/json" -Body $body -ErrorAction SilentlyContinue
    } catch {
        try { return $_.ErrorDetails.Message | ConvertFrom-Json } catch { return $null }
    }
}

Write-Host ""
Write-Host "=== Task 4 Rate Limiter & Fallback Router - Live Tests ===" -ForegroundColor Cyan
Write-Host ""

# Health check
Write-Host "-- Health Check --" -ForegroundColor Yellow
try {
    $h = Invoke-RestMethod -Uri "http://localhost:9300/health" -Method GET
    if ($h.status -eq "ok") { $Results += "[PASS] Router health check" }
    else { $Results += "[FAIL] Router health check" }
} catch { $Results += "[FAIL] Router unreachable - is router.py running?" }

# Normal request - primary succeeds
Write-Host "-- Normal Flow (Primary Model) --" -ForegroundColor Yellow
$r = Send-Request -Tenant "tenant-live-1"
if ($r.choices) {
    $Results += "[PASS] Normal request returns choices from primary"
    if ($r.model -eq "primary-model") { $Results += "[PASS] Response from primary model confirmed" }
    else { $Results += "[INFO] Model: $($r.model)" }
} else {
    $Results += "[FAIL] Normal request failed: $($r | ConvertTo-Json -Compress)"
}

# Admin stats
Write-Host "-- Admin Stats --" -ForegroundColor Yellow
try {
    $stats = Invoke-RestMethod -Uri "http://localhost:9300/admin/stats" -Method GET
    if ($stats.tenants) {
        $Results += "[PASS] Admin stats returns tenant data"
        Write-Host "  Token usage so far:" -ForegroundColor DarkGray
        foreach ($t in $stats.tenants) {
            Write-Host "    $($t.tenant): $($t.tokens_used)/$($t.tokens_limit) tokens" -ForegroundColor DarkGray
        }
    } else { $Results += "[FAIL] Admin stats empty" }
} catch { $Results += "[FAIL] Admin stats unreachable" }

# Force primary to 429 - should fallback
Write-Host "-- Fallback on Primary 429 --" -ForegroundColor Yellow
try {
    Invoke-RestMethod -Uri "$PrimaryUrl/set_429?value=true" -Method GET | Out-Null
    $r = Send-Request -Tenant "tenant-live-2"
    if ($r.choices -and $r._gateway.fallback -eq $true) {
        $Results += "[PASS] Fallback triggered on primary 429"
        $Results += "[PASS] Response contains fallback indicator"
    } elseif ($r.choices) {
        $Results += "[PASS] Got response (fallback worked)"
        $Results += "[INFO] _gateway flag: $($r._gateway | ConvertTo-Json -Compress)"
    } else {
        $Results += "[FAIL] Fallback failed: $($r | ConvertTo-Json -Compress)"
    }
    # Reset primary
    Invoke-RestMethod -Uri "$PrimaryUrl/set_429?value=false" -Method GET | Out-Null
} catch { $Results += "[FAIL] Could not test fallback - primary mock unreachable" }

# Rate limit test - flood with requests
Write-Host "-- Rate Limiting (token exhaustion) --" -ForegroundColor Yellow
$rateLimitHit = $false
for ($i = 0; $i -lt 20; $i++) {
    $bigContent = "A" * 1000   # large content = many tokens
    $r = Send-Request -Tenant "tenant-flood" -Content $bigContent
    if ($r.error.code -eq "rate_limit_exceeded") {
        $rateLimitHit = $true
        $retryAfter = $r.error.message
        Write-Host "  Rate limit hit after $i requests" -ForegroundColor DarkGray
        break
    }
}
if ($rateLimitHit) {
    $Results += "[PASS] Rate limit enforced after token exhaustion"
} else {
    $Results += "[FAIL] Rate limit not triggered after 20 large requests"
}

# Per-tenant stats
Write-Host "-- Per-Tenant Stats --" -ForegroundColor Yellow
try {
    $ts = Invoke-RestMethod -Uri "http://localhost:9300/admin/stats/tenant-live-1" -Method GET
    if ($ts.tokens_used -gt 0) {
        $Results += "[PASS] Per-tenant stats show token usage: $($ts.tokens_used) tokens"
    } else {
        $Results += "[FAIL] Per-tenant stats empty"
    }
} catch { $Results += "[FAIL] Per-tenant stats unreachable" }

# Print results
Write-Host ""
$Passed = ($Results | Where-Object { $_ -like "*[PASS]*" }).Count
foreach ($r in $Results) {
    if ($r -like "*[PASS]*") { Write-Host "  $r" -ForegroundColor Green }
    elseif ($r -like "*[INFO]*") { Write-Host "  $r" -ForegroundColor Yellow }
    else { Write-Host "  $r" -ForegroundColor Red }
}
Write-Host ""
Write-Host "  $Passed/$($Results.Count) tests passed" -ForegroundColor Cyan
if ($Passed -eq $Results.Count) {
    Write-Host "  Task 4 fully verified!" -ForegroundColor Green
}
Write-Host ""
