# Setting Up GitHub & Sharing the Installer

## One-Time Setup (You Only)

### 1. Create a GitHub Account
Go to https://github.com and sign up for a free account.

### 2. Create a New Repository
1. Click the **+** icon → **New repository**
2. Name it: `work-package-sorter`
3. Set it to **Public**
4. Click **Create repository**

### 3. Install Git
Download from https://git-scm.com and install with default settings.

### 4. Push Your Files to GitHub
Open PowerShell in your `work-package-sorter-app` folder and run:

```powershell
git init
git add .
git commit -m "Initial release"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/work-package-sorter.git
git push -u origin main
```

Replace `YOUR_USERNAME` with your actual GitHub username.

### 5. Update installer.ps1
Open `installer.ps1` and update line 9:
```
$GITHUB_USER = "YOUR_GITHUB_USERNAME"   # ← put your real username here
```

Then push the change:
```powershell
git add installer.ps1
git commit -m "Update installer with GitHub username"
git push
```

---

## Sharing with Coworkers

Send them **just two files** from your repo:
- `installer.bat`
- `installer.ps1`

They double-click `installer.bat` and it handles everything:
- Installs Node.js (if not present)
- Installs Python (if not present)  
- Downloads the app from GitHub
- Builds the sorter engine
- Creates a Desktop shortcut

**They need no technical knowledge at all.**

---

## Updating the App

When you make changes:
```powershell
git add .
git commit -m "Describe what changed"
git push
```

Coworkers re-run `installer.bat` to get the latest version.
Their data and saved rules are not affected by updates.

---

## Notes
- The first install takes 3-5 minutes (downloads ~150MB total)
- Subsequent installs are faster if Node/Python are already present
- The installer runs silently — coworkers just wait for the Desktop shortcut to appear
- If anything fails, they'll see a clear error message
