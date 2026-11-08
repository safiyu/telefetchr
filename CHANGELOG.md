# Changelog

All epic victories and mysterious bugs that somehow became features are here.

## [4.2.3] - 2026-11-08
### Changed
- Compact Download Activity Items: Shortened "Auto Download" badge tag to "Auto" and reduced font size across all tags (channel, source, subfolder, status) to `text-[9px]`.
- Reduced Vertical Space: Streamlined activity row padding, title truncation, compact action buttons, and sleek 6px progress bar to maximize visible downloads per screen.

## [4.2.2] - 2026-11-08
### Fixed
- Fixed Viewport Height: Locked desktop view to exact viewport height (`lg:h-screen lg:overflow-hidden`) with zero window scrolling.
- Independent Custom Scrollbars: Configured dedicated internal scrollbars for both the Downloads panel (`#activityContainer`) and the full-width Scanned Files container, keeping layout intact.

## [4.2.1] - 2026-11-08
### Changed
- Dashboard Layout: Placed Channel Selection & Scan Card side-by-side with Downloads Card at equal height.
- Downloads Header: Simplified title to "Downloads".
- Container Scrolling: Made Downloads list and full-width Scanned Files list internally scrollable.

## [4.2.0] - 2026-11-08
### Added
- Responsive 2-Column Split Workspace: Desktop views feature a dedicated 7-column Explorer & Search pane alongside a sticky 5-column live Transfers & Queue pane (`lg:sticky lg:top-20`), eliminating scroll-hunting during active scans and downloads. Seamlessly folds into a single-column layout on mobile devices with touch targets and full responsiveness.
- Cyber-Comic 2.0 Aesthetic: Refined Neo-Brutalist design tokens (`--bg-canvas`, `--bg-card`, `--bg-subcard`, `--accent-gold`, `--accent-cyan`, `--ink`) with tactile 2px ink borders, 4px hard offset shadows, JetBrains Mono font integration, and custom checkbox and badge elements.
- Login Gateway Redesign: Styled login page to match the Cyber-Comic 2.0 terminal window theme with operator inputs and security badges.

## [4.1.4] - 2026-11-08
### Changed
- Renamed "Watch Rules" to "Auto Download": Updated all UI terminology, navigation buttons, view headers, metrics, modal dialogs, and activity feed chips to "Auto Download" for clearer, intuitive workflow understanding.

### Fixed
- Tab Selection Highlight: Unified tab active and inactive highlighting behavior so "Search & Download" and "Auto Download" tabs have identical, prominent selection styling. Added Tailwind 2.2.19 CSS fallbacks for amber palette utilities.

## [4.1.3] - 2026-11-08
### Added
- Dedicated Watch Rules Screen: Replaced crammed modal with a first-class screen featuring live listener status, metrics (active rules, auto-ingested files), and rich rule cards with channel avatars, status toggles, criteria tags, and direct actions.
- Full Watch Rule Editing: Added edit dialog support to modify criteria, target subfolder, and organization on existing rules via `PUT /api/watch-rules/{rule_id}`.
- Primary View Navigation Bar: Integrated a top-level segmented view switcher between "Search & Download" and "Watch Rules", plus a prominent "Quick Fetch" ingestion button.
- Real-Time Watcher Beacon: Added live pulsing watcher badge in header displaying active rule count with one-click navigation to watch rules.
- Safe User Account Menu: Isolated "Delete Session" and "Logout" into an elegant dropdown menu to prevent accidental session termination.
- Dynamic Live Destination Previews: Added real-time monospace path breadcrumb previews (`/downloads/Subfolder/media_type/filename.ext`) across Search, Quick Fetch, and Watch Rules.
- Media Server Webhook Diagnostics: Added `GET /api/webhook/status` and `POST /api/webhook/test` endpoints with in-app test trigger and status indicator for Jellyfin, Plex, Sonarr/Radarr, and Ntfy integrations.
- Activity Feed Origin & Routing Badges: Added visual badges identifying automated downloads (`Auto-Watch`, `Quick Fetch`) and custom destination subfolders on activity cards.

