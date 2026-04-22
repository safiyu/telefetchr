# Changelog

All epic victories and mysterious bugs that somehow became features are here.

## [1.4.0] - 2026-04-22
### Added
- UX Enhancement: Added dynamic Sorting Options (Newest, Oldest, Largest, Smallest, Alphabetical) directly above the scan results.
- UX Enhancement: Added robust Pre-scan Filters ("Videos" and "Audio") using native Telethon API filtering to significantly boost scan speeds.
- UX Enhancement: Exposed previously hidden Download Speed and ETA text directly into the progress bar UI.

## [1.3.0] - 2026-04-22
### Added
- Comprehensive Log Viewer with "Comic Panel" theme.
- Log filtering (search and level), auto-refresh, and clearing functionality.
- Automated 30-day log rotation and cleanup logic.

### Fixed
- Fixed an issue causing MP4 file corruption during parallel downloads, restoring fast-forward/seeking support in local players.
- Replaced native browser popups with custom themed modals (e.g. for clearing logs).
- Added no-cache headers to prevent stale HTML pages on navigation.
- Reduced noise in logs from Telethon and the progress endpoint.
## [1.2.5] - 2024-12-24
### Added
- New "About" dialog with version information and changelog.
- Version display in the application header.
- Proper versioning system in backend and frontend.

## [1.2.4] - 2024-12-24
### Changed
- Redesigned connection status badge in the header to match "Subtle Comic" aesthetic.
- Updated styling in `view.html` and logic in `script.js` for comic-style classes.

## [1.2.3] - 2024-12-24
### Fixed
- Subtitle display adjustments for SRT files (increased size).
- Optimized download start time by implementing CDN warmup and parallel worker scaling.
- Refined subtitle and OSD layout to prevent obstruction.

## [1.2.2] - 2024-12-24
### Fixed
- "Cancelled download session found" alert styling to match "Subtle Comic" theme.
- Button hover feedback refinements (grey to yellow for Scan/Download buttons).

## [1.2.1] - 2024-12-23
### Added
- Robust retry logic for failed downloads.
- File overwrite protection.
- Session consistency improvements across container restarts.

## [1.2.0] - 2024-12-22
### Added
- Full "Subtle Comic" theme implementation.
- Refined mobile UI for toast notifications and file list display.
- Improved media player OSD layout.
