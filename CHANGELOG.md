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

### Fixed

- Fixed target platform hint detection by resolving real platform adapter type from the bound UMO platform ID instead of assuming the first UMO segment is always the adapter name
- Fixed offline notification handling for targets bound to the same `aiocqhttp / OneBot v11` platform instance being monitored; the plugin now skips these impossible sends and writes a clearer warning
