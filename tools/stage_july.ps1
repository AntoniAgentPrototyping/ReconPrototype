# Stage the July 2026 raw dump (Downloads\recon-data-july) into per-window
# input folders. Copies only — the extracted dump stays untouched.
$ErrorActionPreference = "Stop"
$SRC = "C:\Users\AbdulAshraff\Downloads\recon-data-july\JULY"
$IN  = "C:\Users\AbdulAshraff\Downloads\recon\input"

function Stage-Files($files, $dest) {
    New-Item -ItemType Directory -Force "$dest\orders" | Out-Null
    New-Item -ItemType Directory -Force "$dest\income" | Out-Null
    $no = 0; $ni = 0
    foreach ($f in $files) {
        if ($f.Name -match 'rder') { Copy-Item $f.FullName "$dest\orders\"; $no++ }
        elseif ($f.Name -match 'ncome') { Copy-Item $f.FullName "$dest\income\"; $ni++ }
        else { Write-Output "  UNROUTED: $($f.FullName)" }
    }
    Write-Output ("staged {0}: {1} orders, {2} income" -f $dest.Split('\')[-2..-1][0], $no, $ni)
}

# TikTok weekly windows
$tt = @{
    "2026-07_w1" = "Tiktok\Tiktok 2026_01 to 07 July";
    "2026-07_w2" = "Tiktok\Tiktok 2026_08 to 14 July";
    "2026-07_w3" = "Tiktok\Tiktok 2026_15 to 21 July";
    "2026-07_w4" = "Tiktok\Tiktok 2026_22 to 28 July";
    "2026-07_w5" = "Tiktok\26_29 to 31T07 ALL";
}
foreach ($k in $tt.Keys | Sort-Object) {
    $files = Get-ChildItem "$SRC\$($tt[$k])" -Recurse -Filter *.xlsx
    Stage-Files $files "$IN\$k\tiktok"
}

# Shopee windows (s1 = Masan zip + No MASAN zip merged: same settlement window)
$sp = @{
    "2026-07_s1" = @("SHopee\Shopee 26_ 01 to 10T07 - Masan", "SHopee\Shopee 26_ 01 to 10T07 _No MASAN");
    "2026-07_s2" = @("SHopee\Shopee 26_ 11 to 20T07");
    "2026-07_s3" = @("SHopee\Shopee 26_ 21 to 28T07");
    "2026-07_s4" = @("SHopee\Shopee 26_ 29 to 31T07");
}
foreach ($k in $sp.Keys | Sort-Object) {
    $files = @()
    foreach ($d in $sp[$k]) { $files += Get-ChildItem "$SRC\$d" -Recurse -Filter *.xlsx }
    Stage-Files $files "$IN\$k\shopee"
}
Write-Output "STAGING DONE"
