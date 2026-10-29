Output "docs/images/demo.gif"

Set FontSize 14
Set Width 1080
Set Height 640
Set Padding 0
Set BorderRadius 8
Set WindowBar Colorful
Set PlaybackSpeed 1.0

# Setup environment cleanly
Hide
Type "./scripts/setup_demo.sh > /dev/null 2>&1"
Enter
Type "alias espressomail='./scripts/run_espressomail.sh'"
Enter
Type "clear"
Enter
Show

# 1. Launch EspressoMail with rich demo database
Sleep 400ms
Type "espressomail --db /tmp/espressomail_demo.db"
Sleep 400ms
Enter

# 2. Animated startup splash screen (steaming espresso cup & stage progress)
Sleep 2200ms

# 3. Guided Onboarding Wizard (O) showing provider presets
Type "O"
Sleep 1600ms
Down
Sleep 600ms
Down
Sleep 600ms
Escape
Sleep 1000ms

# 4. Message Navigation: Calendar RSVP Invite & OpenPGP Verified Signatures
Down
Sleep 2200ms
Down
Sleep 1800ms
Up
Sleep 600ms
Up
Sleep 1400ms

# 5. TrueColor Theme Cycling (T)
Type "T"
Sleep 1200ms
Type "T"
Sleep 1200ms
Type "T"
Sleep 1200ms
Type "T"
Sleep 1200ms

# 6. Full-Text Search (FTS5 BM25 ranking & live snippets)
Type "/"
Sleep 1000ms
Type "roadmap"
Sleep 2200ms
Escape
Sleep 800ms

# 7. Command Palette (Ctrl+P)
Ctrl+P
Sleep 1000ms
Type "editor"
Sleep 1800ms
Escape
Sleep 800ms

# 8. Compose Modal & Markdown Editor (c)
Type "c"
Sleep 1800ms
Escape
Sleep 800ms

# 9. Clean Exit
Type "q"
Sleep 600ms
Type "y"
Sleep 600ms
