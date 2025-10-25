
# Telefetchr (Telegram file downloader)

## Screenshot
<img width="1910" height="918" alt="image" src="https://github.com/user-attachments/assets/deb5c8e9-e68c-40ff-87df-245dfa127d49" />


## Prerequisites

- Docker installed on your system
- Docker Compose installed (usually comes with Docker Desktop)
- Telegram API credentials (api_id and api_hash from https://my.telegram.org)

## Directory Structure

```
telefetchr/
├── Dockerfile         # Dockerfile for building the image
├── docker-compose.yml # Docker Compose configuration
├── .dockerignore # Ignore unnecessary files
├── requirements.txt # Python dependencies
├── launch.py                # FastAPI app entry point
├── view.html                # Modern web UI
├── script.js   # JavaScript for the web UI
├── downloads/               # Created automatically, stores downloaded files
└── sessions/                # Created automatically, stores session files
```

## Environment Variables (MANDATORY): 

```yaml
environment:
      - PYTHONUNBUFFERED=1
      - API_ID=12345 # your api_id from my.telegram.org
      - API_HASH=saasdasdf12324 # your api_hash from my.telegram.org
      - PHONE_NUMBER=12345 # without + sign
      - MAX_CONCURRENT_DOWNLOADS=3 # optional, default is 3
      # Generate secret: python -c "import secrets; print(secrets.token_urlsafe(32))"
      - SECRET_KEY=your_generated_secret_key_here
      - ADMIN_USERNAME=yourusername
      - ADMIN_PASSWORD=yourpassword
      - ACCESS_TOKEN_EXPIRE_MINUTES=1440  # 24 hours
```

## Accessing the Application

Once the container is running, open your browser and navigate to:

```
http://localhost:8000
```

## First Time Login

1. The application will prompt you to login
2. Click "Send Verification Code"
3. Enter the code you receive via Telegram
4. If you have 2FA enabled, enter your password
5. The session will be saved in the `sessions/` directory

## Persistent Data

- **Downloads**: All downloaded files are stored in this path 
- **Sessions**: Telegram session files are stored in this path
- Both directories are mounted as volumes, so data persists even if you restart or rebuild the container

## Network Paths for Downloads

If you want to download files to a network location:

### Linux/Mac:
```bash
# Mount network share first
sudo mount -t cifs //server/share /mnt/network -o username=user,password=pass

# Update docker-compose.yml to add volume:
volumes:
  - /mnt/network:/app/downloads
```

### Windows:
```powershell
# Map network drive first (e.g., Z:)
net use Z: \\server\share /user:username password

# Update docker-compose.yml:
volumes:
  - Z:/:/app/downloads
```

## Build and Run Instructions

### Option 1: Using Docker Compose (Recommended)
```yaml
services:
  telefetchr:
    image: safiyu/telefetchr:latest
    container_name: telefetchr
    ports:
      - "8000:8000"
    volumes:
      - /path/to/sessions:/app/sessions
      - /path/to/downloads:/app/downloads
    environment:
      - PYTHONUNBUFFERED=1
      - API_ID=123456 # your api_id from my.telegram.org
      - API_HASH=abcddfabc123456 # your api_hash from my.telegram.org
      - PHONE_NUMBER=33334567890 # without + sign
      - MAX_CONCURRENT_DOWNLOADS=3
    restart: always
```

**Linux/macOS (NFS/SMB mount):**
```yaml
- /mnt/nas/downloads:/app/downloads
```

**Windows (mapped network drive):**
```yaml
- Z:/downloads:/app/downloads
```

**Windows (UNC path):**
```yaml
- //server/share/downloads:/app/downloads
```

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
  -e API_ID=123456 \
  -e API_HASH=abcdef123456 \
  -e PHONE_NUMBER=1234567890 \
  -e MAX_CONCURRENT_DOWNLOADS=3 \
  telefetchr

# Run the container (windows)
docker run -d `
  --name telefetchr `
  -p 8000:8000 `
  -v ${PWD}/downloads:/app/downloads `
  -v ${PWD}/sessions:/app/sessions `
  -v ${PWD}/config.yaml:/app/config.yaml:ro `
  -e API_ID=123456 `
  -e API_HASH=abcdef123456 `
  -e PHONE_NUMBER=1234567890 `
  -e MAX_CONCURRENT_DOWNLOADS=3 `
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

Files are downloaded directly to your network drive location, so they're immediately available to other systems on your network.

## Troubleshooting

### Container Won't Start

**Check logs:**
```bash
docker-compose logs
```

**Common issues:**
- Invalid API credentials
- Port 8000 already in use
- Permission issues with network drive
- Path not set correctly

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

## Debug Section Fields Explained**:

- **memory_state**: Current state in application memory
  - `active`: Whether a download is currently in progress
  - `progress`: Number of files completed
  - `total`: Total files in the session
  - `session_id`: Unique identifier for the download session
  - `channel`: Telegram channel being downloaded from
  - `completed_count`: Number of files successfully downloaded
  - `concurrent_count`: Number of files currently downloading
  - `cancelled`: Whether the download was cancelled

- **file_state**: Information about the persisted state file
  - `exists`: Whether the state file exists on disk
  - `size_bytes`: Size of the state file
  - `path`: Location of the state file
  - `content`: First 500 characters of the file content

- **completed_downloads**: Dictionary of all completed downloads
  - Key format: `file_INDEX_MESSAGEID` or `single_MESSAGEID`
  - Contains filename, size, and completion timestamp

- **active_tasks**: List of currently running background tasks

**Indicators of health**:
- ✅ `active: false` when no download running
- ✅ `progress` equals `total` (all files downloaded)
- ✅ `completed_count` equals `total`
- ✅ `concurrent_count: 0` when not downloading
- ✅ `channel` is set to a valid channel name
- ✅ `session_id` exists

**Indicators of problems**:
- ❌ `channel: null` but `session_id` exists
- ❌ `concurrent_count > 0` but `active: false`
- ❌ `total: 0` but `session_id` exists
- ❌ `started_at: null` but files were downloaded

**Solution**: Run cleanup state

### Cleanup state`

**Purpose**: Clean up corrupted or incomplete state data

**What it does**:
1. Creates a backup of the current state file
2. Removes orphaned `concurrent_downloads` (when not active)
3. Resets progress fields when no active download
4. Fully resets if no valid session data exists

**Use Case**: Fix state corruption after interrupted downloads or crashes

### Reset state

**Purpose**: Completely reset all download state (⚠️ USE WITH CAUTION)

**What it does**:
1. Creates a backup of the current state file
2. Clears all state data
3. Resets to initial empty state

**Use Case**: Start fresh when state is severely corrupted

**Response**:
```json
{
  "status": "success",
  "message": "State completely reset. Backup saved."
}
```

**⚠️ Warning**: This will delete all download history and progress. Cannot be undone except by restoring from backup.


## Security Notes

- The `sessions/` directory contains sensitive authentication data
- Use environment variables for sensitive data in production
- Consider using Docker secrets for production deployments


## License

This project is licensed under the MIT License.
