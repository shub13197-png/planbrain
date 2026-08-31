# Installing Planning Brain

**The installers are not code-signed yet.** Both Windows and macOS will show a
warning that sounds serious. This page tells you exactly what you will see and
what it does and does not mean, because a user who meets an unexplained security
dialog reasonably assumes the worst and stops.

## What the warnings actually mean

Code signing certificates cost money annually and identify the publisher. **They
do not inspect the software.** An unsigned installer is not "unsafe" and a signed
one is not "safe" — signing tells you who to blame, not whether the code is
sound.

We have not bought one yet. That is a real gap and is stated here rather than
worked around.

**How to verify what you downloaded**, which is more useful than either dialog:

```bash
sha256sum planbrain-0.1.0.msi        # Linux/macOS
certutil -hashfile planbrain-0.1.0.msi SHA256   # Windows
```

Compare against the checksum published with the release. That confirms the file
matches what CI built, which is the thing you actually want to know.

## Windows

Double-clicking the `.msi` shows:

> **Windows protected your PC**
> Microsoft Defender SmartScreen prevented an unrecognised app from starting.

The **Run anyway** button is hidden until you click **More info** first — which
is the step most people miss and the reason they conclude the download is broken.

1. Click **More info**
2. Click **Run anyway**

SmartScreen flags this because the installer is new and unsigned, so it has no
reputation. That reputation builds over downloads; a signed certificate starts it
higher.

**No firewall prompt.** The backend talks to the interface over a pipe, not a
network port. If Windows ever asks you to allow Planning Brain through the
firewall, something is wrong — please report it.

## macOS

Open the `.dmg` and drag the app to Applications. On first launch:

> **"Planning Brain" cannot be opened because the developer cannot be verified.**

**On macOS 15 (Sequoia) and later**, the old right-click → Open shortcut no
longer works. Use:

1. Try to open the app once and dismiss the warning
2. **System Settings → Privacy & Security**
3. Scroll to Security — there will be a line about Planning Brain being blocked
4. Click **Open Anyway**, then confirm

**On macOS 14 and earlier**, right-click the app → **Open** → **Open** also
works.

If the app is still refused, the download carried a quarantine attribute:

```bash
xattr -d com.apple.quarantine /Applications/Planning\ Brain.app
```

Only run that on a file whose checksum you have already compared.

## Apple Silicon and Intel

The macOS build targets **Apple Silicon (arm64)**. On an Intel Mac it will not
run. Intel builds are not produced yet — a limitation, not a decision, and
adding a second runner is straightforward when someone needs it.

## What it installs, and what it does not do

* **One application and one SQLite file.** No service, no daemon, no scheduled
  task, no browser extension.
* **No network access at any point.** Not during install, not at first launch,
  not ever. This is audited rather than asserted: see
  [`docs/offline.md`](offline.md). The backend blocks outbound sockets in-process
  at startup, and CI runs the packaged artifact in a container with no network
  interface at all.
* **No telemetry, no update check, no crash reporting.** There is nothing to
  opt out of, because there is nothing to opt into.
* **Your data stays in a file you can see.** Delete it and it is gone; copy it
  and you have a backup. There is no cloud copy because there is no cloud.

## Uninstalling

**Windows:** Settings → Apps → Planning Brain → Uninstall.
**macOS:** drag the app to the Trash.

Neither removes your planning database — that is deliberate, since deleting
someone's data because they removed an application is a poor surprise. It lives
at:

* Windows — `%LOCALAPPDATA%\PlanningBrain\`
* macOS — `~/Library/Application Support/PlanningBrain/`

Delete that folder to remove everything.

## Building it yourself

The installers are built by `.github/workflows/package.yml` on GitHub-hosted
runners, one per OS, with no cross-compilation. To build locally you need
Python 3.12+, Rust, and Node 20+:

```bash
pip install -e ".[dev]" pyinstaller
pyinstaller packaging/backend.spec --noconfirm --distpath build/dist --workpath build/work
python packaging/check_size.py build/dist/planbrain-backend

cd desktop && npm install && npx tauri build
```
