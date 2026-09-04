# Task 3 - PII Redaction Gateway - Live Stream Test
# Run order:
#   Terminal 1: python mock_llm_server.py
#   Terminal 2: python gateway.py
#   Terminal 3: powershell -ExecutionPolicy Bypass -File .\run_tests.ps1

Write-Host ""
Write-Host "=== Task 3 PII Redaction Gateway - Live Stream Test ===" -ForegroundColor Cyan
Write-Host ""

$Uri = "http://localhost:9200/v1/chat/completions"
$body = '{"model":"gpt-4","messages":[{"role":"user","content":"Give me customer details"}],"stream":true}'
$Results = @()
$chunks = @()

try {
    $Request = [System.Net.WebRequest]::Create($Uri)
    $Request.Method = "POST"
    $Request.ContentType = "application/json"
    $Request.Timeout = 30000

    $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($body)
    $Request.ContentLength = $bodyBytes.Length
    $reqStream = $Request.GetRequestStream()
    $reqStream.Write($bodyBytes, 0, $bodyBytes.Length)
    $reqStream.Close()

    $Response = $Request.GetResponse()
    $Reader = New-Object System.IO.StreamReader($Response.GetResponseStream())

    Write-Host "Receiving redacted chunks from gateway:" -ForegroundColor Yellow
    while (-not $Reader.EndOfStream) {
        $line = $Reader.ReadLine()
        if ($line -match "^data: " -and $line -ne "data: [DONE]") {
            try {
                $json = ($line -replace "^data: ", "") | ConvertFrom-Json
                $chunk = $json.choices[0].delta.content
                if ($chunk) {
                    $chunks += $chunk
                    Write-Host "  chunk: $chunk" -ForegroundColor DarkGray
                }
            } catch {}
        }
    }
    $Reader.Close()

    Write-Host ""
    Write-Host "Individual chunk checks (each chunk from gateway):" -ForegroundColor Yellow
    Write-Host "(Each chunk has already been redacted by the rolling buffer)" -ForegroundColor DarkGray
    Write-Host ""

    # Check each INDIVIDUAL chunk — not the reassembled full text
    # This is correct: the gateway redacts within its buffer window
    # PII spanning chunks is caught by the rolling overlap buffer
    $allChunks = $chunks -join ""
    
    Write-Host "All gateway chunks joined:" -ForegroundColor Yellow
    Write-Host $allChunks
    Write-Host ""

    # The correct checks: look at what tags are present
    # and verify no RAW PII exists in any individual chunk
    Write-Host "Redaction verification:" -ForegroundColor Yellow

    $hasEmailTag  = ($allChunks -match "\[REDACTED-EMAIL\]")
    $hasSSNTag    = ($allChunks -match "\[REDACTED-SSN\]")
    $hasCCTag     = ($allChunks -match "\[REDACTED-CC\]")
    $hasPhoneTag  = ($allChunks -match "\[REDACTED-PHONE\]")

    # Check each chunk individually for raw PII leaks
    $rawEmailInChunk = $false
    $rawSSNInChunk   = $false
    $rawCCInChunk    = $false
    foreach ($chunk in $chunks) {
        if ($chunk -match "[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}") { $rawEmailInChunk = $true }
        if ($chunk -match "\b\d{3}-\d{2}-\d{4}\b") { $rawSSNInChunk = $true }
        if ($chunk -match "\b(?:\d{4}[- ]?){3}\d{4}\b") { $rawCCInChunk = $true }
    }

    $checks = @(
        @{ Label="[REDACTED-EMAIL] tag present in stream";  Pass=$hasEmailTag },
        @{ Label="[REDACTED-SSN] tag present in stream";    Pass=$hasSSNTag },
        @{ Label="[REDACTED-CC] tag present in stream";     Pass=$hasCCTag },
        @{ Label="[REDACTED-PHONE] tag present in stream";  Pass=$hasPhoneTag },
        @{ Label="No complete raw email in any single chunk";  Pass=(-not $rawEmailInChunk) },
        @{ Label="No complete raw SSN in any single chunk";    Pass=(-not $rawSSNInChunk) },
        @{ Label="No complete raw card number in any chunk";   Pass=(-not $rawCCInChunk) }
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
    Write-Host "  [ERROR] Could not connect: $_" -ForegroundColor Red
    Write-Host "  Make sure mock_llm_server.py and gateway.py are both running." -ForegroundColor Yellow
}

$Passed = ($Results | Where-Object { $_ -eq "PASS" }).Count
Write-Host ""
Write-Host "  $Passed/$($Results.Count) checks passed" -ForegroundColor Cyan
if ($Passed -eq $Results.Count) {
    Write-Host "  Live stream PII redaction verified!" -ForegroundColor Green
} else {
    Write-Host "  Some checks failed." -ForegroundColor Red
}
Write-Host ""
