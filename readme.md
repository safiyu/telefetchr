# Telefetchr

A modern Telegram file downloader with a sleek Comic-themed UI.

## Screenshots

### Main Dashboard
![Main Dashboard](screenshots/main-dashboard.png)
*Clean interface with session info, search controls, and the "Previous Download Session" alert*

### File Search & Selection
![File List](screenshots/file-list.png)
*Browse and select files with the Comic-themed file list*

### Active Downloads
![Active Download](screenshots/active-download.png)
*Real-time progress tracking with toast notifications*

## Features

- **Modern Comic Theme UI** - Clean, playful design with black borders and vibrant colors
- **Bulk Downloads** - Download multiple files from Telegram channels simultaneously
- **Fast & Efficient** - Parallel downloads with configurable workers
- **Network Storage Support** - Direct downloads to NAS/network drives
- **Session Persistence** - Resume interrupted downloads automatically
- **Secure Authentication** - JWT-based auth with optional subnet bypass

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Telegram API credentials from [my.telegram.org](https://my.telegram.org)

### Installation

1. **Create `docker-compose.yml`:**

```yaml
services:
  telefetchr:
    image: safiyu/telefetchr:latest
    container_name: telefetchr
    ports:
      - "9868:9868"
    volumes:
      - ./sessions:/app/sessions
      - ./downloads:/app/downloads
    environment:
      - PYTHONUNBUFFERED=1
      - API_ID=123456                    # From my.telegram.org
      - API_HASH=your_api_hash           # From my.telegram.org
      - PHONE_NUMBER=1234567890          # Without + sign
      - SECRET_KEY=your_secret_key       # Generate: python -c "import secrets; print(secrets.token_urlsafe(32))"
      - ADMIN_USERNAME=admin
      - ADMIN_PASSWORD=yourpassword
      - ACCESS_TOKEN_EXPIRE_MINUTES=1440 # Optional, default: 1440 (24h)
      - TRUSTED_SUBNETS=192.168.1.0/24   # Optional, comma-separated CIDRs for auth bypass
      - PUID=1000                        # Optional
      - PGID=1000                        # Optional
    restart: unless-stopped
```

**Note:** If using with a VPN container, replace `ports:` section with:
```yaml
network_mode: "container:vpn"  # Replace 'vpn' with your VPN container name
```

2. **Start the application:**

```bash
docker-compose up -d
```

3. **Access the web interface:**

```
http://localhost:9868
```

4. **First-time login:**
   - Click "Send Verification Code"
   - Enter the code from Telegram
   - If 2FA is enabled, enter your password

## Network Storage Setup

### Linux/macOS (NFS/SMB)

Mount your network share first:

```bash
# NFS
sudo mount -t nfs server:/share /mnt/nas

# SMB/CIFS
sudo mount -t cifs //server/share /mnt/nas -o username=user,password=pass
```

Update `docker-compose.yml`:

```yaml
volumes:
  - /mnt/nas:/app/downloads
```

### Windows

Map network drive (e.g., Z:) then update `docker-compose.yml`:

```yaml
volumes:
  - Z:/downloads:/app/downloads
```

Or use UNC path directly:

```yaml
volumes:
  - //server/share/downloads:/app/downloads
```

## Common Commands

```bash
# View logs
docker-compose logs -f

# Restart
docker-compose restart

# Stop
docker-compose down

# Update to latest version
docker-compose pull
docker-compose up -d
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `API_ID` | ✅ | - | Telegram API ID from my.telegram.org |
| `API_HASH` | ✅ | - | Telegram API hash from my.telegram.org |
| `PHONE_NUMBER` | ✅ | - | Phone number without + sign |
| `SECRET_KEY` | ✅ | - | JWT secret key (generate with Python) |
| `ADMIN_USERNAME` | ✅ | - | Admin username for login |
| `ADMIN_PASSWORD` | ✅ | - | Admin password for login |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | ❌ | 1440 | JWT token expiration (minutes) |
| `TRUSTED_SUBNETS` | ❌ | - | Comma-separated CIDRs for auth bypass (e.g., `192.168.1.0/24`) |
| `PYTHONUNBUFFERED` | ❌ | 0 | Set to 1 for real-time logging |
| `PUID` | ❌ | 1000 | User ID for file permissions |
| `PGID` | ❌ | 1000 | Group ID for file permissions |
| `DOWNLOAD_WORKERS` | ❌ | 8 | Number of parallel workers per file |
| `DOWNLOAD_CHUNK_SIZE` | ❌ | 4194304 | Chunk size in bytes (default 4MB) |

## Performance Tuning

### Download Speed Optimizations

Telefetchr uses several techniques to maximize download speed:

1. **CDN Warmup** - Before spawning parallel workers, a small initial chunk is fetched to prime Telegram's CDN connection. This reduces the initial delay when starting downloads.

2. **Dynamic Worker Scaling** - Workers are automatically adjusted based on file size:
   - Files < 50MB: 2 workers
   - Files 50-200MB: 4 workers
   - Files > 200MB: Full worker count (default 8)

3. **Parallel Segmented Downloads** - Large files are split into segments downloaded simultaneously, then merged.

### Tuning Download Performance

Add these to your `docker-compose.yml` environment section:

```yaml
environment:
  # ... other variables ...
  - DOWNLOAD_WORKERS=8          # Increase for faster network, decrease for stability
  - DOWNLOAD_CHUNK_SIZE=4194304 # 4MB chunks (increase for faster connections)
```

**Tips:**
- On slow or unstable connections, reduce `DOWNLOAD_WORKERS` to 2-4
- On fast connections (>100Mbps), you can try increasing `DOWNLOAD_CHUNK_SIZE` to 8388608 (8MB)

## Troubleshooting

### Port Already in Use

Change the port in `docker-compose.yml`:

```yaml
ports:
  - "8080:9868"  # Use 8080 instead
```

### Permission Issues

Ensure correct PUID/PGID in docker-compose.yml:

```bash
# Check your user/group ID
id -u  # User ID
id -g  # Group ID
```

### Reset State

If downloads are stuck or corrupted:

1. Access the debug console in the web UI
2. Click "Cleanup State" or "Reset State"

Or manually reset:

```bash
docker-compose down
rm -rf sessions/* downloads/*
docker-compose up -d
```

## License

MIT License - Copyright (c) 2025 Safiyu

See [LICENSE](LICENSE) for full details.
