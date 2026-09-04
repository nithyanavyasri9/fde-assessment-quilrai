# Task 2 MCP Gateway - Live HTTP Test Script
# Run order:
#   Terminal 1: python mock_mcp_server.py
#   Terminal 2: python gateway.py
#   Terminal 3: powershell -ExecutionPolicy Bypass -File .\run_tests.ps1

$GatewayUrl = "http://localhost:9000/mcp"
$Results = @()

function Test-Gateway {
    param(
        [string]$Label,
        [string]$Token,
        [string]$Body,
        [int]$ExpectedCode = 0,
        [bool]$ExpectResult = $false
    )

    $Headers = @{ "Content-Type" = "application/json" }
    if ($Token) { $Headers["Authorization"] = "Bearer $Token" }

    try {
        $Response = Invoke-RestMethod -Uri $GatewayUrl -Method POST -Headers $Headers -Body $Body -ErrorAction SilentlyContinue
    } catch {
        try { $Response = $_.ErrorDetails.Message | ConvertFrom-Json } catch { $Response = $null }
    }

    if ($ExpectResult) {
        if ($Response.result) {
            $script:Results += "[PASS] $Label"
        } else {
            $script:Results += "[FAIL] $Label - expected result, got: $($Response | ConvertTo-Json -Compress)"
        }
    } else {
        $ActualCode = $Response.error.code
        if ($ActualCode -eq $ExpectedCode) {
            $script:Results += "[PASS] $Label"
        } else {
            $script:Results += "[FAIL] $Label - expected code $ExpectedCode got $ActualCode"
        }
    }
}

Write-Host ""
Write-Host "=== Task 2 MCP Gateway Live HTTP Tests ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Auth Tests" -ForegroundColor Yellow
Test-Gateway -Label "No token rejected -32001" -Body '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' -ExpectedCode -32001
Test-Gateway -Label "Invalid token rejected -32001" -Token "bad-token" -Body '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' -ExpectedCode -32001

Write-Host "tools/list Tests" -ForegroundColor Yellow
Test-Gateway -Label "viewer tools/list forwarded" -Token "token-viewer-001" -Body '{"jsonrpc":"2.0","id":3,"method":"tools/list"}' -ExpectResult $true
Test-Gateway -Label "admin tools/list forwarded" -Token "token-admin-001" -Body '{"jsonrpc":"2.0","id":4,"method":"tools/list"}' -ExpectResult $true

Write-Host "RBAC Tests" -ForegroundColor Yellow
Test-Gateway -Label "viewer get_customer_record ALLOWED" -Token "token-viewer-001" -Body '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"get_customer_record"}}' -ExpectResult $true
Test-Gateway -Label "viewer admin_reset_key BLOCKED -32001" -Token "token-viewer-001" -Body '{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"admin_reset_key"}}' -ExpectedCode -32001
Test-Gateway -Label "viewer admin_delete_account BLOCKED -32001" -Token "token-viewer-001" -Body '{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"admin_delete_account"}}' -ExpectedCode -32001
Test-Gateway -Label "admin admin_reset_key ALLOWED" -Token "token-admin-001" -Body '{"jsonrpc":"2.0","id":8,"method":"tools/call","params":{"name":"admin_reset_key"}}' -ExpectResult $true
Test-Gateway -Label "admin admin_delete_account ALLOWED" -Token "token-admin-001" -Body '{"jsonrpc":"2.0","id":9,"method":"tools/call","params":{"name":"admin_delete_account"}}' -ExpectResult $true

Write-Host "Unknown Method Tests" -ForegroundColor Yellow
Test-Gateway -Label "unknown method returns -32601" -Token "token-admin-001" -Body '{"jsonrpc":"2.0","id":10,"method":"some/unknown"}' -ExpectedCode -32601

Write-Host "Health Check" -ForegroundColor Yellow
try {
    $h = Invoke-RestMethod -Uri "http://localhost:9000/health" -Method GET
    if ($h.status -eq "ok") { $Results += "[PASS] GET /health returns ok" }
    else { $Results += "[FAIL] GET /health unexpected response" }
} catch {
    $Results += "[FAIL] GET /health unreachable"
}

Write-Host ""
$Passed = ($Results | Where-Object { $_ -like "*[PASS]*" }).Count
foreach ($r in $Results) {
    if ($r -like "*[PASS]*") { Write-Host "  $r" -ForegroundColor Green }
    else { Write-Host "  $r" -ForegroundColor Red }
}
Write-Host ""
Write-Host "  $Passed/$($Results.Count) tests passed" -ForegroundColor Cyan
if ($Passed -eq $Results.Count) {
    Write-Host "  All tests passed! Task 2 fully verified." -ForegroundColor Green
} else {
    Write-Host "  Some tests failed." -ForegroundColor Red
}
Write-Host ""