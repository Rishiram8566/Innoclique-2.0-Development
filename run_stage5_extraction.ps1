
# Innoclique 2.0 - Stage 5 Geometry Extraction Automation

$BaseUrl = "http://localhost:8000"
$ProjectId = "a5482b08-1c59-4bf0-9eb2-7ea41eb26f9c"
$BatchSize = 5

$ApiUrl = "$BaseUrl/api/projects/$ProjectId/geometry/extract"

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host " INNOClique 2.0 - STAGE 5 EXTRACTION" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

$TotalProcessed = 0
$BatchNumber = 0

while ($true) {

    $BatchNumber++

    Write-Host "`nStarting batch $BatchNumber..." -ForegroundColor Yellow

    try {
        $Result = Invoke-RestMethod `
            -Uri "$ApiUrl`?batch_size=$BatchSize" `
            -Method POST `
            -TimeoutSec 3600

        $Summary = $Result.summary

        if ($null -eq $Summary) {
            Write-Host "Unexpected API response. Stopping safely." -ForegroundColor Red
            $Result | ConvertTo-Json -Depth 20
            break
        }

        $Processed = [int]$Summary.processed_count
        $Extracted = [int]$Summary.extracted_count
        $Remaining = [int]$Summary.remaining_count
        $Failed = [int]$Summary.failed_count

        $TotalProcessed += $Processed

        Write-Host "Batch: $BatchNumber" -ForegroundColor Cyan
        Write-Host "Processed: $Processed"
        Write-Host "Extracted: $Extracted"
        Write-Host "Remaining: $Remaining"
        Write-Host "Failed: $Failed"

        if ($Remaining -le 0) {
            Write-Host "`nAll pending drawings have been processed!" -ForegroundColor Green
            break
        }

        if ($Processed -le 0) {
            Write-Host "`nNo progress detected. Stopping to avoid an endless loop." -ForegroundColor Red
            break
        }
    }
    catch {
        Write-Host "`nAPI request failed:" -ForegroundColor Red
        Write-Host $_.Exception.Message
        Write-Host "Stopping. You can inspect the backend and run again." -ForegroundColor Yellow
        break
    }
}

Write-Host "`nTotal processed across batches: $TotalProcessed" -ForegroundColor Green
Write-Host "Stage 5 extraction automation finished." -ForegroundColor Cyan
