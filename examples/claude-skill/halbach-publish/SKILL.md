---
name: halbach-publish
description: Wrap up a project (or any notes) as a public-safe info page and send it to Halbach ConnectHub's inbox for the owner to review and publish. Use when the user says things like "wrap this up for Halbach", "send a project summary to Halbach", "push this to my site", or "make an info dump for the site". It only drops a submission in the inbox; it never publishes anything.
---

# Send a project summary to Halbach ConnectHub

Halbach ConnectHub is the owner's local control panel for their personal site. Submissions go into an **inbox**, appear in the window as a switched-off module marked "FROM A CHAT", and the owner reviews and publishes them. You never publish, deploy or switch anything on, and you never edit the hub's state.

## 1. Gather facts. Do not invent.

Read the project's own README, notes, timeline, code layout and recent results. If you are not working in the project folder, ask which project. Say "unverified" or "not tested" for anything you did not check. Do not claim a result you did not see.

## 2. Write one `index.md` in a fresh scratch folder

Plain Markdown. Suggested shape (drop sections that do not apply):

```
# <Project name>

One-paragraph summary: what it is and why it exists.

## Status
Where it is right now, and what is done vs not done.

## What it does / how it works
Short, concrete. A small table is fine.

## Results
Real numbers only, with what they were measured on.

## Next
What is planned.

## Links
Public links only.
```

Keep it readable in a minute or two (roughly 40 to 150 lines). Headings `#` to `######`, lists, tables, code blocks, bold/italic, links and small images (PNG or JPG, saved next to `index.md` and referenced by relative name) are supported. Raw HTML is shown as text, not rendered.

## 3. Public-safe rules (this will be on a public website)

Leave out, always:
- keys, tokens, passwords, API secrets, cookies, anything from `.env` files;
- personal email addresses, phone numbers, real names of private people, other people's private messages;
- absolute local paths, Windows usernames, machine names, private IP addresses, internal ports;
- anything the owner has not said is public (unreleased work, friends' projects, private servers' details);
- copyrighted material you do not have the right to share (game assets, long quotes).

If you are unsure whether something is safe, leave it out and tell the owner what you left out.

## 4. Submit

From the scratch folder's parent:

```
halbach submit "<folder>" --name "<Title>" --desc "<one line>"
```

If `halbach` is not on PATH, use the launcher in the Halbach repo instead, which needs no install:

```
"<path to the ConnectHub repo>\halbach-submit.bat" "<folder>" --name "<Title>" --desc "<one line>"
```

You can also pipe Markdown text: `halbach submit --stdin --name "<Title>"`.

The command prints where it put the submission. If it says "Not submitted", read the reason, fix it and try again. Private-looking files (dotfiles, `.env`, keys, logs) are dropped automatically; mention it if that happens.

## 5. Tell the owner, accurately

Say what you sent, that it is **in the inbox and switched off**, and that they need to open Halbach ConnectHub, review it (there is a preview), switch it on and press **Publish**. Do not say it is live. List anything you deliberately left out.
