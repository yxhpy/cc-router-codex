param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $RemainingArgs
)

& claude --tools "Bash" --disable-slash-commands --strict-mcp-config --effort low @RemainingArgs
exit $LASTEXITCODE
