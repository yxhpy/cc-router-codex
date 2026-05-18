param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $RemainingArgs
)

& claude --tools "Bash,Read,Write,Edit,Grep,Glob" --disable-slash-commands --strict-mcp-config --effort low @RemainingArgs
exit $LASTEXITCODE