### Fixed
- Quick Fetch Results Display: Replaced raw monospace dump with structured per-link status feedback showing queued, skipped duplicate, and failed links.
- Quick Fetch Link Counter: Added real-time link counter and accepted format guidance pills.
### Fixed
- Real-Time Watch Rule Download Updates: Added continuous adaptive background polling (`pollProgress`) that checks `/download-progress` every 2.5 seconds when idle (5 seconds in background tab) and 1 second during active downloads, so incoming automated downloads triggered by Telegram watch rules appear on the UI in real time without requiring manual page refreshes.

## [4.1.1] - 2026-11-08
### Added
- Dynamic Folder Picker & Management: Replaced subfolder free-text inputs with dynamic dropdown pickers populated from `SAVE_PATH`, plus an interactive dialog to create new subfolders on the fly across Dashboard, Quick Fetch, and Watch Rules.
- Channel & File Type Dropdowns: Replaced free-text input fields for Watch Rule channels and file types with rich dropdowns, channel dialog selectors with custom handle fallback, and media preset filters (Videos, Audio, Documents, Archives, Photos).
- Comma-Separated Multi-Extension Filtering: Upgraded Telegram file listing to filter multiple extensions simultaneously.
- Folder Management API: Added `GET /api/folders` and `POST /api/folders` with path-traversal validation.

## [4.1.0] - 2026-11-08
### Added
- Auto-Watch & Auto-Download Rules: Background Telegram event listener (`events.NewMessage`) and rule engine matching incoming media by channel, file extensions, keywords/regex, and min/max file size with automatic queue ingestion and retroactive channel scans.
- Smart Subfolder Routing & Media Server Organization: Custom target subfolders and automatic categorization by channel (`@channel/...`) or media type (`videos/`, `audio/`, `documents/`, `photos/`) with strict canonical path traversal protection jailed inside `SAVE_PATH`.
- Media Server Webhook Notifications: Added `COMPLETION_WEBHOOK_URL` support to notify media servers (Jellyfin, Plex, Sonarr/Radarr, Ntfy) immediately upon file download completion.
- Direct Telegram Message Link / Quick Fetch: Direct paste modal accepting public (`t.me/channel/123`), private (`t.me/c/1234567890/456`), and `tg://resolve` message links with instant routing and queueing.

### Fixed
- Subfolder File Serving: Extended `/files/serve/{filepath:path}` to support downloading and opening files saved within subfolders while strictly enforcing canonical directory jail containment.
- Retry & Resumption Subfolder Preservation: Retrying cancelled, interrupted, or failed downloads now preserves `subfolder` and `organize_by` routing metadata instead of defaulting to root.
- Upfront Subfolder Validation: Validated target subfolder paths upfront at all API entry points (`/files/download-selected`, `/files/download-all`, `/files/download/{id}`, `/files/quick-fetch`, `/api/watch-rules`) with 400 rejection on path traversal attempts.
- Watch Service & Event Listener Coroutine: Fixed unawaited `is_connected()` check on service startup and hardened rule keyword matching against regex catastrophic backtracking (ReDoS).
- Quick Fetch Request Throttling: Enforced maximum cap of 100 links per quick fetch request.


## [4.0.1] - 2026-11-08
### Fixed
- Media Filter in Download All: Fixed document/photo condition in `download_all_files` so that video and audio media files are correctly queued and downloaded when filtering.
- Parallel Connection Pool: Resolved socket leakage in `_create_parallel_senders` on partial connection failure and preserved `_init_request.query` state.
- Worker Fallback: Added graceful fallback to single-connection `iter_download` if a parallel sender worker hits a CDN redirect or transient connection error mid-stream.
- Resumption Disk Throttling: Throttled metadata file writes to at most once per 2 seconds during active streaming, avoiding excessive disk writes on every chunk.
- Auth & Security: Added `HttpOnly` cookie authentication for seamless browser downloads on `/files/serve/`, and scoped `?token=` acceptance exclusively to file serving.
- Debug Endpoint Gating: Gated `/debug/*` endpoints behind `ENABLE_DEBUG_ROUTES` or debug log level in production.
- Phone Number Formatting: Fixed string formatting logic so leading `+` is never duplicated when provided in environment variables.
- Private Channel Support: Enabled private channels and supergroups without `@username` in the channel picker by capturing and resolving numeric peer IDs.
- Mobile UI Layout: Fixed toast width to fit cleanly on narrow mobile viewports (`min(400px, calc(100vw - 2rem))`).

