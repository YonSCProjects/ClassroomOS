# ClassroomOS — Setup Guide

> Step-by-step deployment guide for the teacher/operator.  
> Covers initial setup, per-machine deployment, and daily operations.

---

## Prerequisites

Before you begin, make sure you have:

1. **Python 3.10 or newer** installed on ALL machines (admin + students)
   - Download from https://python.org/downloads
   - ✅ Check "Add Python to PATH" during installation
2. **All machines on the same LAN** (e.g., 192.168.1.x)
3. **Administrator access** on all student PCs (needed once, for agent installation)
4. **The IP address** of your admin machine (e.g., `192.168.1.10`)
5. **MAC addresses** of all student PCs (for Wake-on-LAN)
   - Find MAC: open CMD on each PC and run `getmac /v`

---

## Step 1: Set Up the Admin Machine

### 1.1 Download the Project

Copy the entire `ClassroomOS` folder to a convenient location on your machine (e.g., `D:\ClassroomOS`).

### 1.2 Install Dependencies

Open a terminal (PowerShell or CMD) and run:

```bash
cd D:\ClassroomOS
pip install -r requirements.txt
```

This installs: `bcrypt`, `Pillow`, `psutil`, `pywin32`, `pyautogui`, `mss`, `customtkinter`.

### 1.3 Configure the Client List

Edit `console/data/clients.json` with your actual machine information:

```json
{
  "clients": [
    {
      "name": "PC-01",
      "ip": "192.168.1.101",
      "mac": "AA:BB:CC:DD:EE:01",
      "description": "Front row, left"
    },
    {
      "name": "PC-02",
      "ip": "192.168.1.102",
      "mac": "AA:BB:CC:DD:EE:02",
      "description": "Front row, right"
    }
  ]
}
```

**Important**:
- `name`: Any friendly name (shown in the dashboard)
- `ip`: Static IP — these should NOT change. Configure static IPs on your router or set them on each PC.
- `mac`: Required for Wake-on-LAN. Format: `XX:XX:XX:XX:XX:XX`
- `description`: Optional — for your reference only

### 1.4 First Run

```bash
cd D:\ClassroomOS
python console/main.py
```

On first launch:
1. You'll see a "First-Time Setup" dialog
2. Choose a strong admin password (minimum 6 characters)
3. Confirm the password
4. The system will generate a `shared_secret` and save it to `console/data/config.json`

**Copy the shared secret** — you'll need it for Step 2:
```bash
# Find it in:
cat console/data/config.json
# Look for: "shared_secret": "abc123def456..."
```

---

## Step 2: Install the Agent on Each Student PC

You need to do this once per machine. Subsequent boots will start the agent automatically.

### 2.1 Copy the Project Files

Copy the `ClassroomOS` folder to each student PC (USB drive, network share, etc.).

### 2.2 Run the Installer

On each student PC, open **PowerShell as Administrator** and run:

```powershell
cd C:\path\to\ClassroomOS\installer

.\install_agent.ps1 `
    -ConsoleIP "192.168.1.10" `
    -SharedSecret "abc123def456..."
```

Replace:
- `192.168.1.10` with your admin machine's IP
- `abc123def456...` with the shared secret from Step 1.4

### 2.3 What the Installer Does

1. Creates `C:\ClassroomAgent\` directory
2. Copies agent files + shared protocol
3. Installs Python dependencies (if not using .exe mode)
4. Registers the `ClassroomOSAgent` Windows Service (auto-start)
5. Configures the service to restart on failure
6. Opens port 9000 in Windows Firewall
7. Writes `config.json` with the shared secret

### 2.4 Verify Installation

On the student PC:
```powershell
# Check if the service is running
Get-Service ClassroomOSAgent

# Check if port 9000 is open
Test-NetConnection -ComputerName localhost -Port 9000
```

On the admin machine, the dashboard should show the machine as 🟢 online within 10 seconds.

---

## Step 3: Enable Wake-on-LAN (Optional but Recommended)

To use the "Start of Day" / "Wake All" feature, each machine's BIOS must have WOL enabled.

### 3.1 Enable in BIOS

1. Reboot the student PC
2. Enter BIOS/UEFI setup (usually F2, Del, or F12 during boot)
3. Find "Wake on LAN" / "Power On by PCI-E" / "Wake on Magic Packet"
4. Enable it
5. Save and exit

### 3.2 Enable in Windows

```powershell
# Run as Administrator on each student PC
Get-NetAdapter | ForEach-Object {
    Set-NetAdapterAdvancedProperty -Name $_.Name -DisplayName "Wake on Magic Packet" -DisplayValue "Enabled" -ErrorAction SilentlyContinue
}
```

### 3.3 Verify

On the admin console, go to sidebar → "🔍 Test WOL". Each online machine will report:
- ✅ = WOL enabled and working
- ⚠️ = WOL disabled in network adapter settings
- ❓ = Could not determine
- 🔴 = Machine offline

---

## Step 4: Daily Operations

### Start of Day
1. Launch the console: `python console/main.py`
2. Log in with your admin password
3. Click "🌅 Start of Day" to wake all machines
4. Wait 30-60 seconds for machines to boot

### During Class
- Monitor all screens via the thumbnail grid
- Double-click a card to open full remote control
- Use sidebar actions for bulk operations (lock, message, block internet, etc.)
- Use "📦 Backup Manager" to back up game server worlds or project folders

### End of Day
1. Click "🌙 End of Day" → all machines shut down after 60 seconds
2. Close the console

---

## Troubleshooting

### Machine shows as 🔴 offline

1. **Is the PC powered on?** Check physically.
2. **Is the agent service running?** On the student PC:
   ```powershell
   Get-Service ClassroomOSAgent | Select-Object Status
   ```
3. **Is port 9000 open?** On the student PC:
   ```powershell
   Test-NetConnection -ComputerName localhost -Port 9000
   ```
4. **Is the IP correct?** Check `clients.json` matches the actual IP:
   ```powershell
   ipconfig  # on the student PC
   ```
5. **Firewall blocking?** Re-run:
   ```powershell
   netsh advfirewall firewall add rule name="ClassroomOS Agent" dir=in action=allow protocol=TCP localport=9000
   ```

### Remote control shows "Machine offline"

This means the machine responded to ping but the screenshot command failed. Check:
- Is the machine at the login screen (no user logged in)? Screenshots may fail at the login screen.
- Is `mss` installed on the agent machine?

### WOL doesn't work

1. Machine must be completely off (not hibernate/sleep)
2. Machine must be connected via **Ethernet** (WOL over WiFi is unreliable)
3. WOL must be enabled in BIOS (see Step 3)
4. The MAC address in `clients.json` must be correct

### Console crashes on startup

Check `console/data/console.log` for error messages. Common issues:
- Missing Python packages → re-run `pip install -r requirements.txt`
- Corrupted `config.json` → delete it and re-run (will prompt for new password)

---

## Network Diagram

```
                    ┌─────────────────┐
                    │  Network Switch  │
                    └────────┬────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
   ┌────┴─────┐        ┌────┴─────┐        ┌────┴─────┐
   │ Admin PC │        │ PC-01    │        │ PC-12    │
   │ Console  │        │ Agent    │   ...  │ Agent    │
   │ .1.10    │        │ .1.101   │        │ .1.112   │
   │          │──TCP──►│ :9000    │        │ :9000    │
   │          │──UDP──►│ WOL      │        │ WOL      │
   └──────────┘        └──────────┘        └──────────┘
```

All traffic is local LAN only. No internet required.
