
# Telefetchr (Telegram file downloader)

## Prerequisites

- Docker installed on your system
- Docker Compose installed (usually comes with Docker Desktop)
- Telegram API credentials (api_id and api_hash from https://my.telegram.org)

## Directory Structure



```
telefetchr/
├── Dockerfile
├── docker-compose.yml
├── .dockerignore
├── requirements.txt
├── launch.py                # FastAPI app entry point
├── config.yaml
├── view.html                # Modern web UI (Tailwind, FontAwesome, popup toasts)
├── script.js   # JavaScript for the web UI
├── downloads/               # Created automatically
└── sessions/                # Created automatically, stores session files
  ├── session.session
  ├── telegram_session.session
  └── telegram_session.session-journal
```


> **Note:** All static files (HTML, JS) are served from the project root at `/static` by FastAPI. You do not need a physical `static/` folder.

## Configuration File: `config.yaml`

The `config.yaml` file in the project root contains your Telegram API credentials and app settings. **This file is required for the app to run.** Use the config template to create the config.yaml.

**Template:**

```yaml
api_id: YOUR_API_ID
api_hash: YOUR_API_HASH
phone: YOUR_PHONE_NUMBER  # Without + sign, e.g., 33612345678
save_path: downloads # local path or network drive url
channel: ["channel1", "channel2"]
max_concurrent_downloads: 3
```

**Security:**
- `config.yaml` is listed in `.gitignore` and will not be committed to git.
- Never share this file or commit it to public repositories.

## Accessing the Application

Once the container is running, open your browser and navigate to:

```
http://localhost:8000
```

## Running Without Docker (Development)

You can run the FastAPI app directly for development:


```bash
pip install -r requirements.txt
python launch.py
```

Then open [http://localhost:8000](http://localhost:8000) in your browser.

## First Time Login

1. The application will prompt you to login
2. Click "Send Verification Code"
3. Enter the code you receive via Telegram
4. If you have 2FA enabled, enter your password
5. The session will be saved in the `sessions/` directory

## Persistent Data

- **Downloads**: All downloaded files are stored in save path in config
- **Sessions**: Telegram session files are stored in `./sessions/`
- Both directories are mounted as volumes, so data persists even if you restart or rebuild the container

## Network Paths for Downloads

If you want to download files to a network location:

### Linux/Mac:
```bash
# Mount network share first
sudo mount -t cifs //server/share /mnt/network -o username=user,password=pass

# Update docker-compose.yml to add volume:
volumes:
  - /mnt/network:/mnt/network
```

### Windows:
```powershell
# Map network drive first (e.g., Z:)
net use Z: \\server\share /user:username password

# Update docker-compose.yml:
volumes:
  - Z:/:/network
```

Then in the web interface, use `/mnt/network` (Linux/Mac) or `/network` (Windows) as the download path.

## Build and Run Instructions

### 1. Create config.yaml

Create a `config.yaml` file in the project root with your Telegram credentials:

```yaml
api_id: YOUR_API_ID
api_hash: "YOUR_API_HASH"
phone: YOUR_PHONE_NUMBER  # without + sign, e.g., 1234567890
save_path: "downloads"  # See below for path options
max_concurrent_downloads: 3
channel:
  - channel1
  - channel2
```
### Option 1: Using Docker Compose (Recommended)

**Understanding save_path with Docker:**

The `save_path` in your config.yaml determines where files are downloaded. You have three options:

**Option 1: Relative Path (Recommended for simplicity)**
```yaml
save_path: "downloads"
```
Then mount it in docker-compose.yml:
```yaml
volumes:
  - ./downloads:/app/downloads
```

**Option 2: Absolute Path (Network Drive)**
```yaml
save_path: "/mnt/nas/downloads"
```
Then mount the EXACT same path in docker-compose.yml:
```yaml
volumes:
  - /mnt/nas/downloads:/mnt/nas/downloads
```

**Option 3: Absolute Path with Mapping**
```yaml
save_path: "/data/downloads"
```
Map your network drive to this path:
```yaml
volumes:
  - /mnt/your-network-drive:/data/downloads
```

### 2. Configure Volume Mounts

The volume mounts in `docker-compose.yml` MUST match the `save_path` in your config.yaml.

**If you're using a network drive path like `/mnt/nas/downloads`:**

1. First, ensure the network drive is mounted on your host system
2. Set `save_path: "/mnt/nas/downloads"` in config.yaml
3. Mount it in docker-compose.yml:
```yaml
volumes:
  - /mnt/nas/downloads:/mnt/nas/downloads  # Same path on both sides
```

**If you're using a relative path like `downloads`:**

1. Set `save_path: "downloads"` in config.yaml
2. Mount it in docker-compose.yml:
```yaml
volumes:
  - ./downloads:/app/downloads  # Local directory to container
```


**Example docker-compose.yml configurations:**

For network drive (absolute path):
```yaml
volumes:
  - ./sessions:/app/sessions
  - ./config.yaml:/app/config.yaml:ro
  - /mnt/nas/telegram:/mnt/nas/telegram  # Matches save_path in config
```

For local directory (relative path):
```yaml
volumes:
  - ./sessions:/app/sessions
  - ./config.yaml:/app/config.yaml:ro
  - ./downloads:/app/downloads  # Matches save_path: "downloads" in config
```

**Examples for different systems:**

**Linux/macOS (NFS/SMB mount):**
```yaml
- /mnt/nas/downloads:/mnt/nas/downloads
```
Config.yaml: `save_path: "/mnt/nas/downloads"`

**Windows (mapped network drive):**
```yaml
- Z:/downloads:/app/Z/downloads
```
Config.yaml: `save_path: "/app/Z/downloads"`

**Windows (UNC path):**
```yaml
- //server/share/downloads://server/share/downloads
```
Config.yaml: `save_path: "//server/share/downloads"`

### 3. Ensure Required Files Exist

Make sure you have:
- `launch.py` - Your FastAPI application
- `view.html` - The web interface

## Installation & Usage

### Starting the Application

1. **Build and start the container:**
   ```bash
   docker-compose up -d
   ```
   
   The `-d` flag runs the container in detached mode (background).

2. **Check if the container is running:**
   ```bash
   docker-compose ps
   ```

3. **Access the application:**
   Open your browser and navigate to:
   ```
   http://localhost:8000
   ```
### Option 2: Using Docker directly

```bash
# Build the image
docker build -t telefetchr .

# Run the container
docker run -d \
  --name telefetchr \
  -p 8000:8000 \
  -v $(pwd)/downloads:/app/downloads \
  -v $(pwd)/sessions:/app/sessions \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  telefetchr

# Run the container (windows)
docker run -d `
  --name telefetchr `
  -p 8000:8000 `
  -v ${PWD}/downloads:/app/downloads `
  -v ${PWD}/sessions:/app/sessions `
  -v ${PWD}/config.yaml:/app/config.yaml:ro `
  telefetchr


# View logs
docker logs -f telefetchr

# Stop the container
docker stop telefetchr
docker rm telefetchr
```

## Common Commands

### View Logs

**Follow logs in real-time:**
```bash
docker-compose logs -f
```

**View logs for specific service:**
```bash
docker-compose logs -f telefetchr
```

**View last 100 lines:**
```bash
docker-compose logs --tail=100
```

### Stop the Application

**Stop containers (keeps data):**
```bash
docker-compose stop
```

**Stop and remove containers:**
```bash
docker-compose down
```

### Restart the Application

```bash
docker-compose restart
```

### Rebuild After Code Changes

If you modify `launch.py` or other application files:

```bash
docker-compose up -d --build
```

### Access Container Shell

To access the container's shell for debugging:

```bash
docker-compose exec telefetchr /bin/bash
```

## Data Persistence

The following directories are mounted as volumes and will persist data:

- **Network Drive** (configured in docker-compose.yml) - All downloaded files go directly to your network storage
- **sessions/** - Telegram session data (keeps you logged in)
- **config.yaml** - Your configuration file (read-only)

Files are downloaded directly to your network drive location, so they're immediately available to other systems on your network.

## Troubleshooting

### Path Mismatch Issues

**Problem:** Files aren't appearing in your expected location.

**Solution:** Ensure your docker-compose.yml volume mount matches your config.yaml save_path:

1. Check your config.yaml:
```bash
cat config.yaml | grep save_path
```

2. If `save_path: "/mnt/nas/downloads"`, your docker-compose.yml needs:
```yaml
volumes:
  - /mnt/nas/downloads:/mnt/nas/downloads
```

3. If `save_path: "downloads"`, your docker-compose.yml needs:
```yaml
volumes:
  - ./downloads:/app/downloads
```

The paths must match exactly!

### Container Won't Start

**Check logs:**
```bash
docker-compose logs
```

**Common issues:**
- Missing `config.yaml` file
- Invalid API credentials
- Port 8000 already in use

### Port Already in Use

If port 8000 is already occupied, edit `docker-compose.yml`:

```yaml
ports:
  - "8080:8000"  # Change 8080 to any available port
```

Then access the app at `http://localhost:8080`

### Permission Issues

If you encounter permission issues with network drive access:

**For Linux/macOS NFS mounts:**
```bash
# Ensure the mount has correct permissions
sudo chmod 755 /mnt/your-network-drive
```

**For Docker on Linux with network storage:**
```bash
# Run container with specific user ID
docker-compose down
```

Then edit `docker-compose.yml` to add:
```yaml
user: "1000:1000"  # Replace with your user:group ID
```

**Check your user ID:**
```bash
id -u  # Gets your user ID
id -g  # Gets your group ID
```

### Reset Everything

To completely reset (warning: deletes all data):

```bash
docker-compose down -v
rm -rf downloads/* sessions/*
docker-compose up -d --build
```

## Advanced Configuration

### Mounting Network Drives

#### Linux - NFS Mount

First, mount your NFS share on the host:

```bash
# Install NFS client
sudo apt-get install nfs-common

# Create mount point
sudo mkdir -p /mnt/nas

# Mount NFS share
sudo mount -t nfs server.local:/share /mnt/nas

# Or add to /etc/fstab for automatic mounting:
echo "server.local:/share /mnt/nas nfs defaults 0 0" | sudo tee -a /etc/fstab
```

Then update docker-compose.yml:
```yaml
volumes:
  - /mnt/nas:/app/downloads
```

#### Linux - SMB/CIFS Mount

```bash
# Install CIFS utilities
sudo apt-get install cifs-utils

# Create credentials file
sudo nano /etc/.smbcredentials
# Add:
# username=your_username
# password=your_password

# Set permissions
sudo chmod 600 /etc/.smbcredentials

# Mount SMB share
sudo mount -t cifs //server/share /mnt/nas -o credentials=/etc/.smbcredentials

# Or add to /etc/fstab:
echo "//server/share /mnt/nas cifs credentials=/etc/.smbcredentials 0 0" | sudo tee -a /etc/fstab
```

#### Windows - Network Drive

On Windows with Docker Desktop, use the full path:

```yaml
volumes:
  - Z:/TelegramDownloads:/app/downloads  # If Z: is your mapped drive
```

Or use UNC path directly:
```yaml
volumes:
  - //192.168.1.100/share/downloads:/app/downloads
```

#### macOS - Network Drive

Mount your network drive first, then:

```yaml
volumes:
  - /Volumes/NetworkDrive/downloads:/app/downloads
```

### Custom Download Path

**Key Principle:** The path in your `config.yaml` must exist inside the Docker container.

**Example Setup:**

If your network drive is at `/mnt/synology/media` on your host:

1. **config.yaml:**
```yaml
save_path: "/mnt/synology/media"
```

2. **docker-compose.yml:**
```yaml
volumes:
  - /mnt/synology/media:/mnt/synology/media  # Mount with same path
```

**Alternative Setup (Path Mapping):**

If you want to use a different path inside the container:

1. **config.yaml:**
```yaml
save_path: "/storage"  # Path inside container
```

2. **docker-compose.yml:**
```yaml
volumes:
  - /mnt/synology/media:/storage  # Map host path to container path
```

**Multiple Network Locations:**

If you need to support multiple network drives:

**config.yaml:**
```yaml
save_path: "/mnt/nas1"  # Choose which one to use
```

**docker-compose.yml:**
```yaml
volumes:
  - /mnt/nas1:/mnt/nas1
  - /mnt/nas2:/mnt/nas2
  - /mnt/backup:/mnt/backup
  - ./sessions:/app/sessions
```

Then change `save_path` in config.yaml to switch between locations.

### Environment Variables

You can set additional environment variables in `docker-compose.yml`:

```yaml
environment:
  - PYTHONUNBUFFERED=1
  - LOG_LEVEL=INFO
```

### Resource Limits

To limit container resources, add to `docker-compose.yml`:

```yaml
services:
  telefetchr:
    # ... existing config ...
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 2G
        reservations:
          cpus: '1'
          memory: 512M
```

## Updating the Application

1. Pull the latest code changes
2. Rebuild and restart:
   ```bash
   docker-compose down
   docker-compose up -d --build
   ```

## Security Notes

- Keep your `config.yaml` secure and never commit it to version control
- The `sessions/` directory contains sensitive authentication data
- Use environment variables for sensitive data in production
- Consider using Docker secrets for production deployments

## Support

For issues and questions:
- Check the logs: `docker-compose logs -f`
- Ensure all prerequisites are installed
- Verify your Telegram API credentials
- Check that required files (`view.html`, `launch.py`) exist

## License

This project is licensed under the MIT License.