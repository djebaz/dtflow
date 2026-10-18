# Setup VcXsrv X11 server for WSL DTFlow GUI
# Configures cookie authentication, starts VcXsrv, and validates GUI launch
param(
    [switch]$NoTest,
    [int]$TestDurationSeconds = 5
)

$ErrorActionPreference = 'Stop'

function Write-Step {
    param([string]$Message)
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Write-Success {
    param([string]$Message)
    Write-Host "    $Message" -ForegroundColor Green
}

function Write-Failure {
    param([string]$Message)
    Write-Host "    $Message" -ForegroundColor Red
}

# Check prerequisites
Write-Step "Checking prerequisites..."

$vcxsrvPath = 'C:\Program Files\VcXsrv\vcxsrv.exe'
if (-not (Test-Path $vcxsrvPath)) {
    Write-Failure "VcXsrv not found at: $vcxsrvPath"
    Write-Host "Install VcXsrv from https://sourceforge.net/projects/vcxsrv/" -ForegroundColor Yellow
    exit 1
}
Write-Success "VcXsrv found"

$wslDistro = 'Ubuntu-24.04'
$wslPython = '/home/USER/venv-dtflow/bin/python'

try {
    wsl.exe -d $wslDistro -e test -f $wslPython
    if ($LASTEXITCODE -ne 0) { throw }
    Write-Success "WSL Python found: $wslPython"
} catch {
    Write-Failure "WSL Python not found: $wslPython"
    Write-Host "Create the venv first with: wsl -d $wslDistro -- bash -c 'python3 -m venv ~/venv-dtflow'" -ForegroundColor Yellow
    exit 1
}

# Stop existing VcXsrv
Write-Step "Stopping existing VcXsrv instances..."
$existing = Get-Process vcxsrv -ErrorAction SilentlyContinue
if ($existing) {
    $existing | Stop-Process -Force
    Start-Sleep -Seconds 1
    Write-Success "Stopped $($existing.Count) instance(s)"
} else {
    Write-Success "No existing instances"
}

# Create authentication files
Write-Step "Creating X11 authentication files..."

$pythonScript = @'
import os, pathlib, secrets, struct
base = pathlib.Path('/mnt/c/Users/USER/AppData/Local/DTFlow-X11')
base.mkdir(parents=True, exist_ok=True)
client = pathlib.Path.home() / '.config/dtflow-x11'
client.mkdir(parents=True, exist_ok=True)

def field(b): return struct.pack('!H', len(b)) + b

record = struct.pack('!H', 65535) + field(b'') + field(b'0') + field(b'MIT-MAGIC-COOKIE-1') + field(secrets.token_bytes(16))
(base / 'Xauthority').write_bytes(record)
p = client / 'Xauthority'
p.write_bytes(record)
p.chmod(0o600)

env_content = '''export DISPLAY=127.0.0.1:0.0
export XAUTHORITY="$HOME/.config/dtflow-x11/Xauthority"
export QT_QPA_PLATFORM=xcb
'''
(client / 'env.sh').write_text(env_content, encoding='utf-8')
print('Auth files created')
'@

try {
    $pythonScript | wsl.exe -d $wslDistro -- $wslPython -
    if ($LASTEXITCODE -ne 0) { throw "Python script failed" }
    Write-Success "Authentication files created"
} catch {
    Write-Failure "Failed to create auth files: $_"
    exit 1
}

# Start VcXsrv
Write-Step "Starting VcXsrv with authentication..."

$vcxsrvArgs = @(
    ':0'
    '-multiwindow'
    '-nowgl'
    '-noreset'
    '-listen'
    'tcp'
    '-auth'
    'C:\Users\USER\AppData\Local\DTFlow-X11\Xauthority'
    '-logfile'
    'C:\Users\USER\AppData\Local\DTFlow-X11\server.log'
)

try {
    $vcxsrvProcess = Start-Process -FilePath $vcxsrvPath -ArgumentList $vcxsrvArgs -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 3
    $vcxsrvProcess.Refresh()
    
    if ($vcxsrvProcess.HasExited) {
        Write-Failure "VcXsrv exited with code: $($vcxsrvProcess.ExitCode)"
        if (Test-Path 'C:\Users\USER\AppData\Local\DTFlow-X11\server.log') {
            Write-Host "`nServer log:" -ForegroundColor Yellow
            Get-Content 'C:\Users\USER\AppData\Local\DTFlow-X11\server.log' -Tail 20
        }
        exit 1
    }
    
    Write-Success "VcXsrv running (PID: $($vcxsrvProcess.Id))"
} catch {
    Write-Failure "Failed to start VcXsrv: $_"
    exit 1
}

# Test GUI launch
if (-not $NoTest) {
    Write-Step "Testing DTFlow GUI launch..."
    
    $launchScript = @'
cd /mnt/c/Users/USER/Documents/GitHub/dtflow
source ~/.config/dtflow-x11/env.sh
source ~/venv-dtflow/bin/activate
dtflow gui &
DTFLOW_PID=$!
echo "GUI_PID:$DTFLOW_PID"
'@
    
    try {
        $output = $launchScript | wsl.exe -d $wslDistro -- bash
        $dtflowPid = $null
        
        foreach ($line in $output) {
            if ($line -match 'GUI_PID:(\d+)') {
                $dtflowPid = $matches[1]
                break
            }
        }
        
        if (-not $dtflowPid) {
            Write-Failure "Failed to launch DTFlow GUI"
            exit 1
        }
        
        Write-Success "DTFlow GUI launched (WSL PID: $dtflowPid)"
        Write-Host "    Waiting $TestDurationSeconds seconds for GUI to initialize..." -ForegroundColor Gray
        Start-Sleep -Seconds $TestDurationSeconds
        
        # Check if dtflow is still running
        $stillRunning = wsl.exe -d $wslDistro -- bash -c "ps -p $dtflowPid > /dev/null 2>&1; echo `$?"
        
        if ($stillRunning -eq '0') {
            Write-Success "GUI is running successfully!"
            
            # Stop the test GUI
            Write-Host "    Stopping test GUI..." -ForegroundColor Gray
            wsl.exe -d $wslDistro -- bash -c "kill $dtflowPid 2>/dev/null"
            Start-Sleep -Seconds 1
            Write-Success "Test complete"
        } else {
            Write-Failure "GUI process exited unexpectedly"
            Write-Host "Check for errors in the WSL terminal" -ForegroundColor Yellow
            exit 1
        }
        
    } catch {
        Write-Failure "GUI test failed: $_"
        exit 1
    }
}

# Summary
Write-Host "`n" -NoNewline
Write-Host "========================================" -ForegroundColor Green
Write-Host "VcXsrv X11 setup complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "`nTo launch DTFlow GUI from WSL, run:" -ForegroundColor Cyan
Write-Host "  source ~/.config/dtflow-x11/env.sh" -ForegroundColor White
Write-Host "  source ~/venv-dtflow/bin/activate" -ForegroundColor White
Write-Host "  cd /mnt/c/Users/USER/Documents/GitHub/dtflow" -ForegroundColor White
Write-Host "  dtflow gui" -ForegroundColor White
Write-Host "`nVcXsrv is running in the background (PID: $($vcxsrvProcess.Id))" -ForegroundColor Gray