## [4.0.0] - 2026-11-07
### Added
- Unified Activity Screen: Replaced fragmented tabs (Search/Queue) and active/history split with a single-screen feed showing Downloading, Queued, Partial, and Completed items with instant filter chips.
- Partial Download History & Direct Resume: Interrupted/cancelled downloads retain their exact partial progress and can be individually resumed or retried with one click; removed obstructive modal/banner popups.
- Top Bar Disk Widget: Added real-time disk space usage (free/total) and visual progress meter for the configured download directory in the top bar.
- Searchable Channel Combobox: Integrated a responsive, searchable dropdown combobox that smoothly handles large channel catalogs with search filtering, keyboard navigation, and mobile bottom-sheet styling.
- Lightweight Fluid Animations: GPU-accelerated progress bar animations (`scaleX` / `opacity`) with battery/CPU saving visibility pausing and `prefers-reduced-motion` compliance.

### Fixed
- True Download Resumption: Parallel downloader now tracks worker partition byte offsets in a sidecar `.meta` file and preserves staging data upon cancellation or error, allowing resumed downloads to continue directly from where they stopped instead of re-downloading from the beginning.
- UI Stability: Fixed progress bar flashing and missing fill caused by Tailwind color class mismatch and redundant row re-rendering on poll cycles; replaced with surgical DOM reconciliation and smooth CSS width transitions.

### Removed
- Removed unused `/logs` console page and associated logging endpoints from web UI.

## [2.0.0] - 2026-10-20
### Added
- Zero-merge download engine: sparse-file preallocation with parallel range workers (`os.pwrite`), eliminating multi-gigabyte merge phases.
- Security hardening: anti-spoofing client IP resolution, strict path jail for file serving, and safe reverse proxy subnet allowlisting.
- Incomplete staging (`.incomplete/`) with atomic rename promotion upon file completion.
- Configurable duplicate resolution strategies (`skip`, `rename`, `overwrite`).
- Automatic state corruption recovery from backups with backup file rotation.

### Fixed
- State Manager: Implemented atomic persistence with unique temp files, thread-safe snapshotting, and disk write throttling.
- State Manager: Fixed session progress counter for single-file downloads.
- State Manager: Added zombie active state reconciliation and immediate queue event notifications.

### Removed
- Removed debug console from the dashboard.

## [2.1.0] - 2026-11-07
### Added
- Download Engine: Free disk space pre-flight check (`MIN_FREE_DISK_SPACE_MB`, defaulting to 500MB margin) prior to download allocation.
- Docker: Added native container `HEALTHCHECK` directive probing `/api/health`.
- Docker: Multi-architecture image manifests for both `linux/amd64` and `linux/arm64`.

### Fixed
- Download Engine: Configure 512KB chunks and chunk limits in parallel downloader to enable Telethon's direct streaming (`_DirectDownloadIter`).
- Download Engine: Automatically deduct missing or non-media messages from total so progress counters reach 100%.
- Download Engine: Guard against cancellation re-queueing and handle `limit=None` in `download_all_files`.
- UI: Fixed `logs.html` redirect loop for clients connecting from `TRUSTED_SUBNETS`.
- Security: Added `frame-ancestors 'self'` and `X-Frame-Options: SAMEORIGIN` HTTP response headers, bundled texture assets locally, and cleaned CSP meta tags.

### Changed
- Dependencies: Upgraded Telethon to `1.45.0`.
- Dependencies: Migrated authentication from `passlib[bcrypt]` to modern native `bcrypt`, eliminating deprecated standard library `crypt` dependencies ahead of Python 3.13+.

## [2.0.1] - 2026-10-20
### Added
- Non-blocking state persistence: `save_state_async` offloads fsync to the worker thread pool, and a new thread-safe `schedule_progress_save` entry point keeps the event loop responsive during parallel MTProto chunk streaming.

### Fixed
- State Manager: Progress-callback disk flushes no longer spawn unbounded fire-and-forget tasks; redundant flushes within the 1.5s throttle window are debounced, and the async write lock now rebinds safely when the owning event loop is replaced (tests/reloads).

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
