Set-StrictMode -Version Latest

function Resolve-RepositoryCodeAuthority {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string]$InvokedRepositoryRoot,
        [string]$ComputerName = $env:COMPUTERNAME,
        [string]$LaptopRepositoryRoot = "D:\TradingCodes\quant-research-workbench",
        [string]$WorkstationComputerName = "DESKTOP-SAAI85T",
        [string]$WorkstationRepositoryRoot = "D:\TradingML\codes\quant-research-workbench"
    )

    $invokedRoot = [IO.Path]::GetFullPath($InvokedRepositoryRoot).TrimEnd('\')
    $authorityKind = "invoked checkout"
    $authorityRoot = $invokedRoot

    if ($ComputerName.Trim().Equals($WorkstationComputerName, [StringComparison]::OrdinalIgnoreCase)) {
        $authorityKind = "workstation deployment"
        $authorityRoot = [IO.Path]::GetFullPath($WorkstationRepositoryRoot).TrimEnd('\')
    }
    elseif (Test-Path -LiteralPath $LaptopRepositoryRoot -PathType Container) {
        $authorityKind = "laptop source"
        $authorityRoot = [IO.Path]::GetFullPath($LaptopRepositoryRoot).TrimEnd('\')
    }

    if (-not (Test-Path -LiteralPath $authorityRoot -PathType Container)) {
        throw (
            "The {0} repository authority is unavailable: {1}. " +
            "Do not start services from a fallback checkout; deploy the committed laptop source first."
        ) -f $authorityKind, $authorityRoot
    }

    return [pscustomobject]@{
        Kind = $authorityKind
        Root = $authorityRoot
        InvokedRoot = $invokedRoot
        Redirected = -not $authorityRoot.Equals($invokedRoot, [StringComparison]::OrdinalIgnoreCase)
    }
}
