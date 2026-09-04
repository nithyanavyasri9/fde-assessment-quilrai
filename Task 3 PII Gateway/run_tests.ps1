# Task 3 - LLM PII Redaction Gateway - Live Streaming Test
# Run order:
#   Terminal 1: python mock_llm_server.py
#   Terminal 2: python gateway.py
#   Terminal 3: powershell -ExecutionPolicy Bypass -File .\run_tests.ps1

Write-Host ""
Write-Host "=== Task 3 PII Redaction Gateway - Live Stream Test ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Sending request and collecting streamed response..." -ForegroundColor Yellow
Write-Host ""

$body = '{"model":"gpt-4","messages":[{"role":"user","content":"Give me customer details"}]}'
$Uri = "http://localhost:9200/v1/chat/completions"

$fullText = ""
$Results = @()

try {
    $Request = [System.Net.WebRequest]::Create($Uri)
    $Request.Method = "POST"
    $Request.ContentType = "application/json"
    $Request.Timeout = 30000

    $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($body)
    $Request.ContentLength = $bodyBytes.Length
    $stream = $Request.GetRequestStream()
    $stream.Write($bodyBytes, 0, $bodyBytes.Length)
    $stream.Close()

    $Response = $Request.GetResponse()
    $Reader = New-Object System.IO.StreamReader($Response.GetResponseStream())

    Write-Host "Streamed chunks received:" -ForegroundColor Yellow
    while (-not $Reader.EndOfStream) {
        $line = $Reader.ReadLine()
        if ($line -match "^data: " -and $line -ne "data: [DONE]") {
            try {
                $json = ($line -replace "^data: ","") | ConvertFrom-Json
                $chunk = $json.choices[0].delta.content
                if ($chunk) {
                    $fullText += $chunk
                    Write-Host "  chunk: $chunk" -ForegroundColor DarkGray
                }
            } catch {}
        }
    }
    $Reader.Close()

    Write-Host ""
    Write-Host "Full reassembled response:" -ForegroundColor Yellow
    Write-Host $fullText
    Write-Host ""

    # Check redactions
    Write-Host "Redaction checks:" -ForegroundColor Yellow
    $checks = @(
        @{ Label="Email redacted";       Pass=($fullText -match "\[REDACTED-EMAIL\]") },
        @{ Label="SSN redacted";         Pass=($fullText -match "\[REDACTED-SSN\]") },
        @{ Label="Credit card redacted"; Pass=($fullText -match "\[REDACTED-CC\]") },
        @{ Label="Phone redacted";       Pass=($fullText -match "\[REDACTED-PHONE\]") },
        @{ Label="No raw emails leaked"; Pass=($fullText -notmatch "[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}") },
        @{ Label="No raw SSNs leaked";   Pass=($fullText -notmatch "\d{3}-\d{2}-\d{4}") },
        @{ Label="No raw card numbers";  Pass=($fullText -notmatch "4111") }
    )

    foreach ($check in $checks) {
        if ($check.Pass) {
            Write-Host "  [PASS] $($check.Label)" -ForegroundColor Green
            $Results += "PASS"
        } else {
            Write-Host "  [FAIL] $($check.Label)" -ForegroundColor Red
            $Results += "FAIL"
        }
    }

} catch {
    Write-Host "  [ERROR] Could not connect to gateway: $_" -ForegroundColor Red
    Write-Host "  Make sure mock_llm_server.py and gateway.py are both running." -ForegroundColor Yellow
}

$Passed = ($Results | Where-Object { $_ -eq "PASS" }).Count
Write-Host ""
Write-Host "  $Passed/$($Results.Count) checks passed" -ForegroundColor Cyan
if ($Passed -eq $Results.Count) {
    Write-Host "  Live stream PII redaction verified!" -ForegroundColor Green
}
Write-Host ""
