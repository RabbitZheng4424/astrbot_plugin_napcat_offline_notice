# Changelog

All notable changes to this project will be documented in this file.

## 0.1.0

Initial available release.

### Added

- Added background monitoring for `aiocqhttp / OneBot v11` connection state
- Added offline notification when NapCat disconnects
- Added optional recovery notification when NapCat reconnects
- Added target session binding via `/napcat_notice bind`
- Added target session unbinding, listing, status check, and test commands
- Added cooldown control to prevent repeated duplicate notifications
- Added LLM-based notification text generation using the target session's current provider and persona when available
- Added fallback text templates when LLM generation is unavailable
- Added GitHub-ready README documentation
