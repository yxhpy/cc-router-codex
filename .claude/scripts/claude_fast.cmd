@echo off
setlocal
claude --tools "Bash,Read,Write,Edit,Grep,Glob" --disable-slash-commands --strict-mcp-config --effort low %*
exit /b %ERRORLEVEL%
