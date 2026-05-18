@echo off
setlocal
claude --tools "Bash" --disable-slash-commands --strict-mcp-config --effort low %*
exit /b %ERRORLEVEL%
