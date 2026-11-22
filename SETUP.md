# Setting up android-secretary

This file is written for **Claude Code** to follow, with you. Once Claude Code runs
on your phone (see [the README](README.md#claude-code-on-the-phone)), paste this
into it:

> Set up android-secretary on this phone for me, following
> https://github.com/j2kenton/android-secretary/blob/main/SETUP.md

Claude does the typing: it installs what's needed, checks every step, and asks
you about yourself to fill in your settings. You do what only you can do on the
phone: tap through Android's permission screens and answer its questions. It
takes about half an hour, most of it the questions. You can stop at any point
and carry on later ("carry on setting up android-secretary").

You can also follow it yourself: each step says what to run.

---

## For Claude: how to run this setup

The person may not be technical. Speak plainly, one step at a time, and say why
each step matters before asking them to do something. Keep each message short:
they're reading on a phone.

**Ground rules, for the whole setup and after it:**

- **Never change a phone setting yourself**, even where adb could. Say exactly
  where to tap (Android's menus differ a little between phones, so describe what
  to look for), and wait for them to say it's done. Then check it.
- **Nothing you set up may send, approve or delete anything.** That's the
  project's main promise: actions only put things in front of the owner.
- **Personal data goes only in `private.toml`, `rules.toml` and `var/`**, which
  git ignores. Never into a tracked file, a commit or an issue.
- **Ask before installing anything this file doesn't list.**
- Don't read their messages, notifications or contacts beyond what a step
  needs, and say when a step reads them.
- If a step fails, say so plainly, with the error, and try the fix given here
  before anything else.

Keep a short checklist of the steps in your replies, ticking them off, so they
can see where they are.

### Step 0. The risks, first

Before installing anything, tell them in a few short lines what they're
agreeing to, from the README's [Safety](README.md#safety) section: it reads
their messages, contacts and notifications; the text of what it judges is sent
to Claude; with Remote Control on, their Claude account becomes a key to the
phone, so it needs two-step verification; and it comes with no warranty
([Disclaimer](README.md#disclaimer)). Ask if they want to go ahead, and stop if
they don't.

### Step 1. Where you're running

Run `uname -r` and `echo $PREFIX`.

- Kernel release contains `PRoot`: you're in Ubuntu inside Termux (the usual
  setup). Termux's own commands run through Termux's tmux server, so check one
  exists: `ls /data/data/com.termux/files/usr/var/run/tmux-*/default`. If not,
  ask them to open a new Termux session (swipe in from the left edge of Termux,
  **New session**), type `tmux new -d -s main` and press Enter, then come back
  to this session.
- `$PREFIX` is `/data/data/com.termux/files/usr` and no `PRoot`: you're in
  Termux itself. Everything runs directly.
- Anything else (a computer, the cloud): stop. This must run on the phone.
  Tell them to start Claude Code on the phone first (README).

### Step 2. Get the code and run the setup script

The folder must be in Termux's home, because the daemon runs in Termux and
can't see Ubuntu's files:

```bash
cd /data/data/com.termux/files/home
git clone https://github.com/j2kenton/android-secretary
cd android-secretary
bin/setup
```

(If they already have it, `git pull` instead of cloning.) `bin/setup` installs
the Termux packages, makes `private.toml` and `rules.toml` from the examples
(never overwriting existing ones), installs the after-reboot and Share scripts,
and ends with `bin/automations doctor`. From Ubuntu it runs itself in Termux
and shows its output when it's done; the packages take a few minutes, so tell
them that.

### Step 3. Apps and permissions

Go through what `doctor` marked ✗, one at a time, then run
`bin/automations doctor` again. What they'll need:

- **Termux:API**, the app (the package alone isn't enough), from the **same
  place they got Termux** (F-Droid or GitHub; Google Play's versions don't mix
  with the others). Then:
  - notification access: Settings > Notifications > Device & app notifications
    (or "Notification access") > Termux:API > allow;
  - SMS and Contacts: Settings > Apps > Termux:API > Permissions. The first
    `doctor` run may also pop up Android's own permission dialogs: tell them
    to allow them.
- **Display over other apps** for Termux (Settings > Apps > Termux > Appear on
  top), so it can open the right app when they tap a card.
- **Battery**: Settings > Apps > Termux > Battery > Unrestricted, or Android
  may stop it in the background.
- Optional, **Termux:Boot** (same source as Termux), opened once, so it starts
  again after a reboot.

Once the needed lines are all ✓, the core works.

### Step 4. Make it theirs

Now ask about them and fill in `private.toml` (see `private.example.toml` for
every table, with comments) and `rules.toml`. Ask **a few questions at a
time**, explain what each answer is used for, and offer to skip anything.
Write only what they tell you; don't guess names or numbers.

1. **What they want.** Show the list of features from the README ("What it
   does") and ask which they want. For rules they don't want, set
   `enabled = false` in `rules.toml` (or delete them). Some are for one kind of
   user only (the `ensemble-*` rules are for the Ensemble VS Code extension;
   school rules are for parents): ask before keeping those.
2. **About them**, for Claude's prompts: `[owner] about`, one line (where they
   live roughly, languages, family, work). Tell them it's sent to Claude with
   the notifications it judges, so keep it general.
3. **Where they are, roughly**, for the weather and quiet times: a town, turned
   into `[location]` lat/lon rounded to two decimals, and the same numbers in
   the `weather = { lat, lon }` lines in `rules.toml`. Never a street address.
4. **The people closest to them**, whose urgent messages should be loud:
   `[urgent.people]`, keyed by the contact's name exactly as the phone shows it.
   `termux-contact-list` (Termux) gives the names: search it for the ones they
   mention rather than showing the whole list. A child's number for the
   screen-time rule goes in `[contacts]`.
5. **School and other groups**, if they have kids or groups they keep silent:
   `[school] groups` (part of each group name is enough), the kids' classes, and
   who school items get forwarded to. Explain that WhatsApp groups must be
   **unmuted, with notifications set to silent**: a muted group posts nothing
   to read.
6. **Quiet times** (`[quiet]`), **calendars** (`[calendars]`: the ids come from
   adb, step 5), **mailing lists**: only if they want those features.
7. **Words that must never be published** (`[public_check] terms`): their
   surname, their town, anything else private. This matters if they ever share
   their changes.

Then run `bin/automations validate` and `bin/automations doctor`, and show them
a short summary of what's set (names and features, not numbers).

### Step 5. Optional extras

Offer each, say what it adds, and skip any they don't want.

- **adb (Wireless debugging)**: lets it clear other apps' notifications once
  they're on a card, mark chats read, read the calendar and take a screenshot
  when they ask. In Ubuntu: `apt install -y adb`. On the phone: turn on
  Developer options (Settings > About phone > Software information > tap
  **Build number** seven times), then Developer options > **Wireless debugging**
  on > **Pair device with pairing code**. Using split screen, or the pairing
  notification, they read you the pairing port and code; run
  `adb pair 127.0.0.1:<pairing port> <code>`, then
  `adb connect 127.0.0.1:<port shown on the Wireless debugging screen>`, then
  `adb tcpip 5555` (a fixed port, so it reconnects by itself until the next
  reboot). Check with `bin/automations doctor`. After a reboot, Wireless
  debugging is off again (or on but not working: then off and on again fixes
  it): the boot script posts a card asking them to turn it on, then reconnects
  by itself (`bin/automations adb` does it by hand; the
  pairing survives reboots). Suggest the **Wireless debugging** Quick Settings
  tile (Developer options > Quick settings developer tiles) so that's one tap.
- **Morning email check**: the morning summary can include time-sensitive
  email. It needs the Gmail connector turned on in their claude.ai settings;
  then `email = true` in the `morning-summary` rule's action, in `rules.toml`.
  It can only search and read.
- **Backups** of their settings and decisions, encrypted, to a **private**
  GitHub repo they create. They make an [age](https://age-encryption.org) key
  themselves (`age-keygen -o key.txt`), keep `key.txt` in their password
  manager and delete it from the phone, and put the public key (`age1…`) in
  `backup.pub`. **Never show, copy or keep the secret key yourself.** Then
  `git remote add backup <url of the private repo>`; `doctor` checks that it's
  private.
- **Tasker + AutoNotification**, to mark chats read: see the README's "Mark as
  read" section.

### Step 6. Start it

```bash
bin/automations start
bin/automations fire morning-summary
```

The second line shows them a morning summary card right away. Tell them what
to expect over the next day (cards, the morning summary, what's cleared), and
that nothing is ever sent or deleted for them.

### Step 7. Using it from now on

Tell them they don't need to type commands or edit files from now on. They can
ask Claude Code for any change in plain words ("stop showing me the Galaxy
Store's update notices", "make messages from my sister loud too", "what did the
school group say this week?"), and Claude follows `CLAUDE.md` in this folder.

Offer **Remote Control**, so they can do that from anywhere, from the Claude
app on this phone or another device, or from claude.ai/code on a computer.
First make sure they understand that anyone who gets into their Claude account
could then use the phone through it, and ask them to turn on two-step
verification for that account if they haven't. Then: `/remote-control` for
this session, or `/config` > **Enable Remote Control for all sessions**. It
needs a claude.ai subscription (Pro, Max, Team or Enterprise), not an API key,
and the Claude Code session must stay running on the phone: `bin/claude-session`
keeps it in tmux, so it runs when Termux's window is closed. Offer to start it
after every reboot too (`bin/setup --claude-at-boot`), and suggest
`alias cc=<this folder>/bin/claude-session` in Termux's `~/.bashrc`. Have them
open it once with `bin/claude-session` and answer "trust this folder?" so a
reboot doesn't leave it waiting on that question. Tell them to keep
permission prompts on.
